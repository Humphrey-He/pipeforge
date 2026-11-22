"""R4.4 Publisher tests: three-state protocol, immutable releases, isolation
of publish failures from execution state."""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from pipeforge import Artifact, LocalStorage, Pipeline, Publisher, Stage
from pipeforge.core import TaskRepository

TASK = json.loads((Path(__file__).parent.parent / "pipeforge" / "schemas" / "examples" / "task.example.json").read_text(encoding="utf-8"))


class TmpW:
    pass


def _fresh():
    td = tempfile.TemporaryDirectory()
    st = LocalStorage(td.name)
    return td, st, TaskRepository(st)


def _artifacts(st, n=2):
    arts = []
    for i in range(n):
        rel = "work/a%d.bin" % i
        st.write_text(rel, "payload-%d" % i)
        arts.append(Artifact("a%d" % i, rel, st.sha256(rel)))
    return arts


def _task_dict():
    t = copy.deepcopy(TASK)
    for k in t["stages"]:
        t["stages"][k] = {}
    return t


def test_publisher_validate_ok_and_missing():
    td, st, _ = _fresh()
    arts = _artifacts(st, 2)
    pub = Publisher(st)
    t = _task_dict()
    vr = pub.validate(t, arts)
    assert vr.state == "VALIDATED", vr.error
    # one artifact missing on disk
    st.remove("work/a1.bin")
    vr2 = pub.validate(t, arts)
    assert vr2.state == "FAILED"
    assert "missing" in vr2.error
    td.cleanup()


def test_publisher_validate_custom_gate():
    td, st, _ = _fresh()
    arts = _artifacts(st, 1)
    pub = Publisher(st)
    t = _task_dict()
    gate = lambda task, a: "quality score too low"
    vr = pub.validate(t, arts, validator=gate)
    assert vr.state == "FAILED" and "quality" in vr.error
    ok_gate = lambda task, a: None
    vr2 = pub.validate(t, arts, validator=ok_gate)
    assert vr2.state == "VALIDATED"
    td.cleanup()


def test_publisher_publish_immutable():
    td, st, _ = _fresh()
    arts = _artifacts(st, 2)
    pub = Publisher(st)
    t = _task_dict()
    pr1 = pub.publish(t, arts, "library/TASK_x_v001")
    assert pr1.state == "PUBLISHED", pr1.error
    assert st.exists("library/TASK_x_v001/PUBLISHED")
    m = st.read_json("library/TASK_x_v001/PUBLISHED")
    assert m["manifest_sha256"] == pr1.manifest_sha256
    # double publish -> rejected, first version intact
    pr2 = pub.publish(t, arts, "library/TASK_x_v001")
    assert pr2.state == "FAILED"
    assert "already exists" in pr2.error
    m2 = st.read_json("library/TASK_x_v001/PUBLISHED")
    assert m2 == m
    td.cleanup()


def test_publisher_no_artifacts_refused():
    td, st, _ = _fresh()
    pub = Publisher(st)
    vr = pub.validate(_task_dict(), [])
    assert vr.state == "FAILED" and "no artifacts" in vr.error
    td.cleanup()


def test_publish_failure_never_touches_execution_state():
    """Publish failure must not mark pipeline/workers failed."""
    td, st, repo = _fresh()
    t = _task_dict()
    repo.submit(t)
    arts = _artifacts(st, 1)
    # corrupt the artifact hash so staging copy check fails
    bad = Artifact("a0", arts[0].path_rel, "f" * 64)
    pub = Publisher(st)
    pr = pub.publish(t, [bad], "library/TASK_x_v001")
    assert pr.state == "FAILED"
    got = repo.get(t["task_id"])
    assert got["status"] == "PENDING"            # execution state untouched
    assert got["errors"] == []
    assert not st.exists("library/TASK_x_v001")  # no half-published dir
    td.cleanup()
