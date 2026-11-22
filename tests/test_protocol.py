"""PipeForge protocol layer tests (pure stdlib; jsonschema optional)."""
import copy, json, sys, tempfile
from pathlib import Path

import pipeforge.protocol as P

TASK = json.loads((Path(__file__).parent.parent / "pipeforge" / "schemas" / "examples" / "task.example.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((Path(__file__).parent.parent / "pipeforge" / "schemas" / "examples" / "manifest.example.json").read_text(encoding="utf-8"))

def test_examples_valid():
    ok, err = P.validate_task(TASK); assert ok, err
    ok, err = P.validate_manifest(MANIFEST); assert ok, err

def test_stage_forms_both_accepted():
    t = copy.deepcopy(TASK)
    for k in t["stages"]: t["stages"][k] = {}
    ok, err = P.validate_task(t); assert ok, err

def test_transitions():
    assert P.can_transition("PENDING", "RUNNING")
    assert not P.can_transition("COMPLETED", "RUNNING")
    assert not P.can_transition("PENDING", "COMPLETED")  # must pass RUNNING

def test_invalidate_from():
    inv = P.invalidate_from("clean")
    assert "clean" in inv and "uv" in inv and "publish" in inv
    assert "inference" not in inv and "validate_raw" not in inv

def test_new_task_id_prefix():
    assert P.new_task_id(1).startswith("TASK_")

def test_config_hash_deterministic():
    h1 = P.config_hash({"a": 1, "b": 2})
    h2 = P.config_hash({"b": 2, "a": 1})  # key order must not matter
    assert h1 == h2 and len(h1) == 64

def test_stage_valid_checkpoint_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "in.glb"; inp.write_bytes(b"mesh-bytes")
        outp = Path(td) / "out.glb"; outp.write_bytes(b"clean-bytes")
        t = copy.deepcopy(TASK)
        t["stages"]["clean"]["checkpoint_data"] = {
            "status": "COMPLETED",
            "processor_version": "cleaner-1.0",
            "input_path": str(inp), "input_hash": P.sha256_file(inp),
            "config_hash": P.config_hash(t["config"]["clean"]),
            "output_path": str(outp), "output_hash": P.sha256_file(outp),
        }
        # add checkpoint_data via schema-permissive path: stages allow additional props? check both
        ok, _ = P.validate_task(t)
        assert P.stage_valid(t, "clean", inp, "cleaner-1.0", outp)
        # tamper output → hash mismatch → invalid
        outp.write_bytes(b"tampered")
        assert not P.stage_valid(t, "clean", inp, "cleaner-1.0", outp)