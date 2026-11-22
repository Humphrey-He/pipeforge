"""PipeForge — 可恢复生产管线框架（面向 AI 生成与数据处理场景）。

提供统一任务协议、阶段状态管理、断点恢复、Worker 执行接口、
质量门禁和产物发布机制。

版本规则：包版本（框架代码兼容性）与 Task Schema 版本（协议兼容性）独立演进。
"""

__version__ = "0.4.0"
TASK_SCHEMA_VERSION = "2.0"
MANIFEST_SCHEMA_VERSION = "2.0"

from . import protocol  # noqa: F401
from .core import CheckpointManager, QueueScanner, StageOrderError, StateMachine, TaskRepository  # noqa: F401
from .storage import LocalStorage, Storage  # noqa: F401
from .workers import Artifact, BaseWorker, WorkerContext, WorkerResult  # noqa: F401
from .runner import RunReport, Runner  # noqa: F401