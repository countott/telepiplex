# Rename Convergence Without Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make rename converge and finish locally without any automatic sync/Plex integration.

**Architecture:** Keep storage access behind `storage.provider`, but bound each batch and verify every mutation postcondition. Rename owns its terminal state; sync remains a separate manual Feature. Rename metadata reconciles existing inventory coordinates independently from search/download aired filtering.

**Tech Stack:** Python 3.12, asyncio, pytest, telepiplex Feature Runtime and SDK.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`.
- Do not run Git or create `.git`/`.worktrees`.
- Product-facing copy uses lowercase `telepiplex`.
- Do not publish or trigger Plex/sync automatically.
- Run focused tests only; omit the large pressure matrix and full repository suite.

---

### Task 1: Remove automatic rename to sync/Plex integration

**Files:**
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `features/sync/manifest.yaml`
- Modify: `features/sync/src/telepiplex_sync/runtime.py`
- Modify: `features/sync/tests/test_feature_runtime.py`

**Interfaces:**
- Consumes: rename durable job and Host operation reporting.
- Produces: rename-local `completed`/`failed`; sync manual-only runtime surface.

- [x] Write focused failing tests that a successful rename publishes no event,
  names no next plugin, and that sync registers no `media.organized` handler.
- [x] Run those exact tests and confirm failures are caused by current automatic
  handoff behavior.
- [x] Remove the manifest/runtime coupling and make `_finish_operation` complete
  locally after verified cleanup.
- [x] Remove inventory verified-group publication and fail an inventory operation
  when any work group/file/cleanup fails.
- [x] Run the focused tests until green.

### Task 2: Bound storage lookup and preserve fingerprints

**Files:**
- Modify: `features/download/src/telepiplex_download/client.py`
- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/download/tests/test_feature_runtime.py`
- Test: `features/download/tests/test_client_move_safety.py`

**Interfaces:**
- Consumes: `get_file_info_batch(paths: list[str])` and `get_file_tree(root)`.
- Produces: deduplicated file-info mappings, at most 32 direct lookups per RPC,
  and snapshot nodes with `sha1`.

- [x] Write failing tests for 33-path rejection/chunk boundary and snapshot SHA1.
- [x] Run them and verify the intended failures.
- [x] Set the provider batch limit to 32, preserve cache/dedup behavior, and add
  SHA1 to file-tree nodes.
- [x] Run the focused download tests until green.

### Task 3: Make file execution and source cleanup converge

**Files:**
- Modify: `features/rename/src/telepiplex_rename/file_executor.py`
- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/tests/test_file_executor.py`
- Modify: `features/rename/tests/test_file_first_processor.py`

**Interfaces:**
- Consumes: provider ID, SHA1/size snapshot facts, fresh storage lookups.
- Produces: verified `organized`, `no_op`, or `failed` outcomes and an absence-
  verified directory cleanup summary.

- [x] Write failing tests for same-SHA1 retained-source recovery, source absence
  verification, manual selected-root deletion, and post-delete directory checks.
- [x] Run them and verify the current behavior fails each contract.
- [x] Implement fingerprint comparison, retained-source recovery, fresh source
  and target verification, and directory absence verification.
- [x] Change manual inventory cleanup to include the selected work-group root
  while protecting only category/library roots.
- [x] Run the focused rename executor/processor tests until green.

### Task 4: Separate rename inventory scope from aired release scope

**Files:**
- Modify: `features/search/src/telepiplex_search/series_scope.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/search_plan.py`
- Modify: `features/search/tests/test_series_scope.py`
- Modify: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Consumes: confirmed provider inventory plus file-probe season/episode facts.
- Produces: `apply_inventory_probe_scope(contract, probe)` selecting exact
  observed coordinates without air-date filtering and detailed validation errors.

- [x] Write a failing Honey fixture test for seasons 1/2, 38 items, missing air
  dates, and Chinese title preservation.
- [x] Write a failing detailed-error assertion for a missing probe coordinate.
- [x] Run both tests and verify the current aired scope/generic validation fails.
- [x] Add inventory-probe reconciliation and use it only in rename metadata
  resolution; keep `apply_series_scope` unchanged for search/download.
- [x] Change confirmation to raise the detailed validation path/reason.
- [x] Run the focused search tests until green.

### Task 5: Documentation, versions, and focused verification

**Files:**
- Modify: `features/rename/README.md`
- Modify: `features/search/README.md`
- Modify: `features/sync/README.md`
- Modify version manifests/metadata only for Features whose source changed.

**Interfaces:**
- Consumes: completed behavior from Tasks 1-4.
- Produces: accurate product documentation and releasable local Feature metadata.

- [x] Update current documentation and aligned Feature versions without editing
  historical specifications.
- [x] Run only the focused test files changed by Tasks 1-4.
- [x] Run Python syntax parsing for changed source modules.
- [x] Verify `.git` and `.worktrees` are absent and `.stfolder` exists.
- [x] Report changed files, actual command results, and the Syncthing handoff.
