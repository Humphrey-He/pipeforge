"""GS 10-stage Mock Demo (R4.6): the REAL protocol schema's ten stages driven
end-to-end by a Pipeline with mock workers, on the real burner reference.

Covers: diamond-free linear ten stages, mid-pipeline interruption + resume,
publish of final artifacts, idempotent full re-run.
"""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"D:\Repo\pipeforge")

from pipeforge import (Artifact, BaseWorker, LocalStorage, Pipeline, Publisher,
                       Stage, WorkerContext, WorkerResult)
from pipeforge.core import TaskRepository
import pipeforge.protocol as P

REAL_REF = Path(r"D:\Gaming Studio\AssetFactory\references\props\YL_PRP_incense_burner_v01_ref.png")
SCHEMA_STAGES = list(P.STAGES)   # the ten real protocol stages


class StageMockWorker(BaseWorker):
    """Per-stage mock: consumes upstream artifact bytes, appends its tag."""
    processor_version = "mock-1.0"

    def __init__(self, tag):
        self.tag = tag
        self.name = "mock_%s" % tag
        self.executions = 0

    def validate_input(self, task, context):
        refs = task["source"]["reference"]
        if not refs or not context.storage.exists(context.rel_in_task(refs[0]["path"])):
            raise ValueError("reference missing")

    def execute(self, task, context):
        self.executions += 1
        payload = self.tag.encode()
        # consume upstream (the immediately preceding completed stage)
        ups = [k for k in SCHEMA_STAGES if k != self.tag and context.upstream_refs.get(k)]
        if ups:
            ref = context.upstream(ups[-1])
            payload += b"<-" + context.storage.read_bytes(ref.path_rel)[:16]
        out = context.rel_in_workspace("%s.bin" % self.tag)
        context.storage.write_bytes(out, payload)
        sha = context.storage.sha256(out)
        return WorkerResult.ok(self.tag, sha,
                               artifacts=[Artifact(self.tag, out, sha)],
                               metrics={"stage": self.tag})

    def validate_output(self, task, result, context):
        for a in result.artifacts:
            if context.storage.sha256(a.path_rel) != a.sha256:
                raise ValueError("hash mismatch")


def _mk_task(st, repo):
    t = json.loads(Path(r"D:\Repo\pipeforge\pipeforge\schemas\examples\task.example.json").read_text(encoding="utf-8"))
    for k in t["stages"]:
        t["stages"][k] = {}
    ref_sha = P.sha256_file(REAL_REF)
    t["source"]["reference"] = [{"path": "ref_001.png", "sha256": ref_sha,
                                 "width": 512, "height": 512, "role": "primary"}]
    d = "tasks/claimed/%s" % t["task_id"]
    st.mkdir(d)
    shutil.copy(REAL_REF, st.resolve(d + "/ref_001.png"))
    st.atomic_write_json(d + "/task.json", t)
    repo.submit(copy.deepcopy(t))
    return t


def main():
    print("=== GS 10-Stage Mock Demo (R4.6) ===")
    td = tempfile.TemporaryDirectory()
    st = LocalStorage(td.name)
    repo = TaskRepository(st)
    t = _mk_task(st, repo)
    workers = {k: StageMockWorker(k) for k in SCHEMA_STAGES}
    stages = [Stage(k, workers[k], depends_on=((SCHEMA_STAGES[i-1],) if i else ()))
              for i, k in enumerate(SCHEMA_STAGES)]
    pipe = Pipeline(st, stages)
    print("[1] plan:", " -> ".join(pipe.plan()[:4]), "... (%d stages)" % len(pipe.plan()))

    # run 1: interrupt at stage 6 (quality) by a flaky worker
    flaky = {"on": True}
    orig_exec = workers["quality"].execute
    def flaky_exec(task, ctx):
        if flaky["on"]:
            return WorkerResult.fail("NETWORK_TIMEOUT", "simulated GPU flake")
        return orig_exec(task, ctx)
    workers["quality"].execute = flaky_exec
    rep1 = pipe.run(t["task_id"])
    print("[2] run1:", rep1.outcome, "| executed:", rep1.executed_stages)
    assert rep1.outcome == "PARTIAL"
    done_after_run1 = [k for k in SCHEMA_STAGES
                       if (repo.get(t["task_id"])["stages"][k] or {}).get("status") == "COMPLETED"]
    print("[3] completed so far:", len(done_after_run1), "stages")

    # run 2: heal + resume (fresh Pipeline = new process simulation)
    flaky["on"] = False
    pipe2 = Pipeline(st, stages)
    rep2 = pipe2.run(t["task_id"])
    print("[4] run2:", rep2.outcome, "| resumed_from:", rep2.resumed_from,
          "| newly executed:", rep2.executed_stages)
    assert rep2.outcome == "EXECUTED"
    assert rep2.resumed_from == "quality"
    first5 = [w.executions for k, w in workers.items() if k in done_after_run1]
    assert all(e == 1 for e in first5), "completed stages must not re-execute"

    # publish final artifacts
    final = repo.get(t["task_id"])
    arts = []
    for k in SCHEMA_STAGES:
        ck = final["stages"][k]["checkpoint_data"]
        root = str(st.root)
        rel = str(ck["output_path"])
        if rel.startswith(root):
            rel = rel[len(root):].replace(chr(92), "/").lstrip("/")
        arts.append(Artifact(k, rel, ck["output_hash"]))
    pub = Publisher(st)
    vr = pub.validate(final, arts)
    print("[5] publish validate:", vr.state)
    assert vr.state == "VALIDATED"
    target = "library/%s/%s" % (final["asset_id"], "v001")
    pr = pub.publish(final, arts, target)
    print("[6] published:", pr.state, "->", target)
    assert pr.state == "PUBLISHED"
    marker = st.read_json(target + "/PUBLISHED")
    print("    manifest sha:", marker["manifest_sha256"][:12], "| artifacts:", len(marker and st.read_json(target + "/manifest.json")["artifacts"]))

    # idempotent full re-run after publish
    pipe3 = Pipeline(st, stages)
    rep3 = pipe3.run(t["task_id"])
    print("[7] rerun after publish:", rep3.outcome)
    assert rep3.outcome == "SKIPPED_ALL"
    # complete task
    from pipeforge import Runner
    Runner(st, repo).complete_task(t["task_id"])
    print("[8] task status:", repo.get(t["task_id"])["status"])
    print("\n=== 10-STAGE DEMO PASSED: interrupt@6 -> resume -> publish -> idempotent ===")
    td.cleanup()


if __name__ == "__main__":
    main()
