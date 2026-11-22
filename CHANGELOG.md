# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- R3: Runner/Scheduler coordination layer; checkpoint integration into worker execution loop

## [0.3.0] - 2026-11-22

### Added
- `pipeforge/workers/base.py` — `BaseWorker` ABC (three-phase: validate_input / execute / validate_output), `WorkerResult` (success/output_key/output_hash/metrics/error_code/error_message + `artifacts` list for multi-output), `WorkerContext` (storage + task_dir + workspace), `Artifact` dataclass. Contract deliberately free of pipeline semantics; workers cannot touch repository/status/publish markers (PF-01..03)
- `examples/file_worker.py` — second-consumer scenario proving genericity: text transform → verify → hash → generic artifact manifest (own pluggable schema v1.0, NOT the AssetFactory manifest) → validate → eight-step safe publish (staging → checks → atomic swap → PUBLISHED written LAST); double-publish rejected, existing versions never damaged (PF-04..07)
- `examples/demo_file_pipeline.py` — end-to-end demo (submit → validate → execute → publish)
- `tests/test_file_worker.py` — 5 tests: input hash mismatch, missing input, manifest correctness, publish-then-no-overwrite, staging preserved on occupied target
- Gaming Studio `adapters/legacy_job_adapter.py` — pure read-only V1 JOB → V2 Task converter: deterministic asset-id mapping (YL_PRP_000128 → PROP_INCENSE_BURNER_128), stable task-id derivation persisted in sidecar registry (task schema is additionalProperties:false), unknown fields audited not dropped, legacy status never inherited (forced PENDING), .blend sources honestly rejected (schema requires image references — no fabricated fields), originals never modified (GS-01..05)
- `adapters/tests/test_legacy_job_adapter.py` — 8 tests incl. cross-process determinism probe and real-sample rejection

### Acceptance
- All 14 review checkpoints (PF-01..07, GS-01..05, INT-01..02) green; PF suite 27/27, GS adapter suite 8/8

### Planned
- R2: worker layer (`BaseWorker` / `WorkerResult`), Gaming Studio adapter, example file-processing worker, first real asset task end-to-end

## [0.2.0] - 2026-11-22

### Added
- `pipeforge/storage/` — `Storage` ABC (12 methods: exists/read/write/read_json/atomic_write_json/list_dir/mkdir/sha256/move/remove/resolve) + `LocalStorage` implementation; all paths relative, traversal-safe via `protocol.safe_join`
- `pipeforge/core/task_repository.py` — authoritative records ledger: `submit` (idempotent duplicate rejection) / `update` (read-mutate-validate-atomic-write-back) / `get` / `list`; every persisted record is a protocol-valid document
- `pipeforge/core/state_machine.py` — thin wrapper over protocol transitions: `can` / `transition` / `assert_stage_order` (raises `StageOrderError`) / `invalidate` (resets downstream stages to empty form)
- `pipeforge/core/checkpoint.py` — `CheckpointManager`: writes seven-item checkpoint payloads, advances stage status/attempts; `verifies` / `can_skip` delegating to `protocol.stage_valid`
- `pipeforge/core/queue_scanner.py` — inbox scan & claim: full submission protocol (valid task.json + reference hash match + READY marker) before claiming; claimed tasks moved `inbox/ → tasks/claimed/`; rejected stay in place with reason
- `tests/test_core.py` — 15 kernel tests on tempdir `LocalStorage` (roundtrips, duplicate/invalid rejection, stage ordering, invalidation, checkpoint tamper/version/config-change detection, scanner happy path + 3 rejection paths + reclaim after record loss)

### Changed
- Package version 0.1.0 → 0.2.0; `pipeforge/__init__.py` re-exports core + storage
- Test suite: 7 → 22 (all green)


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