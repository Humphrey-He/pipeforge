"""Runner — single-stage execution coordinator (R3).

Owns the worker lifecycle around one stage of a task:
load/validate -> status gate -> idempotent skip via checkpoint -> execute ->
validate output -> checkpoint write + task update in ONE atomic repository write.

Deliberately generic: stage_key is a parameter, whatever keys the task's schema
defines (PipeForge's shipped schema happens to carry a 3D pipeline's names;
other consumers may inject their own schema via validate_task(schema_path=))).

Consistency rules:
- Checkpoint mutation and task-status update persist together (single
  repository.update write) — no torn state between them.
- Re-running a COMPLETED stage with a verifying checkpoint SKIPS execution
  (worker not invoked) — idempotent.
- Interrupt before that write leaves the task RUNNING with attempt counted;
  the next run re-executes — crash-safe by construction, no partial writes.

Failure classification: worker error_code is mapped into the task schema's
error enum; unknown codes fall back to OUTPUT_INVALID with an [unmapped] tag
in the message (never dropped, never fabricated as a different severity).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .. import protocol as P
from ..core import CheckpointManager, StateMachine, TaskRepository
from ..storage import Storage
from ..workers import BaseWorker, WorkerContext, WorkerResult

OUTCOMES = ("SKIPPED", "COMPLETED", "RETRY_WAIT", "FAILED", "REJECTED")

_ALIASES = {
    "TIMEOUT": "NETWORK_TIMEOUT",
    "TRANSIENT": "NETWORK_TIMEOUT",
    "CRASHED": "OUTPUT_INVALID",
}


def map_error_code(code: Optional[str]) -> str:
    if code in P.ERROR_CODES:
        return code
    if code in _ALIASES:
        return _ALIASES[code]
    return "OUTPUT_INVALID"


@dataclass
class RunReport:
    task_id: str
    stage_key: str
    outcome: str                 # one of OUTCOMES
    attempts: int = 0
    skipped: bool = False
    error: Optional[dict] = None
    artifacts: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.outcome in ("SKIPPED", "COMPLETED")


class Runner:
    """Coordinates one stage. Does not orchestrate across stages — that is the
    business pipeline's job (see examples/demo and GS demo)."""

    def __init__(self, storage: Storage, repository: Optional[TaskRepository] = None,
                 checkpoints: Optional[CheckpointManager] = None):
        self._s = storage
        self._repo = repository or TaskRepository(storage)
        self._ck = checkpoints or CheckpointManager(storage)

    # -------------------------------------------------- helpers

    def _load(self, task_or_id) -> dict:
        if isinstance(task_or_id, dict):
            task = task_or_id
        else:
            task = self._repo.get(task_or_id)
        if task is None:
            raise KeyError("task not found: %r" % task_or_id)
        return task

    def _stage_slot(self, task: dict, stage_key: str) -> dict:
        slot = task["stages"].get(stage_key)
        if slot is None:
            raise ValueError("stage %r not defined by this task's schema" % stage_key)
        return slot

    # -------------------------------------------------- main entry

    def run(self, task_or_id, worker: BaseWorker, stage_key: str) -> RunReport:
        task = self._load(task_or_id)
        tid = task["task_id"]
        slot = self._stage_slot(task, stage_key)

        # 1. idempotent skip: completed + checkpoint still verifies
        if slot.get("status") == "COMPLETED":
            if self._ck.can_skip(task, stage_key, worker.processor_version,
                                  input_rel=self._input_rel(task),
                                  output_rel=self._output_rel(slot)):
                return RunReport(tid, stage_key, "SKIPPED", attempts=slot.get("attempt", 0),
                                 skipped=True)
            # checkpoint stale (config/processor/output changed) -> re-run below
            StateMachine.invalidate(task, stage_key)

        # 2. status gate: bring task to RUNNING (or resume an interrupted RUNNING)
        if task["status"] == "PENDING":
            StateMachine.transition(task, "RUNNING")
        elif task["status"] in ("RETRY_WAIT", "REVIEW"):
            StateMachine.transition(task, "RUNNING")
        elif task["status"] != "RUNNING":
            raise ValueError("task %s cannot run from status %s" % (tid, task["status"]))

        # 3. mark stage RUNNING, count attempt, persist the start point
        slot["status"] = "RUNNING"
        slot["attempt"] = (slot.get("attempt") or 0) + 1
        slot["error"] = None
        self._repo.save(task)

        ctx = WorkerContext(self._s, task_dir=self._task_dir(task),
                            workspace="tasks/work/%s" % tid)

        # 4. validate_input — rejection is non-retryable by contract
        try:
            worker.validate_input(task, ctx)
        except ValueError as e:
            return self._fail(task, stage_key, "INPUT_INVALID", str(e),
                              retryable=False)

        # 5. execute
        try:
            result = worker.execute(task, ctx)
        except Exception as e:  # worker crashed, not a clean WorkerResult
            return self._fail(task, stage_key, map_error_code(None),
                              "worker raised: %r" % e, retryable=True,
                              keep_running=True)

        if not result.success:
            raw = result.error_code or "OUTPUT_INVALID"
            retryable = self._retryable_code(raw)
            return self._fail(task, stage_key, raw,
                              result.error_message or "worker reported failure",
                              retryable=retryable)

        # 6. validate_output — output contract violation is non-retryable
        try:
            worker.validate_output(task, result, ctx)
        except ValueError as e:
            return self._fail(task, stage_key, "OUTPUT_INVALID", str(e),
                              retryable=False)

        # 7. checkpoint + stage COMPLETED + persist (single atomic write)
        out_rel = result.artifacts[0].path_rel if result.artifacts else None
        in_rel = self._input_rel(task)
        def _apply(t: dict) -> None:
            self._ck.write(t, stage_key, worker.processor_version,
                           input_rel=in_rel, output_rel=out_rel)
        _apply(task)
        self._repo.save(task)
        slot = self._stage_slot(task, stage_key)
        return RunReport(tid, stage_key, "COMPLETED", attempts=slot.get("attempt", 1),
                         artifacts=result.artifacts, metrics=result.metrics)

    # -------------------------------------------------- failure path

    def _retryable_code(self, code: Optional[str]) -> bool:
        return code in ("NETWORK_TIMEOUT", "GPU_OOM", "MODEL_LOAD_FAILED",
                        "DRIVE_SYNC_FAILED", "TRANSIENT", "TIMEOUT")

    def _fail(self, task: dict, stage_key: str, code: str, message: str, *,
              retryable: bool, keep_running: bool = False) -> RunReport:
        # failure paths can fire before the task ever reached RUNNING (e.g.
        # validate_input rejection on a PENDING task) — bring it to RUNNING
        # first so the state machine has a legal path to RETRY_WAIT/FAILED.
        if task["status"] == "PENDING":
            StateMachine.transition(task, "RUNNING")
        tid = task["task_id"]
        slot = self._stage_slot(task, stage_key)
        attempt = slot.get("attempt") or 1
        mapped = map_error_code(code)
        tagged = message if mapped == code else "[unmapped:%s->%s] %s" % (code, mapped, message)
        err = P.make_error(mapped, stage_key, tagged, retryable, attempt)

        def _apply(t: dict) -> None:
            s = t["stages"][stage_key]
            s["error"] = err["message"]
            t.setdefault("errors", []).append(err)
            t["retry"]["current_attempt"] = attempt
            if retryable and attempt < t["retry"]["max_attempts"]:
                s["status"] = "RETRY_WAIT"
                StateMachine.transition(t, "RETRY_WAIT")
            else:
                s["status"] = "FAILED"
                if t["status"] != "FAILED":
                    StateMachine.transition(t, "FAILED")

        _apply(task)
        self._repo.save(task)
        return RunReport(tid, stage_key,
                         "RETRY_WAIT" if (retryable and attempt < task["retry"]["max_attempts"]) else "FAILED",
                         attempts=attempt, error=err)

    # -------------------------------------------------- completion

    def complete_task(self, task_or_id, schema_path: Optional[str] = None) -> dict:
        """RUNNING -> COMPLETED only when every stage slot is COMPLETED/SKIPPED."""
        task = self._load(task_or_id)
        pending = [k for k, s in task["stages"].items()
                   if (s or {}).get("status") not in ("COMPLETED", "SKIPPED", None)]
        empty = [k for k, s in task["stages"].items() if not s]
        if empty:
            raise ValueError("stages never executed: %s" % ", ".join(empty))
        if task["status"] != "RUNNING":
            raise ValueError("complete_task from %s (need RUNNING)" % task["status"])
        StateMachine.transition(task, "COMPLETED")
        self._repo.save(task)
        return task

    # -------------------------------------------------- conventions

    def _task_dir(self, task: dict) -> str:
        tid = task["task_id"]
        for cand in ("tasks/claimed/%s" % tid, "inbox/%s" % tid):
            if self._s.exists(cand + "/task.json"):
                return cand
        return ""

    def _input_rel(self, task: dict) -> Optional[str]:
        refs = (task.get("source", {}).get("reference") or [])
        if not refs:
            return None
        base = self._task_dir(task)
        rel = refs[0].get("path", "")
        return "%s/%s" % (base, rel) if base else (rel or None)

    def _output_rel(self, slot: dict) -> Optional[str]:
        ck = slot.get("checkpoint_data") or {}
        op = ck.get("output_path")
        if not op:
            return None
        root = str(self._s.root)
        if str(op).startswith(root):
            return str(op)[len(root):].replace("\\", "/").lstrip("/")
        return None


def _apply_status_only(t: dict, stage_key: str, err: dict, attempt: int,
                       retryable: bool) -> None:
    s = t["stages"][stage_key]
    s["status"] = "RETRY_WAIT" if retryable else "FAILED"
    s["error"] = err["message"]
    t.setdefault("errors", []).append(err)
    t["retry"]["current_attempt"] = attempt
