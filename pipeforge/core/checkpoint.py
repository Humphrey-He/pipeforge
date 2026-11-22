"""CheckpointManager —— 七项检查点的写入与验证（协议 6.2）。"""
from __future__ import annotations

from typing import Optional

from .. import protocol as P
from ..storage import Storage


class CheckpointManager:
    def __init__(self, storage: Storage):
        self._s = storage

    def write(self, task: dict, stage: str, processor_version: str,
              input_rel: Optional[str] = None, output_rel: Optional[str] = None,
              status: str = "COMPLETED", extra: Optional[dict] = None) -> dict:
        """写入完整检查点并推进阶段状态。返回检查点负载。"""
        if stage not in P.STAGES:
            raise ValueError("未知阶段: %s" % stage)
        ck = {
            "status": status,
            "processor_version": processor_version,
            "input_path": str(self._s.resolve(input_rel)) if input_rel else None,
            "input_hash": self._s.sha256(input_rel) if input_rel else None,
            "config_hash": P.config_hash(task.get("config", {}).get(stage, {})),
            "output_path": str(self._s.resolve(output_rel)) if output_rel else None,
            "output_hash": self._s.sha256(output_rel) if output_rel else None,
        }
        if extra:
            ck.update(extra)
        slot = task["stages"].setdefault(stage, {})
        if not isinstance(slot, dict):
            task["stages"][stage] = {}
            slot = task["stages"][stage]
        slot["checkpoint_data"] = ck
        if status == "COMPLETED":
            slot["status"] = "COMPLETED"
            slot["attempt"] = (slot.get("attempt") or 0) + 1
        return ck

    def verifies(self, task: dict, stage: str, processor_version: str,
                 input_rel: Optional[str] = None, output_rel: Optional[str] = None) -> bool:
        input_path = self._s.resolve(input_rel) if input_rel else None
        output_path = self._s.resolve(output_rel) if output_rel else None
        return P.stage_valid(task, stage, input_path, processor_version, output_path)

    def can_skip(self, task: dict, stage: str, processor_version: str,
                 input_rel: Optional[str] = None, output_rel: Optional[str] = None) -> bool:
        """stage_valid 全过才允许跳过重跑。"""
        return self.verifies(task, stage, processor_version, input_rel, output_rel)
