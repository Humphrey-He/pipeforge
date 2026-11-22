"""FileWorker tests: PF-04..PF-07 — execution, generic manifest,
publish protection, idempotency."""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from pipeforge import LocalStorage, WorkerContext
from pipeforge.core import TaskRepository
from file_worker import FileWorker, publish

TASK = json.loads((Path(__file__).parent.parent / "pipeforge" / "schemas" / "examples" / "task.example.json").read_text(encoding="utf-8"))


def _fresh():
    td = tempfile.TemporaryDirectory()
    st = LocalStorage(td.name)
    return td, st, TaskRepository(st)


def _task(st, body="hello pipeforge", wrong_sha=False):
    t = copy.deepcopy(TASK)
    for k in t["stages"]:
        t["stages"][k] = {}
    st.write_text("inbox/in.txt", body)
    sha = st.sha256("inbox/in.txt")
    for r in t["source"]["reference"]:
        r["path"] = "in.txt"
        r["sha256"] = ("0" * 64) if wrong_sha else sha
        r["width"] = 8; r["height"] = 8
    return t


def _run(w, st, t):
    ctx = WorkerContext(st, task_dir="inbox", workspace="tasks/work/" + t["task_id"])
    w.validate_input(t, ctx)
    res = w.execute(t, ctx)
    w.validate_output(t, res, ctx)
    return res, ctx


def test_fw_input_hash_mismatch_rejected():
    td, st, _ = _fresh()
    t = _task(st, wrong_sha=True)
    try:
        _run(FileWorker(), st, t)
        raise AssertionError("hash mismatch must raise")
    except ValueError as e:
        assert "hash mismatch" in str(e)
    td.cleanup()


def test_fw_execute_and_manifest():
    td, st, _ = _fresh()
    t = _task(st, body="upper me")
    res, ctx = _run(FileWorker(), st, t)
    assert res.success and res.output_hash
    m = st.read_json("tasks/work/%s/staging/manifest.json" % t["task_id"])
    assert m["schema_version"] == "1.0"
    assert m["artifacts"][0]["sha256"] == res.output_hash
    assert m["quality"]["status"] == "PASS"
    td.cleanup()


def test_fw_missing_input_rejected():
    td, st, _ = _fresh()
    t = _task(st)
    st.remove("inbox/in.txt")
    try:
        _run(FileWorker(), st, t)
        raise AssertionError("missing input must raise")
    except ValueError as e:
        assert "not found" in str(e)
    td.cleanup()


def test_fw_publish_then_no_overwrite():
    td, st, _ = _fresh()
    t = _task(st)
    res, ctx = _run(FileWorker(), st, t)
    lib = "library/%s_v001" % t["task_id"]
    marker = publish(st, t["task_id"], ctx.rel_in_workspace("staging"), lib)
    assert marker["status"] == "PUBLISHED"
    assert st.exists(lib + "/PUBLISHED") is True or st.exists(lib + "/PUBLISHED")
    # second publish to same target must be rejected; existing version intact
    before = st.read_json(lib + "/manifest.json")
    try:
        publish(st, t["task_id"], "tasks/work/x/staging", lib)
        raise AssertionError("double publish must raise")
    except FileExistsError:
        pass
    assert st.read_json(lib + "/manifest.json") == before
    td.cleanup()


def test_fw_staging_kept_on_target_exists():
    td, st, _ = _fresh()
    t = _task(st)
    res, ctx = _run(FileWorker(), st, t)
    lib = "library/%s_v001" % t["task_id"]
    st.mkdir(lib)  # pre-existing target
    try:
        publish(st, t["task_id"], ctx.rel_in_workspace("staging"), lib)
        raise AssertionError("must refuse occupied target")
    except FileExistsError:
        pass
    # staging content untouched — no damage to anything
    assert st.exists(ctx.rel_in_workspace("staging/output.txt"))
    td.cleanup()
