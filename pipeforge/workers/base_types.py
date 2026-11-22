"""Small shared types for the worker contract."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UpstreamRef:
    """Artifact reference of an upstream stage (from its checkpoint)."""
    stage_key: str
    path_rel: str
    sha256: str
