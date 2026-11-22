"""R3 Runner tests: lifecycle, idempotent skip, crash recovery,
failure classification, retry accounting, complete_task gating."""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from pipeforge import (BaseWorker, LocalStorage, Runner, WorkerContext,
                       WorkerResult, Artifact)
from pipeforge.core import TaskRepository
import pipeforge.protocol as P

TASK = json.loads((Path(__file__).parent.parent / "pipeforge" / "schemas" / "examples" / "task.example.json").read_text(encoding="utf-8"))


def _fresh():
    td = tempfile.TemporaryDirectory()
    st = LocalStorage(td.name)
    repo = TaskRepository(st)
    return td, st, repo


def _task(st, body="hello"):
    t = copy.deepcopy(TASK)
    for k in t["stages"]:
        t["stages"][k] = {}
    st.write_text("tasks/claimed/%s/task.json" % t["task_id"], json.dumps(t))
    st.write_text("tasks/claimed/%s/in.txt" % t["task_id"], body)
    sha = st.sha256("tasks/claimed/%s/in.txt" % t["task_id"])
    for r in t["source"]["reference"]:
        r["path"] = "in.txt"
        r["sha256"] = sha
        r["width"] = 8; r["height"] = 8
    st.atomic_write_json("tasks/claimed/%s/task.json" % t["task_id"], t)
    repo_task = copy.deepcopy(t)
    return t, repo_task


class EchoWorker(BaseWorker):
    """Deterministic echo worker with injectable faults."""
    name = "echo"
    processor_version = "1.0.0"

    def __init__(self, fail=None, crash=False, validate_out_fail=False):
        self.fail = fail          # ("CODE", "msg") -> result failure
        self.crash = crash        # raise RuntimeError in execute
        self.validate_out_fail = validate_out_fail
        self.executions = 0

    def validate_input(self, task, context):
        rel = context.rel_in_task(task["source"]["reference"][0]["path"])
        if not context.storage.exists(rel):
            raise ValueError("input missing")

    def execute(self, task, context):
        self.executions += 1
        if self.crash:
            raise RuntimeError("boom")
        if self.fail:
            code, msg = self.fail
            return WorkerResult.fail(code, msg)
        rel = context.rel_in_task(task["source"]["reference"][0]["path"])
        text = context.storage.read_text(rel)
        out = context.rel_in_workspace("out.txt")
        context.storage.write_text(out, text)
        sha = context.storage.sha256(out)
        return WorkerResult.ok("output", sha,
                               artifacts=[Artifact("output", out, sha)],
                               metrics={"len": len(text)})

    def validate_output(self, task, result, context):
        if self.validate_out_fail:
            raise ValueError("output contract violated")


def _setup(st):
    t, repo_t = _task(st)
    st2 = st
    return t, repo_t, st2


def test_runner_normal_lifecycle():
    td, st, repo = _fresh()
    t, _ = _task(st)
    repo.submit(copy.deepcopy(t))
    r = Runner(st, repo).run(t["task_id"], EchoWorker(), stage_key="clean")
    assert r.outcome == "COMPLETED", r
    got = repo.get(t["task_id"])
    assert got["stages"]["clean"]["status"] == "COMPLETED"
    assert got["stages"]["clean"]["checkpoint_data"]["processor_version"] == "1.0.0"
    assert got["status"] == "RUNNING"  # task not auto-completed
    td.cleanup()


def test_runner_idempotent_skip():
    td, st, repo = _fresh()
    t, _ = _task(st)
    repo.submit(copy.deepcopy(t))
    runner = Runner(st, repo)
    w = EchoWorker()
    r1 = runner.run(t["task_id"], w, stage_key="clean")
    r2 = runner.run(t["task_id"], w, stage_key="clean")
    assert r2.outcome == "SKIPPED" and r2.skipped
    assert w.executions == 1  # second run did NOT re-execute
    td.cleanup()


def test_runner_stale_checkpoint_invalidates_and_reruns():
    td, st, repo = _fresh()
    t, _ = _task(st)
    repo.submit(copy.deepcopy(t))
    runner = Runner(st, repo)
    w1 = EchoWorker()
    runner.run(t["task_id"], w1, stage_key="clean")
    # bump processor version -> checkpoint stale -> must re-execute
    class EchoV2(EchoWorker):
        processor_version = "2.0.0"
    w2 = EchoV2()
    r = runner.run(t["task_id"], w2, stage_key="clean")
    assert r.outcome == "COMPLETED" and w2.executions == 1
    got = repo.get(t["task_id"])
    assert got["stages"]["clean"]["checkpoint_data"]["processor_version"] == "2.0.0"
    td.cleanup()


def test_runner_rejects_bad_input_nonretryable():
    td, st, repo = _fresh()
    t, _ = _task(st)
    st.remove("tasks/claimed/%s/in.txt" % t["task_id"])
    repo.submit(copy.deepcopy(t))
    r = Runner(st, repo).run(t["task_id"], EchoWorker(), stage_key="clean")
    assert r.outcome == "FAILED"
    got = repo.get(t["task_id"])
    assert got["status"] == "FAILED"
    assert got["errors"][-1]["code"] == "INPUT_INVALID"
    assert got["errors"][-1]["retryable"] is False
    td.cleanup()


def test_runner_retryable_failure_counts_attempts():
    td, st, repo = _fresh()
    t, _ = _task(st)
    repo.submit(copy.deepcopy(t))
    runner = Runner(st, repo)
    w = EchoWorker(fail=("NETWORK_TIMEOUT", "flake"))
    r1 = runner.run(t["task_id"], w, stage_key="clean")
    assert r1.outcome == "RETRY_WAIT"
    got = repo.get(t["task_id"])
    assert got["status"] == "RETRY_WAIT"
    assert got["stages"]["clean"]["status"] == "RETRY_WAIT"
    assert got["retry"]["current_attempt"] == 1
    # retry with healthy worker -> completes
    r2 = runner.run(t["task_id"], EchoWorker(), stage_key="clean")
    assert r2.outcome == "COMPLETED"
    got2 = repo.get(t["task_id"])
    assert got2["stages"]["clean"]["attempt"] == 2
    td.cleanup()


def test_runner_exhausts_retries_then_fails():
    td, st, repo = _fresh()
    t, _ = _task(st)
    t["retry"]["max_attempts"] = 2
    repo.submit(copy.deepcopy(t))
    runner = Runner(st, repo)
    w = EchoWorker(fail=("NETWORK_TIMEOUT", "flake"))
    r1 = runner.run(t["task_id"], w, stage_key="clean")
    r2 = runner.run(t["task_id"], w, stage_key="clean")
    assert r1.outcome == "RETRY_WAIT" and r2.outcome == "FAILED"
    got = repo.get(t["task_id"])
    assert got["status"] == "FAILED"
    assert got["errors"][-1]["attempt"] == 2
    td.cleanup()


def test_runner_worker_crash_keeps_task_retryable():
    td, st, repo = _fresh()
    t, _ = _task(st)
    repo.submit(copy.deepcopy(t))
    runner = Runner(st, repo)
    r = runner.run(t["task_id"], EchoWorker(crash=True), stage_key="clean")
    assert r.outcome == "RETRY_WAIT"  # crash treated retryable
    got = repo.get(t["task_id"])
    assert got["errors"][-1]["retryable"] is True
    assert "worker raised" in got["errors"][-1]["message"]
    td.cleanup()


def test_runner_output_contract_violation_fails_hard():
    td, st, repo = _fresh()
    t, _ = _task(st)
    repo.submit(copy.deepcopy(t))
    r = Runner(st, repo).run(t["task_id"], EchoWorker(validate_out_fail=True), stage_key="clean")
    assert r.outcome == "FAILED"
    got = repo.get(t["task_id"])
    assert got["errors"][-1]["code"] == "OUTPUT_INVALID"
    assert got["errors"][-1]["retryable"] is False
    td.cleanup()


def test_runner_interrupt_then_resume():
    """Crash AFTER checkpoint persisted but BEFORE complete: next run skips."""
    td, st, repo = _fresh()
    t, _ = _task(st)
    repo.submit(copy.deepcopy(t))
    runner = Runner(st, repo)
    w = EchoWorker()
    r1 = runner.run(t["task_id"], w, stage_key="clean")
    # simulate interrupt: task stays RUNNING (already persisted); resume by re-run
    r2 = runner.run(t["task_id"], w, stage_key="clean")
    assert r2.outcome == "SKIPPED"
    assert w.executions == 1
    td.cleanup()


def test_runner_status_gate_rejects_terminal():
    td, st, repo = _fresh()
    t, _ = _task(st)
    t2 = copy.deepcopy(t)
    P.transition(t2, "RUNNING"); P.transition(t2, "COMPLETED")
    repo.submit(t2) if False else None
    # manually craft a COMPLETED record (submit only takes PENDING)
    st.atomic_write_json("tasks/records/%s.json" % t2["task_id"], t2)
    try:
        Runner(st, repo).run(t2["task_id"], EchoWorker(), stage_key="clean")
        raise AssertionError("must reject terminal task")
    except ValueError:
        pass
    td.cleanup()


def test_runner_complete_task_gating():
    td, st, repo = _fresh()
    t, _ = _task(st)
    repo.submit(copy.deepcopy(t))
    runner = Runner(st, repo)
    runner.run(t["task_id"], EchoWorker(), stage_key="clean")
    try:
        runner.complete_task(t["task_id"])
        raise AssertionError("must refuse: 9 stages empty")
    except ValueError as e:
        assert "never executed" in str(e)
    td.cleanup()


def test_runner_unmapped_error_code_tagged():
    td, st, repo = _fresh()
    t, _ = _task(st)
    repo.submit(copy.deepcopy(t))
    r = Runner(st, repo).run(t["task_id"], EchoWorker(fail=("WEIRD_CODE", "x")), stage_key="clean")
    assert r.outcome == "FAILED"
    got = repo.get(t["task_id"])
    e = got["errors"][-1]
    assert e["code"] == "OUTPUT_INVALID"
    assert "[unmapped" in e["message"]
    td.cleanup()
