"""Example FileWorker — the second consumer scenario.

Proves PipeForge carries no game-asset semantics: input.txt -> transform ->
verify -> hash -> generic artifact manifest -> validate -> publish.

Manifest policy (pluggable business schema, NOT the AssetFactory manifest):
this example owns schema v1.0 with generic fields only.

Publish protocol (eight steps, PUBLISHED written LAST):
1. transform inside staging/  2. verify output exists  3. compute sha256
4. write manifest (staging)   5. validate manifest     6. target-absent check
7. atomic swap staging -> library  8. write PUBLISHED marker
Failed publish never damages an existing published version (staging only).
"""
from __future__ import annotations

import json

from pipeforge import BaseWorker, WorkerContext, WorkerResult, Artifact
from pipeforge import protocol as P

MANIFEST_SCHEMA_VERSION = "1.0"


class FileWorker(BaseWorker):
    name = "file_worker"
    processor_version = "0.1.0"

    def validate_input(self, task: dict, context: WorkerContext) -> None:
        refs = (task.get("source", {}).get("reference") or [])
        if not refs:
            raise ValueError("input file reference missing")
        ref = refs[0]
        rel = context.rel_in_task(ref["path"])
        if not context.storage.exists(rel):
            raise ValueError("input file not found: %s" % ref["path"])
        want = ref.get("sha256")
        if want and context.storage.sha256(rel) != want:
            raise ValueError("input file hash mismatch: %s" % ref["path"])

    def execute(self, task: dict, context: WorkerContext) -> WorkerResult:
        st = context.storage
        ws = context.workspace or ("tasks/work/%s" % task["task_id"])
        staging = ws + "/staging"
        src_rel = context.rel_in_task(task["source"]["reference"][0]["path"])
        text = st.read_text(src_rel)
        transformed = text.upper()
        out_name = "output.txt"
        out_rel = staging + "/" + out_name
        st.write_text(out_rel, transformed)
        if not st.exists(out_rel):
            return WorkerResult.fail("OUTPUT_INVALID", "staging output missing")
        sha = st.sha256(out_rel)
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "task_id": task["task_id"],
            "artifacts": [{"path": out_name, "sha256": sha,
                           "size_bytes": len(transformed.encode("utf-8"))}],
            "quality": {"status": "PASS"},
            "created_at": P.now_iso(),
        }
        ok, err = self.validate_manifest(manifest)
        if not ok:
            return WorkerResult.fail("OUTPUT_INVALID", "manifest invalid: %s" % err)
        st.atomic_write_json(staging + "/manifest.json", manifest)
        return WorkerResult.ok("output", sha,
                               artifacts=[Artifact("output", out_rel, sha)],
                               metrics={"bytes_in": len(text),
                                        "bytes_out": len(transformed)})

    def validate_output(self, task: dict, result: WorkerResult,
                        context: WorkerContext) -> None:
        if not result.success or not result.artifacts:
            raise ValueError("worker did not succeed or produced no artifacts")
        for a in result.artifacts:
            if not context.storage.exists(a.path_rel):
                raise ValueError("artifact missing: %s" % a.path_rel)
            if context.storage.sha256(a.path_rel) != a.sha256:
                raise ValueError("artifact hash mismatch: %s" % a.path_rel)

    @staticmethod
    def validate_manifest(m: dict):
        errs = []
        if m.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            errs.append("schema_version must be %s" % MANIFEST_SCHEMA_VERSION)
        if not str(m.get("task_id", "")).startswith("TASK_"):
            errs.append("task_id must carry TASK_ prefix")
        arts = m.get("artifacts") or []
        if not arts:
            errs.append("artifacts must be a non-empty list")
        else:
            for a in arts:
                if not a.get("path") or len(a.get("sha256", "")) != 64:
                    errs.append("artifact entry malformed: %r" % a)
        if m.get("quality", {}).get("status") not in ("PASS", "WARNING"):
            errs.append("quality.status must be PASS or WARNING")
        return (not errs), errs


def publish(storage, task_id: str, staging_rel: str, library_rel: str,
            manifest_rel: str = "manifest.json") -> dict:
    """Eight-step safe publish. PUBLISHED written LAST; no overwrite;
    failures leave published versions intact."""
    if storage.exists(library_rel):
        raise FileExistsError("publish target already exists: %s" % library_rel)
    storage.move(staging_rel, library_rel)
    m = storage.read_json(library_rel + "/" + manifest_rel)
    marker = {"status": "PUBLISHED", "task_id": task_id,
              "manifest": manifest_rel,
              "manifest_sha256": storage.sha256(library_rel + "/" + manifest_rel),
              "published_at": P.now_iso()}
    storage.atomic_write_json(library_rel + "/PUBLISHED", marker)
    return marker
