# Dual Diagnostic Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create one log-session directory per Host start containing lossless machine JSONL and information-complete Chinese human logs, with global and per-Feature views sharing event identities.

**Architecture:** `telepiplex_plugin_sdk.diagnostics` owns the canonical event, context propagation, schema, sanitization metadata, renderers and feature transport. Host logging owns session-directory lifecycle and fans each canonical event into global and optional per-Feature dual files. RPC and Telegram boundaries bind correlation data so foreground error references and backend exceptions share one incident identity.

**Tech Stack:** Python 3.12, standard `logging`, `contextvars`, JSONL, JSON Schema artifact, pytest/unittest.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`; do not run Git or create `.git`/`.worktrees`.
- Product-facing text uses lowercase `telepiplex`.
- Every Host start creates one new directory below `/config/logs/sessions`.
- Human and machine logs, including Feature-classified views, live in that same session directory.
- Retain the newest 30 sessions and sessions no older than 30 days.
- Redact before every persistent or stdout output.
- Use tests first and observe each new behavior fail before implementation.

---

### Task 1: Canonical diagnostic event and renderers

**Files:**
- Create: `sdk/src/telepiplex_plugin_sdk/diagnostics.py`
- Create: `sdk/src/telepiplex_plugin_sdk/diagnostic-event-v1.schema.json`
- Modify: `sdk/src/telepiplex_plugin_sdk/log_sanitizer.py`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Produces `DiagnosticContext`, `bind_diagnostic_context(...)`, `current_diagnostic_context()`, `build_diagnostic_event(...)`, `render_machine_event(...)`, and `render_human_event(...)`.
- A canonical event carries stable IDs, timing, runtime identity, structured facts, error chain and privacy metadata.

- [ ] Write failing tests using literal JSON and Chinese output expectations for stable schema, populated-field preservation, exception chains and nested redaction.
- [ ] Run `pytest -q tests/test_diagnostics.py` and verify failure because the module and schema do not exist.
- [ ] Implement the smallest canonical model, recursive sanitizer, machine renderer and exhaustive human renderer.
- [ ] Run the focused tests and verify green.
- [ ] Add a failing test for oversized sanitized text chunk reconstruction, then implement ordered `payload.chunk` output and verify green.

### Task 2: Startup session directory and dual file handlers

**Files:**
- Modify: `app/utils/logger.py`
- Modify: `app/init.py`
- Test: `tests/test_logger.py`
- Modify: `tests/test_technical_identity_migration.py`

**Interfaces:**
- Produces `LogSession`, `create_log_session(config_root, now=None, session_id=None)`, `current_log_session()`, and dual logging handlers.
- Session paths are `/config/logs/sessions/<local timestamp>-<session_id>/telepiplex.{human.log,machine.jsonl}`.

- [ ] Write failing real-filesystem tests for unique startup directories, same-directory dual files, and no append into a previous session.
- [ ] Run focused tests and confirm current fixed `telepiplex.log` behavior fails them.
- [ ] Implement session creation and root dual handlers; keep Docker stdout human-readable.
- [ ] Add failing folder-retention tests for 31 recent sessions and a session older than 30 days.
- [ ] Implement explicit child-directory cleanup and verify both count and age behavior.
- [ ] Verify logger reconfiguration changes levels without creating another session.

### Task 3: Feature transport and same-folder classification

**Files:**
- Modify: `sdk/src/telepiplex_plugin_sdk/logging_utils.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/runtime.py`
- Modify: `app/runtime/plugin_supervisor.py`
- Test: `tests/test_plugin_supervisor.py`
- Test: `tests/test_plugin_sdk_runtime.py`
- Test: `tests/test_plugin_runtime_e2e.py`

**Interfaces:**
- Feature transport prefix is `@tpx-event-v1 ` followed by one JSON object.
- Host writes `feature-<plugin_id>.human.log` and `feature-<plugin_id>.machine.jsonl` beside global files and preserves source `event_id`.

- [ ] Write failing process-level tests proving both Feature files are in the Host session directory and share event IDs with global JSONL.
- [ ] Verify red because current supervisor writes `state/logs/runtime.log`.
- [ ] Implement SDK transport, Host ingestion, Feature file fan-out and raw stdout/stderr wrapping.
- [ ] Run focused supervisor, SDK runtime and E2E tests to green.
- [ ] Add malformed transport and sequence-gap tests, then implement safe fallback events.

### Task 4: Cross-boundary correlation and foreground incidents

**Files:**
- Modify: `app/runtime/plugin_rpc.py`
- Modify: `app/runtime/runtime_broker.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/host_client.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/runtime.py`
- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/115bot.py`
- Test: `tests/test_plugin_rpc.py`
- Test: `tests/test_runtime_broker.py`
- Test: `tests/test_plugin_sdk_runtime.py`
- Test: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- RPC envelopes gain backward-compatible `diagnostics` containing trace/span/operation/incident identity.
- `telepiplex_error_handler(update, context)` logs the exception and sends a sanitized message containing the same `incident_id`.

- [ ] Write failing RPC tests proving trace and parent span survive Host↔Feature boundaries.
- [ ] Implement envelope injection and scoped server binding; verify focused RPC tests.
- [ ] Write a failing Telegram error test that compares the frontend problem number with the JSONL incident ID.
- [ ] Implement the top-level error handler and register it on the Application.
- [ ] Add user-surface diagnostic events at operation milestone delivery and callback answer boundaries.

### Task 5: Structured lifecycle, operation and provider evidence

**Files:**
- Modify: `app/runtime/plugin_supervisor.py`
- Modify: `app/runtime/runtime_broker.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/logging_utils.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/sync/src/telepiplex_sync/sync_service.py`
- Test: `features/search/tests/test_feature_service.py`
- Test: `features/download/tests/test_feature_runtime.py`
- Test: `features/rename/tests/test_feature_processor.py`
- Test: `features/sync/tests/test_feature_runtime.py`

**Interfaces:**
- Structured `extra={"event_name": ..., "diagnostic_fields": ...}` feeds the canonical event without parsing prose.

- [ ] Add failing assertions for dispatch duration, retry/ownership fields and source outcome fields in machine JSONL.
- [ ] Convert common lifecycle/RPC/dispatch boundaries to structured events.
- [ ] Preserve existing human messages while supplying typed machine fields.
- [ ] Run affected Feature suites after each boundary conversion.

### Task 6: Version, packaging and documentation

**Files:**
- Modify: `app/115bot.py`
- Modify: `sdk/pyproject.toml`
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/search/src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `features/download/manifest.yaml`
- Modify: `features/download/pyproject.toml`
- Modify: `features/download/README.md`
- Modify: `features/download/src/telepiplex_download.egg-info/PKG-INFO`
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/pyproject.toml`
- Modify: `features/rename/README.md`
- Modify: `features/rename/src/telepiplex_rename.egg-info/PKG-INFO`
- Modify: `features/sync/manifest.yaml`
- Modify: `features/sync/pyproject.toml`
- Modify: `features/sync/README.md`
- Modify: `features/sync/src/telepiplex_sync.egg-info/PKG-INFO`
- Modify: `features/caption/manifest.yaml`
- Modify: `features/caption/pyproject.toml`
- Modify: `features/caption/README.md`
- Modify: `features/caption/src/telepiplex_caption.egg-info/PKG-INFO`
- Modify: `README.md`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_technical_identity_migration.py`
- Modify: `tests/test_unraid_publish_script.py`

**Interfaces:**
- Host `v3.5.0-host`, SDK `1.3.0`, search `1.9.7`, download `1.0.12`, rename `1.4.5`, sync `1.1.1`, caption `0.1.3`.

- [ ] Update test expectations first and verify they fail on old versions and paths.
- [ ] Update versions, exact SDK pins and documentation.
- [ ] Build all Feature packages into a temporary directory and validate archives without leaving artifacts in the workspace.

### Task 7: Full verification and pressure

**Files:**
- Modify: `tools/pressure_operation_pipeline.py`
- Test: `tests/test_pressure_operation_pipeline.py`

**Interfaces:**
- Pressure result must report zero business failures while injected milestone failures remain recoverable.

- [ ] Run Host and all five Feature suites with bundled Python 3.12.
- [ ] Run a real startup-session smoke test and validate every JSONL line against the schema.
- [ ] Run 200 pipelines at concurrency 32 with milestone fault injection and verify zero failures.
- [ ] Verify no secret fixture appears in human, machine or Docker-capture outputs.
- [ ] Verify `.git` and `.worktrees` are absent and `.stfolder` is present without invoking Git.
