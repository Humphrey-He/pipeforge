"""Publisher — three-state release protocol (R4 contract 4).

EXECUTED (pipeline outcome) -> VALIDATED (artifact checks pass) -> PUBLISHED
(version directory swapped in atomically, marker written last).

Publish failures are NEVER worker failures: a PUBLISH_FAILED report leaves
the task EXECUTED and every expensive upstream stage untouched; publishing
is idempotent (occupied target rejected, existing versions never damaged).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .. import protocol as P
from ..storage import Storage
from ..workers import Artifact


@dataclass
class PublishReport:
    task_id: str
    state: str                    # VALIDATED / PUBLISHED / FAILED
    target: str = ""
    artifacts: list = field(default_factory=list)
    manifest_sha256: str = ""
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.state == "PUBLISHED"


class Publisher:
    """Validate-then-publish. Independent of Pipeline execution logic."""

    def __init__(self, storage: Storage):
        self._s = storage

    def validate(self, task: dict, artifacts: list,
                 validator: Optional[Callable[[dict, list], Optional[str]]] = None,
                 ) -> PublishReport:
        """Check every artifact exists and hashes match. A custom validator
        may return an error string (business quality gate) — None means pass."""
        errs = []
        for a in artifacts:
            if not self._s.exists(a.path_rel):
                errs.append("artifact missing: %s" % a.path_rel)
                continue
            if a.sha256 and self._s.sha256(a.path_rel) != a.sha256:
                errs.append("artifact hash mismatch: %s" % a.path_rel)
        if not artifacts:
            errs.append("no artifacts to publish")
        if validator is not None and not errs:
            verr = validator(task, artifacts)
            if verr:
                errs.append(str(verr))
        if errs:
            return PublishReport(task["task_id"], "FAILED", error="; ".join(errs))
        return PublishReport(task["task_id"], "VALIDATED", artifacts=artifacts)

    def publish(self, task: dict, artifacts: list, target_rel: str,
                staging_rel: str = "",
                validator: Optional[Callable[[dict, list], Optional[str]]] = None,
                ) -> PublishReport:
        """Eight-step safe publish (R2 protocol generalized).

        target_absent check -> stage artifacts into target staging -> atomic
        swap -> PUBLISHED marker last. Idempotent: occupied target rejected,
        existing published versions never touched on failure.
        """
        vr = self.validate(task, artifacts, validator)
        if vr.state != "VALIDATED":
            return vr
        if self._s.exists(target_rel):
            return PublishReport(task["task_id"], "FAILED", target=target_rel,
                                 error="publish target already exists: %s" % target_rel)
        # gather artifacts into a staging dir then swap the whole dir in
        staging = staging_rel or ("%s.staging" % target_rel)
        if self._s.exists(staging):
            self._s.remove(staging)
        for a in artifacts:
            src = self._s.resolve(a.path_rel)
            rel = "%s/%s" % (staging, a.path_rel.replace("/", "__"))
            data = src.read_bytes()
            self._s.write_bytes(rel, data)
            if self._s.sha256(rel) != a.sha256:
                return PublishReport(task["task_id"], "FAILED", target=target_rel,
                                     error="staged copy hash mismatch: %s" % a.key)
        manifest = {
            "schema_version": "1.0",
            "task_id": task["task_id"],
            "asset_id": task.get("asset_id", ""),
            "artifacts": [{"key": a.key, "path": a.path_rel, "sha256": a.sha256} for a in artifacts],
            "created_at": P.now_iso(),
        }
        import json
        self._s.write_text("%s/manifest.json" % staging, json.dumps(manifest, indent=2))
        msha = self._s.sha256("%s/manifest.json" % staging)
        self._s.move(staging, target_rel)
        marker = {
            "status": "PUBLISHED", "task_id": task["task_id"],
            "manifest": "manifest.json", "manifest_sha256": msha,
            "published_at": P.now_iso(),
        }
        self._s.atomic_write_json("%s/PUBLISHED" % target_rel, marker)
        return PublishReport(task["task_id"], "PUBLISHED", target=target_rel,
                             artifacts=artifacts, manifest_sha256=msha)
