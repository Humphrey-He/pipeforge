"""R4.1/R4.2 tests: stage graph validation, multi-stage execution order,
diamond dependencies, upstream artifact references."""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from pipeforge import (Artifact, BaseWorker, LocalStorage, Pipeline, Stage,
                       WorkerContext, WorkerResult)
from pipeforge.core import TaskRepository
from pipeforge.pipeline import validate_graph
import pipeforge.protocol as P

TASK = json.loads((Path(__file__).parent.parent / "pipeforge" / "schemas" / "examples" / "task.example.json").read_text(encoding="utf-8"))


class PassWorker(BaseWorker):
    """Writes a small artifact; optionally consumes an upstream reference."""
    name = "pass"
    processor_version = "1.0.0"

    def __init__(self, tag="x", needs_upstream=None):
        self.tag = tag
        self.needs_upstream = needs_upstream    # stage key this worker reads
        self.executions = 0

    def validate_input(self, task, context):
        if self.needs_upstream:
            ref = context.upstream(self.needs_upstream)   # raises KeyError if absent
            if not context.storage.exists(ref.path_rel):
                raise ValueError("upstream artifact missing on disk: %s" % ref.path_rel)

    def execute(self, task, context):
        self.executions += 1
        payload = self.tag
        if self.needs_upstream:
            ref = context.upstream(self.needs_upstream)
            payload += "+" + context.storage.read_text(ref.path_rel)
        out = context.rel_in_workspace("%s.out" % self.tag)
        context.storage.write_text(out, payload)
        sha = context.storage.sha256(out)
        return WorkerResult.ok(self.tag, sha,
                               artifacts=[Artifact(self.tag, out, sha)])

    def validate_output(self, task, result, context):
        for a in result.artifacts:
            if context.storage.sha256(a.path_rel) != a.sha256:
                raise ValueError("artifact hash mismatch")


def _fresh():
    td = tempfile.TemporaryDirectory()
    st = LocalStorage(td.name)
    repo = TaskRepository(st)
    return td, st, repo


def _task(st, repo):
    t = copy.deepcopy(TASK)
    for k in t["stages"]:
        t["stages"][k] = {}
    d = "tasks/claimed/%s" % t["task_id"]
    st.write_text(d + "/in.txt", "seed")
    sha = st.sha256(d + "/in.txt")
    for r in t["source"]["reference"]:
        r["path"] = "in.txt"; r["sha256"] = sha
        r["width"] = 8; r["height"] = 8
    st.atomic_write_json(d + "/task.json", t)
    repo.submit(copy.deepcopy(t))
    return t


# ---------------- graph validation (R4.1) ----------------

def test_graph_rejects_duplicate_keys():
    w = PassWorker("a")
    try:
        validate_graph([Stage("clean", w), Stage("clean", w)])
        raise AssertionError("dupes must raise")
    except ValueError as e:
        assert "duplicate" in str(e)


def test_graph_rejects_unknown_dep():
    w = PassWorker("a")
    try:
        validate_graph([Stage("clean", w, depends_on=("nope",))])
        raise AssertionError("unknown dep must raise")
    except ValueError as e:
        assert "unknown" in str(e)


def test_graph_rejects_cycle():
    w = PassWorker("a")
    try:
        validate_graph([Stage("clean", w, depends_on=("uv",)),
                        Stage("uv", w, depends_on=("clean",))])
        raise AssertionError("cycle must raise")
    except ValueError as e:
        assert "cycle" in str(e)


def test_graph_rejects_self_dependency():
    w = PassWorker("a")
    try:
        Stage("clean", w, depends_on=("clean",))
        raise AssertionError("self-dep must raise")
    except ValueError:
        pass


def test_graph_diamond_topological_order():
    w = PassWorker("a")
    order = validate_graph([
        Stage("inference", w),
        Stage("validate_raw", w, depends_on=("inference",)),
        Stage("clean", w, depends_on=("validate_raw",)),
        Stage("uv", w, depends_on=("validate_raw",)),
        Stage("bake", w, depends_on=("clean", "uv")),
    ])
    assert order[0] == "inference"
    assert order[1] == "validate_raw"
    assert set(order[2:4]) == {"clean", "uv"}
    assert order[-1] == "bake"


# ---------------- multi-stage execution (R4.2) ----------------

def test_pipeline_linear_execution_order():
    td, st, repo = _fresh()
    t = _task(st, repo)
    w1, w2, w3 = PassWorker("s1"), PassWorker("s2"), PassWorker("s3")
    p = Pipeline(st, [Stage("inference", w1),
                      Stage("validate_raw", w2, depends_on=("inference",)),
                      Stage("clean", w3, depends_on=("validate_raw",))])
    assert p.plan() == ["inference", "validate_raw", "clean"]
    rep = p.run(t["task_id"])
    assert rep.outcome == "EXECUTED", rep.outcome
    assert rep.executed_stages == ["inference", "validate_raw", "clean"]
    got = repo.get(t["task_id"])
    for k in ("inference", "validate_raw", "clean"):
        assert got["stages"][k]["status"] == "COMPLETED"
    td.cleanup()


def test_pipeline_diamond_execution_with_upstream_refs():
    td, st, repo = _fresh()
    t = _task(st, repo)
    a = PassWorker("A")
    b = PassWorker("B", needs_upstream="inference")          # reads A's output
    c = PassWorker("C", needs_upstream="inference")          # reads A's output too
    d = PassWorker("D", needs_upstream="clean")              # reads B's output
    p = Pipeline(st, [
        Stage("inference", a),
        Stage("validate_raw", PassWorker("V"), depends_on=("inference",)),
        Stage("clean", b, depends_on=("validate_raw",)),
        Stage("validate_clean", PassWorker("VC"), depends_on=("validate_raw",)),
        Stage("uv", c, depends_on=("validate_raw",)),
        Stage("bake", d, depends_on=("clean", "uv")),
    ])
    rep = p.run(t["task_id"])
    assert rep.outcome == "EXECUTED", rep.outcome
    assert set(rep.executed_stages) == {"inference", "validate_raw", "clean", "validate_clean", "uv", "bake"}
    # upstream payload actually flowed: D's artifact contains A's content
    d_slot = repo.get(t["task_id"])["stages"]["bake"]["checkpoint_data"]
    d_path = d_slot["output_path"]
    content = Path(d_path).read_text(encoding="utf-8")
    assert content == "D+B+A", content   # D <- clean(B); B <- inference(A); A = leaf
    td.cleanup()


def test_pipeline_rejects_stage_key_not_in_schema():
    td, st, repo = _fresh()
    t = _task(st, repo)
    p = Pipeline(st, [Stage("nonexistent_stage", PassWorker("x"))])
    try:
        p.run(t["task_id"])
        raise AssertionError("unknown schema key must raise")
    except ValueError as e:
        assert "not defined by task schema" in str(e)
    td.cleanup()


def test_pipeline_stops_on_unretryable_failure():
    td, st, repo = _fresh()
    t = _task(st, repo)
    class Bomb(BaseWorker):
        name = "bomb"; processor_version = "1.0"
        def validate_input(s, task, ctx): pass
        def execute(s, task, ctx):
            return WorkerResult.fail("INPUT_INVALID", "bad input")
        def validate_output(s, task, res, ctx): pass
    w_after = PassWorker("after")
    p = Pipeline(st, [Stage("inference", Bomb()),
                      Stage("validate_raw", w_after, depends_on=("inference",))])
    rep = p.run(t["task_id"])
    assert rep.outcome == "FAILED"
    assert w_after.executions == 0            # downstream never ran
    got = repo.get(t["task_id"])
    assert got["stages"]["validate_raw"] == {}   # untouched
    td.cleanup()


def test_pipeline_partial_on_retryable_failure():
    td, st, repo = _fresh()
    t = _task(st, repo)
    class Flake(BaseWorker):
        name = "flake"; processor_version = "1.0"
        def validate_input(s, task, ctx): pass
        def execute(s, task, ctx):
            return WorkerResult.fail("NETWORK_TIMEOUT", "flaky")
        def validate_output(s, task, res, ctx): pass
    w_after = PassWorker("after")
    p = Pipeline(st, [Stage("inference", PassWorker("A")),
                      Stage("validate_raw", Flake(), depends_on=("inference",)),
                      Stage("clean", w_after, depends_on=("validate_raw",))])
    rep = p.run(t["task_id"])
    assert rep.outcome == "PARTIAL"
    assert rep.executed_stages == ["inference"]
    assert w_after.executions == 0
    got = repo.get(t["task_id"])
    assert got["stages"]["inference"]["status"] == "COMPLETED"
    assert got["stages"]["validate_raw"]["status"] == "RETRY_WAIT"
    td.cleanup()


def test_pipeline_full_resume_after_partial():
    """R4.3 preview: after PARTIAL, a re-run skips done stages and finishes."""
    td, st, repo = _fresh()
    t = _task(st, repo)
    flake_state = {"fail": True}
    class Heals(BaseWorker):
        name = "heals"; processor_version = "1.0"
        def validate_input(s, task, ctx): pass
        def execute(s, task, ctx):
            if flake_state["fail"]:
                return WorkerResult.fail("NETWORK_TIMEOUT", "first fails")
            out = ctx.rel_in_workspace("healed.out")
            ctx.storage.write_text(out, "ok")
            sha = ctx.storage.sha256(out)
            return WorkerResult.ok("healed", sha, artifacts=[Artifact("healed", out, sha)])
        def validate_output(s, task, res, ctx): pass
    w_heal = Heals()
    a = PassWorker("A")
    p = Pipeline(st, [Stage("inference", a),
                      Stage("validate_raw", w_heal, depends_on=("inference",)),
                      Stage("clean", PassWorker("C"), depends_on=("validate_raw",))])
    rep1 = p.run(t["task_id"])
    assert rep1.outcome == "PARTIAL"
    assert a.executions == 1
    flake_state["fail"] = False
    rep2 = p.run(t["task_id"])
    assert rep2.outcome == "EXECUTED", rep2.outcome
    assert rep2.resumed_from == "validate_raw"
    assert a.executions == 1                     # NOT re-executed
    assert set(rep2.executed_stages) == {"validate_raw", "clean"}
    td.cleanup()
