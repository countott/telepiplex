# Open115 Native Batch Move Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move rename outputs with the official 115 server-side batch move API and verify every moved file without copy-delete amplification.

**Architecture:** Download exposes a bounded `move_files_by_id` storage method backed by `/open/ufile/move`. Rename prepares files independently, groups them by target directory, submits ID chunks, and reconciles fresh source/target directory listings.

**Tech Stack:** Python 3.12, requests, pytest/unittest, telepiplex cross-Feature storage capability.

**Spec:** `docs/superpowers/specs/2026-08-18-rename-partial-resolution-and-native-move-design.md`

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`.
- Do not run Git, create `.git`/`.worktrees`, publish, or connect to GitHub.
- Use only the public 115 OpenAPI endpoint `/open/ufile/move`.
- Do not weaken provider-ID, target-conflict, source-absence, or cleanup verification.
- The default native move chunk contains 32 IDs; configuration allows 1 through 100.

---

### Task 1: Download native move capability

**Files:**
- Modify: `features/download/src/telepiplex_download/client.py`
- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/download/tests/test_client_move_safety.py`
- Modify: `features/download/tests/test_feature_runtime.py`

**Interfaces:**
- Produces: `Open115Client.move_files_by_id(file_ids: list[str], target_dir_id: str) -> dict`.
- Produces: whitelisted `storage.provider.move_files_by_id` with at most 100 unique IDs.

- [x] Add a failing client test asserting one POST to `/open/ufile/move` with comma-separated `file_ids` and `to_cid`, and no copy/delete call.
- [x] Add failing input-boundary and storage-capability tests.
- [x] Run tests and confirm the method/whitelist are missing.
- [x] Implement validation, deduplication, structured result, and cache invalidation.
- [x] Run focused Download tests until green.

### Task 2: Rename grouped native execution

**Files:**
- Modify: `features/rename/src/telepiplex_rename/file_executor.py`
- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/config.default.yaml`
- Modify: `features/rename/config.schema.json`
- Modify: `features/rename/tests/test_file_executor.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `features/rename/tests/test_config_schema_contract.py`

**Interfaces:**
- `execute_file_resolutions(..., move_batch_size: int = 32) -> FileExecutionSummary`.
- StorageProxy exposes `move_files_by_id` as an irreversible moving stage.

- [x] Add a failing 65-file test proving three native calls of 32/32/1 for one target directory and zero legacy moves.
- [x] Add failing tests for target/source listing verification, provider-failure reconciliation, and incompatible-provider fallback.
- [x] Run focused executor tests and confirm execution remains per-file.
- [x] Refactor preparation from mutation, group prepared moves, and add bounded native calls.
- [x] Verify target name/provider ID and source provider-ID absence from fresh paginated directory listings.
- [x] Pass configured `storage_move_batch_size` and run focused Rename tests until green.

### Task 3: Documentation, version and verification

**Files:**
- Modify: `features/download/manifest.yaml`
- Modify: `features/download/pyproject.toml`
- Modify: `features/download/README.md`
- Modify: Download version tests.
- Modify: `features/rename/README.md`

**Interfaces:**
- Produces: aligned download `1.0.17` source release identity.

- [x] Update maintained version references and document native move/fallback semantics.
- [x] Run focused P1 tests and the complete Download/rename Feature suites.
- [x] Build `/tmp/download-1.0.17.tpx` and verify the archive.
- [x] Run the repository-wide test command from `AGENTS.md` after both P0 and P1 are green.
- [x] Verify `.git` and `.worktrees` are absent and `.stfolder` is present.
