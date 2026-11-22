"""R3.5+ consistency review tests: four scenarios, ONE task contract.

Acceptance standard (per review): the SAME task under normal execution,
failure-retry, process interruption, and re-scan must show consistent
status / checkpoint / artifacts / execution counts.
"""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from pipeforge import (Artifact, BaseWorker, LocalStorage, Runner,
                       WorkerContext, WorkerResult)
from pipeforge.core import QueueScanner, TaskRepository
import pipeforge.protocol as P

TASK = json.loads((Path(__file__).parent.parent / "pipeforge" / "schemas" / "examples" / "task.example.json").read_text(encoding="utf-8"))


def _fresh():
    td = tempfile.TemporaryDirectory()
    st = LocalStorage(td.name)
    repo = TaskRepository(st)
    return td, st, repo


class CountingWorker(BaseWorker):
    name = "counting"
    processor_version = "1.0.0"

    def __init__(self, fails=0):
        self.fails = fails          # first N executions fail retryably
        self.executions = 0

    def validate_input(self, task, context):
        rel = context.rel_in_task(task["source"]["reference"][0]["path"])
        if not context.storage.exists(rel):
            raise ValueError("input missing")

    def execute(self, task, context):
        self.executions += 1
        if self.executions <= self.fails:
            return WorkerResult.fail("NETWORK_TIMEOUT", "flake #%d" % self.executions)
        rel = context.rel_in_task(task["source"]["reference"][0]["path"])
        out = context.rel_in_workspace("out.txt")
        context.storage.write_text(out, "content-v%d" % self.executions)
        sha = context.storage.sha256(out)
        return WorkerResult.ok("output", sha,
                               artifacts=[Artifact("output", out, sha)],
                               metrics={"exec": self.executions})

    def validate_output(self, task, result, context):
        for a in result.artifacts:
            if context.storage.sha256(a.path_rel) != a.sha256:
                raise ValueError("artifact hash mismatch")


def _mk_task(st, repo, submit=True):
    t = copy.deepcopy(TASK)
    for k in t["stages"]:
        t["stages"][k] = {}
    d = "tasks/claimed/%s" % t["task_id"]
    st.write_text(d + "/in.txt", "hello-consistency")
    sha = st.sha256(d + "/in.txt")
    for r in t["source"]["reference"]:
        r["path"] = "in.txt"; r["sha256"] = sha
        r["width"] = 8; r["height"] = 8
    st.atomic_write_json(d + "/task.json", t)
    if submit:
        repo.submit(copy.deepcopy(t))
    return t


def _contract(repo, tid, stage="clean"):
    """Snapshot the four consistency dimensions of a task."""
    t = repo.get(tid)
    slot = t["stages"][stage]
    ck = slot.get("checkpoint_data") or {}
    return {
        "task_status": t["status"],
        "stage_status": slot.get("status"),
        "attempt": slot.get("attempt", 0),
        "output_hash": ck.get("output_hash"),
        "output_path": ck.get("output_path"),
        "errors": len(t.get("errors", [])),
    }


def test_scenario_1_normal_execution_consistency():
    td, st, repo = _fresh()
    t = _mk_task(st, repo)
    w = CountingWorker()
    r = Runner(st, repo).run(t["task_id"], w, "clean")
    assert r.outcome == "COMPLETED"
    c = _contract(repo, t["task_id"])
    assert c["task_status"] == "RUNNING"          # not auto-completed
    assert c["stage_status"] == "COMPLETED"
    assert c["attempt"] == 1
    assert c["output_hash"] and len(c["output_hash"]) == 64
    # report artifacts == persisted checkpoint artifacts
    assert r.artifacts[0].sha256 == c["output_hash"]
    assert r.artifacts[0].path_rel in c["output_path"].replace(chr(92), "/")
    assert w.executions == 1
    td.cleanup()


def test_scenario_2_retry_consistency():
    td, st, repo = _fresh()
    t = _mk_task(st, repo)
    w = CountingWorker(fails=1)
    runner = Runner(st, repo)
    r1 = runner.run(t["task_id"], w, "clean")
    assert r1.outcome == "RETRY_WAIT"
    c1 = _contract(repo, t["task_id"])
    assert c1["task_status"] == "RETRY_WAIT"
    assert c1["stage_status"] == "RETRY_WAIT"
    assert c1["attempt"] == 1
    assert c1["errors"] == 1
    assert c1["output_hash"] is None              # no artifact from failed run
    r2 = runner.run(t["task_id"], w, "clean")
    assert r2.outcome == "COMPLETED"
    c2 = _contract(repo, t["task_id"])
    assert c2["task_status"] == "RUNNING"
    assert c2["attempt"] == 2                     # counted once per execution
    assert c2["errors"] == 1                      # history preserved
    assert c2["output_hash"] == r2.artifacts[0].sha256
    assert w.executions == 2
    td.cleanup()


def test_scenario_3_interrupt_resume_consistency():
    """Simulate a process kill AFTER checkpoint persist, BEFORE complete."""
    td, st, repo = _fresh()
    t = _mk_task(st, repo)
    w = CountingWorker()
    runner = Runner(st, repo)
    r1 = runner.run(t["task_id"], w, "clean")
    snap = _contract(repo, t["task_id"])
    # "restart": brand-new Runner/Worker instances, same storage
    w2 = CountingWorker()
    runner2 = Runner(st, repo)
    r2 = runner2.run(t["task_id"], w2, "clean")
    assert r2.outcome == "SKIPPED"
    assert r2.skipped is True
    assert w2.executions == 0                     # NOT re-executed
    assert r2.artifacts, "SKIPPED report must carry artifacts (review finding)"
    c3 = _contract(repo, t["task_id"])
    assert c3 == snap                             # resume changed nothing
    assert r2.artifacts[0].sha256 == snap["output_hash"]
    td.cleanup()


def test_scenario_4_rescan_no_duplicate_execution():
    """Re-scanning claimed/completed work must not re-execute anything."""
    td, st, repo = _fresh()
    t = _mk_task(st, repo, submit=True)
    w = CountingWorker()
    runner = Runner(st, repo)
    runner.run(t["task_id"], w, "clean")
    # move claimed dir back to inbox to simulate a rogue re-submission
    st.move("tasks/claimed/%s" % t["task_id"], "inbox/%s" % t["task_id"])
    qs = QueueScanner(st, repo)
    rep = qs.intake_once()
    assert rep["claimed"] == []                   # nothing re-claimed
    assert rep["incomplete"], rep                # re-submitted dir lacks READY -> visible
    assert "READY" in rep["incomplete"][0]["missing"]
    w2 = CountingWorker()
    r = runner.run(t["task_id"], w2, "clean")
    assert r.outcome == "SKIPPED" and w2.executions == 0
    td.cleanup()


def test_report_artifacts_consistency_crosscheck():
    """If persisted checkpoint disagrees with worker result, FAIL loudly."""
    td, st, repo = _fresh()
    t = _mk_task(st, repo)
    class LyingWorker(CountingWorker):
        def validate_output(self, task, result, context):
            pass  # skip contract check; Runner cross-check is under test
        def execute(self, task, context):
            res = super().execute(task, context)
            if res.artifacts:
                # point at a real file but with a fabricated hash
                bad = Artifact(res.artifacts[0].key,
                               res.artifacts[0].path_rel, "f" * 64)
                res.artifacts = [bad]
            return res
    r = Runner(st, repo).run(t["task_id"], LyingWorker(), "clean")
    assert r.outcome == "FAILED"
    got = repo.get(t["task_id"])
    assert got["errors"][-1]["code"] == "OUTPUT_INVALID"
    assert "mismatch" in got["errors"][-1]["message"]
    td.cleanup()


def test_save_atomicity_under_invalid_mutation():
    """save() must refuse to persist anything that violates the schema."""
    td, st, repo = _fresh()
    t = _mk_task(st, repo)
    good = repo.get(t["task_id"])
    t2 = copy.deepcopy(good)
    t2["priority"] = "not-a-number"
    try:
        repo.save(t2)
        raise AssertionError("invalid task must be refused")
    except ValueError:
        pass
    # disk still holds the good version
    assert repo.get(t["task_id"])["priority"] == good["priority"]
    td.cleanup()
