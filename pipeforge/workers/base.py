"""Worker execution contract.

BaseWorker only cares about execution behaviour: validate_input / execute / validate_output.
No pipeline semantics (stage names, models, meshes, textures) appear in this contract.
Workers must NOT touch TaskRepository, task status, or publish markers —
those belong to a future Runner / Scheduler / Publisher.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..storage import Storage


@dataclass
class Artifact:
    """Single artifact description (multi-artifact workers use artifacts list)."""
    key: str
    path_rel: str
    sha256: str = ""
    size_bytes: int = -1


@dataclass
class WorkerResult:
    success: bool
    output_key: str | None = None
    output_hash: str | None = None
    metrics: dict = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)

    @classmethod
    def ok(cls, key: str, sha: str, *, artifacts=None, metrics=None):
        return cls(success=True, output_key=key, output_hash=sha,
                   artifacts=artifacts or [], metrics=metrics or {})

    @classmethod
    def fail(cls, error_code: str, error_message: str, *, metrics=None):
        return cls(success=False, error_code=error_code,
                   error_message=str(error_message)[:500], metrics=metrics or {})  


@dataclass
class WorkerContext:
    storage: Storage
    task_dir: str = ""
    workspace: str = ""
    extras: dict = field(default_factory=dict)

    def rel_in_task(self, name: str) -> str:
        return "%s/%s" % (self.task_dir, name) if self.task_dir else name

    def rel_in_workspace(self, name: str) -> str:
        return "%s/%s" % (self.workspace, name) if self.workspace else name


class BaseWorker(ABC):
    """Three-phase worker contract; stateless; no protocol side effects."""

    name: str = "base"
    processor_version: str = "0.0.0"

    @abstractmethod
    def validate_input(self, task: dict, context: WorkerContext) -> None:
        """Raise ValueError with concrete reason when input is invalid."""

    @abstractmethod
    def execute(self, task: dict, context: WorkerContext) -> WorkerResult:
        """Run the transformation. Must NOT mutate protocol fields (status/stages)."""

    @abstractmethod
    def validate_output(self, task: dict, result: WorkerResult,
                        context: WorkerContext) -> None:
        """Raise ValueError when output is invalid."""
