# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- R1: task kernel — `Storage` interface, `LocalStorage`, `TaskRepository`, `StateMachine` wrapper, `CheckpointManager`, `QueueScanner` (tempdir-tested)
- R2: Gaming Studio integration — `BaseWorker` / `WorkerResult`, V1 JOB adapter, first real asset task end-to-end

## [0.1.0] - 2026-11-22

First public cut: protocol layer migrated from AssetFactory as framework seed code.

### Added
- `pipeforge/protocol.py` — task/manifest validation (draft-07, degraded self-check without `jsonschema`), task-ID generation, stage state machine (`can_transition` / `invalidate_from`), deterministic `config_hash`, seven-item checkpoint validation (`stage_valid`), sha256 file hashing, publish-boundary checks
- `pipeforge/schemas/` — `task.schema.json` + `manifest.schema.json` (JSON Schema draft-07), with `examples/` validated fixtures
- Dual-form `stages`: tasks may start with empty stage objects `{}` and gain full objects (`status`/`attempt`/`checkpoint`/`error`/`checkpoint_data`) as stages execute
- `tests/` — 7 protocol tests incl. checkpoint tamper roundtrip; naive runner `tests/_run_all.py` (no pytest dependency)
- `pyproject.toml` — zero mandatory dependencies; optional `jsonschema` extra
- Version constants exported independently: `__version__` (package), `TASK_SCHEMA_VERSION` / `MANIFEST_SCHEMA_VERSION` (protocol)

### Fixed
- `task.schema.json` previously **forbade** the `checkpoint_data` key inside stage objects, while `stage_valid()` requires it at runtime — checkpointed tasks could never pass their own protocol validation. Full-form stages now permit `checkpoint_data` (structure validated semantically by `stage_valid()`). Fix synced back to the source AssetFactory protocol directory.

### Migration Notes
- Seed code originates from `D:\Gaming Studio\AssetFactory\protocol\` (copied, not moved — the source tree stays frozen as a mirror during the transition window).

[Unreleased]: https://github.com/Humphrey-He/pipeforge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Humphrey-He/pipeforge/releases/tag/v0.1.0