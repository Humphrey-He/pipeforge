"""阶段/任务状态机薄封装（复用 protocol 的转换表与失效传播）。"""
from __future__ import annotations

from .. import protocol as P


class StageOrderError(ValueError):
    """前序阶段未完成即尝试执行本阶段。"""


class StateMachine:
    """无状态帮助器；任务 dict 为唯一数据载体。"""

    @staticmethod
    def can(cur: str, nxt: str) -> bool:
        return P.can_transition(cur, nxt)

    @staticmethod
    def transition(task: dict, nxt: str) -> dict:
        return P.transition(task, nxt)

    @staticmethod
    def assert_stage_order(task: dict, stage: str) -> None:
        idx = P.STAGES.index(stage)
        for prev in P.STAGES[:idx]:
            st = (task["stages"].get(prev) or {}).get("status")
            if st not in ("COMPLETED", "SKIPPED"):
                raise StageOrderError("前置阶段未完成: %s=%s (需要先于 %s)" % (prev, st, stage))

    @staticmethod
    def invalidate(task: dict, what_changed: str) -> dict:
        """将 what_changed 起点的全部下游阶段重置为空对象形态。"""
        for stage in P.invalidate_from(what_changed):
            task["stages"][stage] = {}
        return task
