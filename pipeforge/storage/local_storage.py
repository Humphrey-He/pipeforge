"""本地文件系统 Storage 实现（测试与单机部署用）。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .. import protocol as P
from .base import Storage


class LocalStorage(Storage):
    def __init__(self, root: str | Path):
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, rel: str) -> Path:
        if not rel or rel == ".":
            return self._root
        return P.safe_join(self._root, rel)

    def exists(self, rel: str) -> bool:
        return self.resolve(rel).exists()

    def read_text(self, rel: str) -> str:
        return self.resolve(rel).read_text(encoding="utf-8")

    def write_text(self, rel: str, data: str) -> None:
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")

    def read_json(self, rel: str) -> Any:
        return P.read_json(self.resolve(rel))

    def atomic_write_json(self, rel: str, obj: Any) -> None:
        P.atomic_write_json(self.resolve(rel), obj)

    def list_dir(self, rel: str = "") -> list[str]:
        p = self.resolve(rel)
        if not p.is_dir():
            return []
        return sorted(x.name for x in p.iterdir())

    def mkdir(self, rel: str) -> None:
        self.resolve(rel).mkdir(parents=True, exist_ok=True)

    def sha256(self, rel: str) -> str:
        return P.sha256_file(self.resolve(rel))

    def move(self, src_rel: str, dst_rel: str) -> None:
        src = self.resolve(src_rel)
        dst = self.resolve(dst_rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

    def remove(self, rel: str) -> None:
        p = self.resolve(rel)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
