# Runtime Log and Metadata Confirmation Hardening Implementation Plan

> **For agentic workers:** Execute inline in the current session. This Mac workspace forbids Git/worktrees, so every task ends with tests rather than commits.

**Goal:** Eliminate Feature stdout deadlocks, duplicate metadata planning, callback timeout inversion, oversized search responses, and per-file RPC amplification while retaining strict media/file verification.

**Architecture:** Bound diagnostics at the SDK producer and make Host collection impossible to abandon silently; freeze metadata resolutions in a durable search store; move rename confirmation behind an immediately returning callback; batch only pre-mutation file lookups and keep post-mutation checks fresh.

**Tech Stack:** Python 3.12, asyncio, SQLite, unittest/pytest, existing telepiplex Feature SDK and Unix-socket RPC.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`; never run Git or publish from the Mac.
- User-facing product prose stays lowercase `telepiplex`.
- Follow strict RED -> GREEN for each behavior.
- Do not edit generated `build/`, egg-info, bytecode, or cache artifacts.
- Use the bundled Python runtime and disable bytecode/pytest cache.

---

### Task 1: Bounded SDK diagnostics and resilient Host capture

**Files:**
- Modify: `sdk/src/telepiplex_plugin_sdk/diagnostics.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/logging_utils.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/host_client.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/runtime.py`
- Modify: `app/runtime/runtime_broker.py`
- Modify: `app/runtime/plugin_supervisor.py`
- Modify: `tests/test_plugin_sdk_runtime.py`
- Modify: `tests/test_plugin_supervisor.py`
- Modify: `tests/fixtures/plugin_processes/fake_python.py`

**Produces:** bounded diagnostic summaries, 32 KiB producer lines, chunked Host capture, capture-task fail-closed restart.

- [x] Add tests that emit 70 KiB and multi-megabyte lines and verify a later health call succeeds.
- [x] Run focused tests and observe the current `readline()`/line-size failure.
- [x] Add reusable bounded diagnostic summary and final transport guard.
- [x] Replace Host `readline()` capture with chunk splitting and safe oversize omission.
- [x] Add capture-task watchdog and run SDK/supervisor/runtime-broker suites green.

### Task 2: Durable one-plan search confirmation and compact response

**Files:**
- Create: `features/search/src/telepiplex_search/metadata_resolutions.py`
- Create: `features/search/tests/test_metadata_resolutions.py`
- Modify: `features/search/src/telepiplex_search/runtime.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/media_metadata_v1.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/tests/test_regression_pressure.py`

**Produces:** `resolution_id` contract, durable idempotent resolution store, confirm path with zero planner calls, deduplicated result.

- [x] Add failing tests for planner call count, restart replay, expiry, invalid ref and response duplication.
- [x] Run focused tests and confirm current confirmation calls the planner twice.
- [x] Implement SQLite store with TTL/max-entry pruning and resolved-result caching.
- [x] Split resolve/confirm branches before planning and consume only frozen records.
- [x] Remove duplicate top-level evidence/source queries and compact unused evidence arrays.
- [x] Run complete search capability and pressure suites green.

### Task 3: Background rename confirmation with durable cancellation

**Files:**
- Modify: `features/rename/src/telepiplex_rename/jobs.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `tests/test_operation_pipeline_e2e.py`

**Produces:** immediately returning callback, `resolving_metadata` durable state, resumable/idempotent background worker and clear error/cancel states.

- [x] Add a blocked-search regression proving callback and cancel remain responsive.
- [x] Run it and observe current callback blocks.
- [x] Split selection validation from background completion and persist the transition before spawn.
- [x] Add restart resume and deterministic/transient error handling tests.
- [x] Run rename metadata, operation and end-to-end pipeline suites green.

### Task 4: Bounded storage batch lookup

**Files:**
- Modify: `features/download/src/telepiplex_download/client.py`
- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/download/tests/test_feature_runtime.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/src/telepiplex_rename/file_executor.py`
- Modify: `features/rename/tests/test_file_executor.py`
- Modify: `features/rename/tests/test_regression_pressure.py`

**Produces:** 128-path provider batch, mutation-aware StorageProxy cache, batched initial planning/execution reads and fresh final verification.

- [x] Add failing batch-contract and 65-file RPC-count tests.
- [x] Implement provider batch and compatibility fallback.
- [x] Add StorageProxy prefetch/cache invalidation and use it in processor/executor.
- [x] Verify move/rename target checks are never served from a pre-mutation cache.
- [x] Run download storage and rename execution/pressure suites green.

### Task 5: Release identities, full pressure and package verification

**Files:**
- Modify: `app/115bot.py`
- Modify: `sdk/pyproject.toml`
- Modify: `features/download/manifest.yaml`, `features/download/pyproject.toml`, `features/download/README.md`
- Modify: `features/search/manifest.yaml`, `features/search/pyproject.toml`, `features/search/README.md`
- Modify: `features/rename/manifest.yaml`, `features/rename/pyproject.toml`, `features/rename/README.md`
- Modify: current-version contract tests only.

**Produces:** Host `v3.5.4-host`, SDK `1.3.2`, download `1.0.15`, search `1.11.2`, rename `1.5.3`.

- [x] Update focused version expectations first and observe failures.
- [x] Align authoritative source version files and current README examples.
- [x] Run new stress tests, then Core and all five Feature suites.
- [x] Build download/search/rename packages in `/tmp`, run `unzip -t`, and inspect embedded manifests.
- [x] Verify `.git`/`.worktrees` are absent and `.stfolder` exists; list every changed file and hand off via Syncthing.
