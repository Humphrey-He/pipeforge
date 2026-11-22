"""R1 task kernel tests: Storage / TaskRepository / StateMachine / Checkpoint / QueueScanner.
All on tempdir LocalStorage — zero external deps, zero network."""
import copy
import json
import tempfile
from pathlib import Path

from pipeforge.core import (CheckpointManager, QueueScanner,
                            StageOrderError, StateMachine, TaskRepository)
from pipeforge.storage import LocalStorage
import pipeforge.protocol as P

TASK = json.loads((Path(__file__).parent.parent / "pipeforge" / "schemas" / "examples" / "task.example.json").read_text(encoding="utf-8"))


def _fresh():
    td = tempfile.TemporaryDirectory()
    st = LocalStorage(td.name)
    repo = TaskRepository(st)
    return td, st, repo


def _drop_task():
    t = copy.deepcopy(TASK)
    for k in t["stages"]:
        t["stages"][k] = {}
    return t


# ---------------- Storage ----------------

def test_storage_roundtrip_and_boundaries():
    td, st, _ = _fresh()
    st.write_text("a/b.txt", "hello")
    assert st.read_text("a/b.txt") == "hello"
    assert st.list_dir("a") == ["b.txt"]
    assert len(st.sha256("a/b.txt")) == 64
    try:
        st.resolve("../escape")
        raise AssertionError("should reject traversal")
    except ValueError:
        pass
    td.cleanup()


def test_storage_atomic_write_json():
    td, st, _ = _fresh()
    st.atomic_write_json("x/t.json", {"k": 1})
    assert st.read_json("x/t.json") == {"k": 1}
    assert not st.exists("x/t.json.tmp")
    td.cleanup()


# ---------------- TaskRepository ----------------

def test_repo_submit_get_update_list():
    td, st, repo = _fresh()
    t = _drop_task()
    repo.submit(t)
    got = repo.get(t["task_id"])
    assert got is not None and got["task_id"] == t["task_id"]
    assert len(repo.list(status="PENDING")) == 1
    updated = repo.update(t["task_id"], lambda x: P.transition(x, "RUNNING"))
    assert updated["status"] == "RUNNING"
    td.cleanup()


def test_repo_rejects_duplicate_and_invalid():
    td, st, repo = _fresh()
    t = _drop_task()
    repo.submit(t)
    try:
        repo.submit(t)
        raise AssertionError("dup rejected")
    except FileExistsError:
        pass
    bad = _drop_task()
    bad["task_id"] = "INVALID_ID"
    try:
        repo.submit(bad)
        raise AssertionError("invalid rejected")
    except ValueError:
        pass
    td.cleanup()


def test_repo_update_rejects_invalid_mutation():
    td, st, repo = _fresh()
    t = _drop_task()
    repo.submit(t)
    def break_it(x):
        x["priority"] = "not-a-number"
    try:
        repo.update(t["task_id"], break_it)
        raise AssertionError("invalid mutation rejected")
    except ValueError:
        pass
    td.cleanup()


# ---------------- StateMachine ----------------

def test_state_machine_transition_and_order():
    td, _, _ = _fresh()
    t = _drop_task()
    sm = StateMachine()
    try:
        sm.assert_stage_order(t, "uv")
        raise AssertionError("uv blocked before inference")
    except StageOrderError:
        pass
    for k in ("inference", "validate_raw", "clean", "validate_clean"):
        t["stages"][k] = {"status": "COMPLETED", "attempt": 1, "checkpoint": None, "error": None}
    sm.assert_stage_order(t, "uv")  # now passes
    td.cleanup()


def test_state_machine_invalidate_resets_downstream():
    td, _, _ = _fresh()
    t = _drop_task()
    for k in t["stages"]:
        t["stages"][k] = {"status": "COMPLETED", "attempt": 1, "checkpoint": None, "error": None}
    StateMachine.invalidate(t, "clean")
    assert t["stages"]["clean"] == {}
    assert t["stages"]["publish"] == {}
    assert t["stages"]["inference"]["status"] == "COMPLETED"
    td.cleanup()


# ---------------- CheckpointManager ----------------

def test_checkpoint_write_and_verify_roundtrip():
    td, st, repo = _fresh()
    t = _drop_task()
    repo.submit(t)
    st.write_text("work/in.glb", "raw-mesh")
    st.write_text("work/out.glb", "clean-mesh")
    cm = CheckpointManager(st)
    cm.write(t, "clean", "cleaner-1.0", input_rel="work/in.glb", output_rel="work/out.glb")
    assert cm.verifies(t, "clean", "cleaner-1.0", "work/in.glb", "work/out.glb")
    assert t["stages"]["clean"]["status"] == "COMPLETED"
    assert t["stages"]["clean"]["attempt"] == 1
    repo.update(t["task_id"], lambda x: None)  # persist back
    td.cleanup()


def test_checkpoint_detects_tamper_and_version_bump():
    td, st, _ = _fresh()
    t = _drop_task()
    st.write_text("work/in.glb", "raw-mesh")
    st.write_text("work/out.glb", "clean-mesh")
    cm = CheckpointManager(st)
    cm.write(t, "clean", "cleaner-1.0", input_rel="work/in.glb", output_rel="work/out.glb")
    assert not cm.verifies(t, "clean", "cleaner-2.0", "work/in.glb", "work/out.glb")
    st.write_text("work/out.glb", "TAMPERED")
    assert not cm.verifies(t, "clean", "cleaner-1.0", "work/in.glb", "work/out.glb")
    td.cleanup()


def test_checkpoint_detects_config_change():
    td, st, _ = _fresh()
    t = _drop_task()
    st.write_text("work/in.glb", "raw")
    st.write_text("work/out.glb", "clean")
    cm = CheckpointManager(st)
    cm.write(t, "clean", "cleaner-1.0", input_rel="work/in.glb", output_rel="work/out.glb")
    assert cm.verifies(t, "clean", "cleaner-1.0", "work/in.glb", "work/out.glb")
    t["config"]["clean"]["profile"] = "character_high"
    assert not cm.verifies(t, "clean", "cleaner-1.0", "work/in.glb", "work/out.glb")
    td.cleanup()


# ---------------- QueueScanner ----------------

def _put_inbox(st, t, ready=True, ref_body="ref-body", id_mismatch=False, wrong_ref_sha=False):
    d = "inbox/" + t["task_id"]
    doc = copy.deepcopy(t)
    if id_mismatch:
        doc["task_id"] = "TASK_19990101_999"
    if wrong_ref_sha:
        for r in doc["source"]["reference"]:
            r["sha256"] = "0" * 64
    st.atomic_write_json(d + "/task.json", doc)
    st.write_text(d + "/reference/ref_001.png", ref_body)
    if ready:
        st.write_text(d + "/READY", json.dumps({"status": "READY"}))


def test_scanner_intake_happy_path():
    td, st, repo = _fresh()
    t = _drop_task()
    st.write_text("tmp_ref", t["source"]["reference"][0].get("sha256") and "ref-body" or "ref-body")
    sha = st.sha256("tmp_ref")
    for r in t["source"]["reference"]:
        r["sha256"] = sha
    _put_inbox(st, t)
    qs = QueueScanner(st, repo)
    rep = qs.intake_once()
    assert len(rep["claimed"]) == 1, rep
    assert rep["rejected"] == []
    assert repo.get(t["task_id"]) is not None
    assert st.exists("tasks/claimed/" + t["task_id"])
    assert not st.exists("inbox/" + t["task_id"])
    td.cleanup()


def test_scanner_rejects_missing_ready():
    td, st, repo = _fresh()
    t = _drop_task()
    _put_inbox(st, t, ready=False)
    qs = QueueScanner(st, repo)
    rep = qs.intake_once()
    assert rep["claimed"] == []
    assert st.exists("inbox/" + t["task_id"])
    td.cleanup()


def test_scanner_rejects_tampered_reference():
    td, st, repo = _fresh()
    t = _drop_task()
    _put_inbox(st, t, wrong_ref_sha=True, ref_body="tampered")
    qs = QueueScanner(st, repo)
    rep = qs.intake_once()
    assert rep["claimed"] == []
    assert len(rep["rejected"]) == 1
    assert "哈希不符" in rep["rejected"][0]["reason"]
    td.cleanup()


def test_scanner_rejects_id_mismatch():
    td, st, repo = _fresh()
    t = _drop_task()
    _put_inbox(st, t, id_mismatch=True)
    qs = QueueScanner(st, repo)
    rep = qs.intake_once()
    assert rep["claimed"] == [] and len(rep["rejected"]) == 1
    assert "task_id 不符" in rep["rejected"][0]["reason"]
    td.cleanup()


def test_scanner_reclaim_after_record_loss():
    td, st, repo = _fresh()
    t = _drop_task()
    st.write_text("tmp_ref", "ref-body")
    sha = st.sha256("tmp_ref")
    for r in t["source"]["reference"]:
        r["sha256"] = sha
    _put_inbox(st, t)
    qs = QueueScanner(st, repo)
    qs.intake_once()
    st.remove("tasks/records/%s.json" % t["task_id"])
    st.move("tasks/claimed/" + t["task_id"], "inbox/" + t["task_id"])
    rep2 = qs.intake_once()
    assert len(rep2["claimed"]) == 1
    assert repo.get(t["task_id"]) is not None
    td.cleanup()
