"""Storage 抽象接口。

PipeForge 全部持久化经由 Storage 完成；框架核心不绑定任何云厂商 API。
Drive / S3 / GCS 等后端实现本接口即可接入（由业务侧或扩展包提供）。
所有路径均为相对 root 的 rel，禁止绝对路径与穿越（经 protocol.safe_join 强制）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Storage(ABC):
    """相对路径寻址的存储接口。"""

    @abstractmethod
    def exists(self, rel: str) -> bool: ...

    @abstractmethod
    def read_text(self, rel: str) -> str: ...

    @abstractmethod
    def write_text(self, rel: str, data: str) -> None: ...

    @abstractmethod
    def read_bytes(self, rel: str) -> bytes: ...

    @abstractmethod
    def write_bytes(self, rel: str, data: bytes) -> None: ...

    @abstractmethod
    def read_json(self, rel: str) -> Any: ...

    @abstractmethod
    def atomic_write_json(self, rel: str, obj: Any) -> None: ...

    @abstractmethod
    def list_dir(self, rel: str = "") -> list[str]: ...

    @abstractmethod
    def mkdir(self, rel: str) -> None: ...

    @abstractmethod
    def sha256(self, rel: str) -> str: ...

    @abstractmethod
    def move(self, src_rel: str, dst_rel: str) -> None: ...

    @abstractmethod
    def remove(self, rel: str) -> None: ...

    @abstractmethod
    def resolve(self, rel: str) -> Path: ...
