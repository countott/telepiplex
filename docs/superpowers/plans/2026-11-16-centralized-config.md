# Telepiplex Centralized Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track the steps below; do not use Git in this workspace.

**Goal:** Make `/config/config.yaml` the only authoritative configuration, with Telegram edits, isolated Feature snapshots, and durable 115 credentials.

**Architecture:** A central YAML backend maps `shared` and `features.<plugin_id>` into each release's existing configuration shape. Host transactions validate candidate snapshots and coordinate affected processes before committing owned paths. Feature packages retain their default/schema contracts and independent installation.

**Tech Stack:** Python 3.12, existing PyYAML/jsonschema, asyncio Host RPC, pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-centralized-config-design.md` (approved by user in this chat).

## Global Constraints

- Mac source edits/local tests only; no Git, worktrees, GitHub access or publication.
- All project text mutations use apply_patch.
- Preserve Telepiplex branding and existing lowercase technical identities.
- Keep Telegram `/config`; all writes go to the top-level YAML in production.
- Keep each plugin's existing flat effective schema; don't expose the whole central YAML to plugins.
- Explicit false/zero/empty values retain semantics; unknown sections and uninstalled plugin configuration survive.
- No silent precedence for conflicting nonempty legacy shared values.
- Download must declare Host-mediated writes; old writable versions fail closed with an upgrade diagnostic.
- Successful version rollback keeps current settings and tokens; failed activation restores only owned unchanged paths.

## Review Focus

- A stale config wizard and a token refresh overlap: only chosen fields change; current token survives.
- Shared settings have multiple running consumers: all affected schemas validate and all consumers switch consistently.
- A legacy source disagrees on paths or nonempty TMDB tokens: no partial migration and no secrets in errors.
- A shadow/retired Download attempts a write: reject it; failed authorization rollback cannot restore stale credentials.
- A manual edit is invalid or a new process fails: old routes/processes remain usable and unrelated config is retained.

## Interfaces between tasks

`CentralConfigStore(path: Path)` owns YAML and exposes:

- `read() -> dict`
- `effective(plugin_id: str, defaults: dict, *, document: dict | None = None) -> dict`
- `prepare_patch(plugin_id: str, patch: dict) -> ConfigChange`
- `prepare_document_patch(patch: dict) -> ConfigChange` for Host-owned shared edits
- `commit(change: ConfigChange) -> dict` and `rollback(change: ConfigChange) -> dict`, compare expected old/current values only at changed paths and preserve other paths
- `migrate_legacy(plugin_root: Path) -> bool`, idempotent, preserving original private files

`ConfigChange` exposes `before`, `after`, `changed_paths`, and `affected_plugins`. Builtin projection maps category_folder for search/rename/sync to `shared.category_folder`, Search metadata.tmdb.api_key and Sync tmdb.api_key to `shared.tmdb.api_key`; all other plugin fields stay in `features.<id>`.

`PluginStore(root, *, central_config=None)` retains standalone legacy behavior only for compatibility/testing. Production always supplies central_config. It exposes `config_defaults(release) -> dict`, `read_config(release)` through central effective mapping, `central_config`, and `prepare_config_change(plugin_id, patch)`. Stage/commit in central mode must not create a private authoritative config or write candidate changes before healthy activation. Existing schema migration produces a candidate, not a premature authoritative write.

`PluginSupervisor.start(release, *, shadow=False, config=None)` accepts a prepared effective dict in central mode; each instance receives its own private snapshot. Existing legacy callers remain accepted. Broker identities carry instance_id and active/shadow status; promotion grants authority, stop/restart revokes old authority. Host config writer hooks receive authenticated identity, not a caller-selected plugin_id.

`PluginManager.configure_patch(plugin_id, patch, *, should_cancel=None)` uses raw patches, validates/reloads affected installed consumers, and commits only owned central paths. `configure_shared(patch, ...)` does the same for shared settings. Download live patches use a separately scoped current-instance path with expected old values, without acquiring a lifecycle lock that deadlocks drain/start.

## Task 1: Central YAML backend and PluginStore (storage worker)

Files: new `app/runtime/central_config.py`, modify `app/runtime/plugin_store.py`, new focused central storage tests plus relevant Store tests.

- [ ] Write failing tests for projection, sparse defaults, explicit zero/false/empty, path-level compare/commit/rollback, shared mapping, untouched sections.
- [ ] Run the tests and confirm intended failures before implementation.
- [ ] Implement the interfaces above. Preserve existing Store behavior when no backend is injected.
- [ ] Write migration tests using real old defaults, customized values, missing/uninstalled sections, category/TMDB conflicts and repeated startup.
- [ ] Implement all-or-nothing migration, `config_version: 2`, safe error paths, retained original files and 0600 atomic writes.
- [ ] Run central tests and Store tests; report real counts and review risks.

## Task 2: Runtime snapshots, authenticated writes, Download (runtime worker)

Files: `app/runtime/plugin_supervisor.py`, `app/runtime/runtime_broker.py`, `app/runtime/plugin_manifest.py`, SDK host client/types, Download config store/runtime/manifest and focused runtime tests.

- [ ] Write failing tests proving per-instance snapshot isolation, no main path exposure, shadow/retired writer rejection, scoped Download patch and expected-value conflict.
- [ ] Implement optional `config` snapshot argument, authenticated identity lifecycle and minimal Host write/read RPC hook. Coordinate hook shape with lifecycle worker.
- [ ] Add Download manifest declaration `config_write_mode: host_rpc_v1`; extend manifest parser for the declaration while keeping other plugins compatible.
- [ ] Adapt Download config store to use a Host-backed synchronous adapter from worker threads in centralized mode; preserve existing local adapter for standalone tests/legacy mode.
- [ ] Preserve token refresh, direct/scan auth, directory/size changes, snapshots and cancellation semantics; every write uses field-level expected values.
- [ ] Add declared Host API requirement for the new Download protocol and verify manifest/runtime/SDK tests plus Download suite.

## Task 3: Central lifecycle transactions (lifecycle worker)

Files: `app/runtime/plugin_manager.py`, optional focused `app/runtime/config_transactions.py`, new integration tests.

- [ ] Read Tasks 1/2 interfaces and communicate any incompatible assumption before editing shared interfaces.
- [ ] Write failing tests for raw wizard patches preserving refreshed tokens, shared multi-consumer activation, one-consumer failure rollback, cancelled writes and latest-value-preserving code rollback.
- [ ] Add central configure_patch/configure_shared paths; legacy standalone Store paths remain supported.
- [ ] Route production install/update/startup/reload/rollback through effective projection and prepared snapshots; no premature central migration/default writes.
- [ ] Reject legacy Download activation before replacing a running compatible instance; startup keeps Host operable with an upgrade diagnostic.
- [ ] Connect Broker live Download writes to central expected-value patches and active config cache updates; reject shadow and retired identities.
- [ ] Run focused manager and central lifecycle tests; report real counts and integration hooks for root.

## Task 4: Host wiring, Telegram shared entry, complete templates (root)

Files: `app/115bot.py`, `app/init.py`, `app/handlers/config_handler.py`, `app/handlers/plugin_handler.py`, both config templates and related tests.

- [ ] Write failing Host wiring/Telegram tests against the new interfaces.
- [ ] Instantiate one central backend at `init.CONFIG_FILE`, migrate legacy values before processes start, inject it into Store and runtime writers.
- [ ] Forward raw config_patch to manager.configure_patch while preserving cancellation and operation reporting.
- [ ] Add clear shared route/TMDB editing entries to /config, retaining all installed Feature wizards and secret redaction.
- [ ] Assemble full annotated template with Host/plugins/shared/features sections and every existing active configuration field exactly once as a user-maintained value; keep both templates identical.
- [ ] Ensure manual /reload uses consistent central projection and truthful all-consumer reporting.
- [ ] Run targeted Host/template/config tests and integrate worker interfaces.

## Task 5: Documentation, versions, review and full verification (root)

- [ ] Update current README/README_EN/Feature docs for sole main YAML, legacy migration, Download upgrade floor, runtime snapshots and /config behavior. Do not alter historical plans/audits.
- [ ] Set consistent Host/SDK and actually changed Feature versions and requirements after interfaces settle; update associated contract tests intentionally.
- [ ] Obtain independent correctness review of source changes against baseline and approved spec, fix actionable findings, and rerun affected tests.
- [ ] Run Host and all five Feature suites using the prescribed Python, PYTHONDONTWRITEBYTECODE and no pytest cache. Caption validation adds temporary OpenCC dependency path if the runtime still lacks it.
- [ ] Check `.git` and `.worktrees` absent, `.stfolder` retained; list all changed files and actual results in delivery.
- [ ] Remind user to wait for Syncthing `Up to Date / 最新`; publication remains user-controlled on Unraid.

## Execution record

- Initial code cleanup completed: Rename full suite 518 passed, 26 subtests; Caption equivalent schema across 1,888 cases and full suite 500 passed, 6 skipped after providing declared OpenCC dependency in a temporary validation directory.
- Host cleanup regression: 53 passed, 1 skipped, 58 subtests.
- Independent cleanup review found no actionable issues. Generated source-tree build caches are ignored by Syncthing; clean temporary-source artifact check is used for deleted-module verification.
- User explicitly approved the written centralization design and requested continuation. Execute in this chat with parallel scoped workers; do not ask for another execution-method approval.
