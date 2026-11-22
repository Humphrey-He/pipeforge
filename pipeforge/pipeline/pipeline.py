"""Pipeline — multi-stage execution coordinator (R4 contract 2/3).

Orchestrates stages in topological order by DELEGATING every execution to the
R3 Runner (this class never executes workers itself). Stage-level recovery is
the natural extension of R3 idempotent-skip semantics: re-running a pipeline
after interruption skips COMPLETED stages and resumes at the first unfinished
one. Upstream artifacts reach workers exclusively via checkpoint references
(WorkerContext.upstream) — never via worker internals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .. import protocol as P
from ..core import CheckpointManager, StateMachine, TaskRepository
from ..runner import RunReport, Runner
from ..storage import Storage
from ..workers import BaseWorker, WorkerContext
from .stage import Stage, validate_graph


@dataclass
class PipelineReport:
    task_id: str
    outcome: str                  # EXECUTED / PARTIAL / FAILED / SKIPPED_ALL / CANCELLED
    stage_reports: list = field(default_factory=list)
    executed_stages: list = field(default_factory=list)
    resumed_from: Optional[str] = None

    def __bool__(self) -> bool:
        return self.outcome in ("EXECUTED", "SKIPPED_ALL")


class Pipeline:
    """Multi-stage coordinator. Construction validates the stage graph against
    itself; run() additionally validates stage keys against the task schema."""

    def __init__(self, storage: Storage, stages: list,
                 repository: Optional[TaskRepository] = None,
                 checkpoints: Optional[CheckpointManager] = None):
        self._s = storage
        self.stages = list(stages)
        self._order = validate_graph(self.stages)   # raises on dupes/unknown/cycles
        self._by_key = {s.key: s for s in self.stages}
        self._repo = repository or TaskRepository(storage)
        self._ck = checkpoints or CheckpointManager(storage)

    # -------------------------------------------------- planning

    def plan(self) -> list[str]:
        """Canonical topological execution order (cached at construction)."""
        return list(self._order)

    def _linear_mode(self) -> bool:
        return all(not s.depends_on for s in self.stages)

    # -------------------------------------------------- execution

    def run(self, task_or_id) -> PipelineReport:
        runner = Runner(self._s, self._repo, self._ck)
        task = runner._load(task_or_id)
        tid = task["task_id"]
        # stage keys must exist in the TASK schema definition
        for s in self.stages:
            if s.key not in task["stages"]:
                raise ValueError("stage %r not defined by task schema" % s.key)
        already = [k for k in self._order
                   if (task["stages"].get(k) or {}).get("status") in ("COMPLETED", "SKIPPED")]
        resumed_from = None
        if already and len(already) < len(self._order):
            resumed_from = next(k for k in self._order if k not in already)
        reports: list[RunReport] = []
        executed: list[str] = []
        outcome = "EXECUTED"
        if len(already) == len(self._order):
            outcome = "SKIPPED_ALL"
        for key in self._order:
            stage = self._by_key[key]
            slot = (task["stages"].get(key) or {})
            status = slot.get("status")
            if status in ("COMPLETED", "SKIPPED"):
                continue
            # dependency gate: all deps must be COMPLETED (or SKIPPED if optional)
            blocked = self._unsatisfied_deps(task, stage)
            if blocked:
                outcome = "FAILED" if outcome != "EXECUTED" else "FAILED"
                reports.append(self._dep_blocked_report(tid, key, blocked))
                break
            r = runner.run(task, stage.worker, key)
            reports.append(r)
            task = self._repo.get(tid)  # reload authoritative state
            if r.outcome == "COMPLETED":
                executed.append(key)
                continue
            if r.outcome == "SKIPPED":
                continue
            if r.outcome == "RETRY_WAIT":
                outcome = "PARTIAL"
                break
            # FAILED / others: stop the pipeline
            outcome = "FAILED"
            break
        if outcome == "EXECUTED" and executed == [] and not already:
            outcome = "EXECUTED"
        return PipelineReport(tid, outcome, stage_reports=reports,
                              executed_stages=executed, resumed_from=resumed_from)

    def _unsatisfied_deps(self, task: dict, stage: Stage) -> list[str]:
        if not stage.depends_on:
            return []
        out = []
        for dep in stage.depends_on:
            st = (task["stages"].get(dep) or {}).get("status")
            dep_stage = self._by_key[dep]
            ok = st == "COMPLETED" or (st == "SKIPPED" and dep_stage.optional)
            if not ok:
                out.append("%s=%s" % (dep, st))
        return out

    def _dep_blocked_report(self, tid: str, key: str, blocked: list[str]) -> RunReport:
        return RunReport(tid, key, "FAILED",
                         error={"code": "INPUT_INVALID",
                                "stage": key,
                                "message": "upstream dependency not satisfied: %s" % ", ".join(blocked),
                                "retryable": False, "attempt": 0,
                                "timestamp": P.now_iso()})
