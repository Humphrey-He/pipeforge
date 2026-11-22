"""QueueScanner —— inbox 扫描与认领。

完整提交协议（3.3）：task.json 合法 + 参考文件哈希一致 + READY 存在才认领。
认领 = 写权威 record（经 TaskRepository）+ inbox/{task_id} 整体移入 tasks/claimed/。
"""
from __future__ import annotations

from typing import Optional

from .. import protocol as P
from ..storage import Storage
from .task_repository import TaskRepository


class QueueScanner:
    def __init__(self, storage: Storage, repository: TaskRepository,
                 inbox_dir: str = "inbox", claimed_dir: str = "tasks/claimed"):
        self._s = storage
        self._repo = repository
        self._inbox = inbox_dir
        self._claimed = claimed_dir

    def scan_inbox(self) -> list[str]:
        """返回满足 task.json + READY 双条件的 task_id（目录名）列表。"""
        if not self._s.exists(self._inbox):
            return []
        out = []
        for name in self._s.list_dir(self._inbox):
            d = "%s/%s" % (self._inbox, name)
            if self._s.exists(d + "/task.json") and self._s.exists(d + "/READY"):
                out.append(name)
        return sorted(out)

    def _verify_references(self, task: dict, dir_rel: str) -> Optional[str]:
        for ref in (task.get("source", {}).get("reference") or []):
            rel = "%s/%s" % (dir_rel, ref.get("path", ""))
            if not self._s.exists(rel):
                return "缺参考文件: %s" % ref.get("path")
            want = ref.get("sha256")
            if want and self._s.sha256(rel) != want:
                return "参考文件哈希不符: %s" % ref.get("path")
        return None

    def intake_once(self) -> dict:
        """执行一轮认领。返回 {claimed: [...], rejected: [{dir, reason}]}。"""
        report = {"claimed": [], "rejected": []}
        for tid in self.scan_inbox():
            d = "%s/%s" % (self._inbox, tid)
            try:
                task = self._s.read_json(d + "/task.json")
            except Exception as e:
                report["rejected"].append({"dir": tid, "reason": "task.json 解析失败: %s" % e})
                continue
            if task.get("task_id") != tid:
                report["rejected"].append({"dir": tid, "reason": "目录名与 task_id 不符: %s" % task.get("task_id")})
                continue
            ok, err = P.validate_task(task)
            if not ok:
                report["rejected"].append({"dir": tid, "reason": "协议校验失败: %s" % err[:2]})
                continue
            ref_err = self._verify_references(task, d)
            if ref_err:
                report["rejected"].append({"dir": tid, "reason": ref_err})
                continue
            try:
                self._repo.submit(task)
            except FileExistsError:
                report["rejected"].append({"dir": tid, "reason": "record 已存在(重复提交)"})
                continue
            self._s.move(d, "%s/%s" % (self._claimed, tid))
            report["claimed"].append(task)
        return report
