"""TaskRepository —— records/ 权威任务台账。

唯一写入点：submit（新任务，幂等拒绝重复）/ update（读-改-校验-原子写回）。
所有落盘均经 Storage 抽象 + 协议校验：台账里的每条记录都是合法协议文档。
"""
from __future__ import annotations

from typing import Callable, Optional

from .. import protocol as P
from ..storage import Storage


class TaskRepository:
    def __init__(self, storage: Storage, records_dir: str = "tasks/records"):
        self._s = storage
        self._dir = records_dir

    def _rel(self, task_id: str) -> str:
        return "%s/%s.json" % (self._dir, task_id)

    def submit(self, task: dict) -> dict:
        ok, err = P.validate_task(task)
        if not ok:
            raise ValueError("任务未通过协议校验: %s" % err)
        if task["status"] != "PENDING":
            raise ValueError("submit 仅接受 PENDING 任务, 当前: %s" % task["status"])
        rel = self._rel(task["task_id"])
        if self._s.exists(rel):
            raise FileExistsError("任务已存在(幂等拒绝): %s" % task["task_id"])
        self._s.atomic_write_json(rel, task)
        return task

    def save(self, task: dict) -> dict:
        """Validate + atomically persist the given authoritative task dict.
        Unlike update(), no re-read from disk: the caller owns the mutation.
        Runner uses this at persistence points (single atomic write keeps
        checkpoint + status consistent)."""
        ok, err = P.validate_task(task)
        if not ok:
            raise ValueError("task failed protocol validation: %s" % err)
        self._s.atomic_write_json(self._rel(task["task_id"]), task)
        return task
    def get(self, task_id: str) -> Optional[dict]:
        rel = self._rel(task_id)
        return self._s.read_json(rel) if self._s.exists(rel) else None

    def update(self, task_id: str, fn: Callable[[dict], None]) -> dict:
        task = self.get(task_id)
        if task is None:
            raise KeyError("任务不存在: %s" % task_id)
        fn(task)
        ok, err = P.validate_task(task)
        if not ok:
            raise ValueError("更新后未通过协议校验: %s" % err)
        self._s.atomic_write_json(self._rel(task_id), task)
        return task

    def list(self, status: Optional[str] = None) -> list[dict]:
        out = []
        if not self._s.exists(self._dir):
            return out
        for name in self._s.list_dir(self._dir):
            if not name.endswith(".json"):
                continue
            t = self._s.read_json("%s/%s" % (self._dir, name))
            if status is None or t.get("status") == status:
                out.append(t)
        return out
