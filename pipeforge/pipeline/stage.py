"""Stage — generic stage description (R4 contract 1).

A Stage binds a task-schema stage key to a worker plus dependency metadata.
PipeForge never interprets the business meaning of keys; it validates the
graph shape (acyclic, keys unique) at construction time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from ..workers import BaseWorker


@dataclass(frozen=True)
class Stage:
    """One node of a pipeline graph.

    key:        the stage key as defined by the TASK SCHEMA (not by PF);
    worker:     the worker that executes this stage;
    depends_on: other stage keys within the same pipeline; empty means
                "linear predecessor over the schema stage order" (matches
                R3 assert_stage_order semantics);
    optional:   a SKIPPED outcome satisfies dependents.
    """
    key: str
    worker: BaseWorker
    depends_on: Tuple[str, ...] = ()
    optional: bool = False

    def __post_init__(self) -> None:
        if not self.key or not isinstance(self.key, str):
            raise ValueError("stage key must be a non-empty string")
        if self.key in self.depends_on:
            raise ValueError("stage %r depends on itself" % self.key)


def validate_graph(stages) -> list[str]:
    """Construct-time validation. Returns the canonical topological order.

    Raises ValueError on: duplicate keys, unknown dependency keys, cycles.
    """
    keys = [s.key for s in stages]
    if len(set(keys)) != len(keys):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise ValueError("duplicate stage keys: %s" % dupes)
    by_key = {s.key: s for s in stages}
    for s in stages:
        for dep in s.depends_on:
            if dep not in by_key:
                raise ValueError("stage %r depends on unknown key %r" % (s.key, dep))
    # Kahn topological sort (stable: preserves declaration order among ready nodes)
    order: list[str] = []
    remaining = {s.key: set(s.depends_on) for s in stages}
    pending = [s.key for s in stages]
    while pending:
        progressed = False
        for k in list(pending):
            if not remaining[k]:
                order.append(k)
                pending.remove(k)
                progressed = True
                for other in pending:
                    remaining[other].discard(k)
        if not progressed:
            raise ValueError("dependency cycle among: %s" % sorted(pending))
    return order
