# rename Inventory Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Telegram `/rename` entry that scans one selected 115 category or unorganized directory and serially reuses the existing metadata-backed rename pipeline for direct children.

**Architecture:** A focused inventory helper classifies direct children and builds stable jobs. `RenameFeature` owns the Telegram session and batch orchestration, while each item continues through the existing organization method with an inventory flag that prevents the child from terminating the parent operation.

**Tech Stack:** Python 3.12, telepiplex Feature SDK, SQLite job store, pytest/unittest.

## Global Constraints

- Do not modify Search scope, whole-series, season-pack, or episode identification behavior.
- Do not modify Host poster fallback behavior.
- Scan only direct children of the selected root and require one confirmation before writes.
- Read every direct child folder's complete descendant tree with paginated directory listing, without the provider tree helper's depth or total-node cap.
- Classify only by the current rename target structure: matching is completed and every non-matching child is incomplete.
- Execute inventory items serially and preserve stable per-item idempotency.
- Do not run Git or publish from the Mac workspace.

---

### Task 1: Inventory classification and configuration contract

**Files:**
- Create: `features/rename/src/telepiplex_rename/inventory.py`
- Create: `features/rename/tests/test_inventory.py`
- Modify: `features/rename/config.default.yaml`
- Modify: `features/rename/config.schema.json`
- Modify: `features/rename/tests/test_config_schema_contract.py`

**Interfaces:**
- Produces: `inventory_job_id(item, source_path) -> str`.
- Produces: `contains_video(file_tree) -> bool`.
- Produces: `looks_organized_release(root_name, file_tree) -> bool`.

- [x] Write literal unit tests for stable ID, video detection, normalized series, normalized movie, and raw release rejection.
- [x] Run the new tests and confirm the helper import fails.
- [x] Implement the minimal pure helper and make the tests pass.
- [x] Add four canonical `category_folder` defaults and schema validation, then run config contract tests.

### Task 2: Telegram scan menu and read-only preview

**Files:**
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/src/telepiplex_rename/runtime.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/tests/test_feature_processor.py`

**Interfaces:**
- `/rename` returns category plus unorganized buttons.
- `rename:inventory:root:<index>` starts a background direct-child scan.
- The scan reports `inventory_confirmation` with counts and confirm/cancel buttons.

- [x] Add an integration test for menu contents and direct-child scan classification.
- [x] Run it and confirm `/rename` is currently rejected.
- [x] Implement session state, storage reads, background scan, preview reporting, and cancellation.
- [x] Re-run the focused integration test.

### Task 3: Serial metadata-backed inventory execution

**Files:**
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/src/telepiplex_rename/jobs.py`
- Modify: `features/rename/tests/test_feature_processor.py`

**Interfaces:**
- `rename:inventory:confirm` starts serial execution.
- Inventory payloads carry `_inventory_batch_id`, `_inventory_index`, `_inventory_source_kind`, and stable `inventory:<file_id>` job IDs.
- Unorganized items derive `selected_path` from confirmed `placement.category_kind`.

- [x] Add a test that an unorganized movie is routed to its configured category and emits `media.organized`.
- [x] Run it and confirm no inventory confirmation handler exists.
- [x] Implement retryable stable job claims, batch execution, per-item result accounting, and final summary.
- [x] Add and run an ambiguity test proving the batch pauses and resumes before continuing.

### Task 4: Verification and documentation

**Files:**
- Modify: `features/rename/README.md`

**Interfaces:** `/rename` appears in the Feature command menu and README; `/rename_config` remains available but hidden. The immutable Feature identity is `rename@1.2.0`.

- [x] Run the entire rename Feature test suite.
- [x] Run Host command catalog and artifact/manifest contract tests affected by the new command.
- [x] Verify `.git` and `.worktrees` remain absent and `.stfolder` remains present.

### Task 5: Structure-authoritative inventory correction

**Files:**
- Modify: `features/rename/src/telepiplex_rename/inventory.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/src/telepiplex_rename/jobs.py`
- Modify: `features/rename/tests/test_inventory.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `features/rename/README.md`

**Interfaces:**
- `_inventory_file_tree(child, source_path) -> list[dict]` paginates and traverses the complete descendant tree, rejecting non-advancing pagination or directory cycles.
- The scan preview exposes only `pending` and `completed` counts.
- `RenameJobStore.claim_retryable(job_id, reopen_completed=True)` reopens a structurally incomplete historical job only for inventory execution.

- [x] Write literal tests for target-safe normalized paths, exact series depth and matching season numbers.
- [x] Write an integration test proving every direct child folder is read as a descendant tree, `/未整理` uses the same structural classifier, no-video remains incomplete, and historical Job success does not override current structure.
- [x] Run the focused tests and confirm they fail against the four-bucket/history-authoritative implementation.
- [x] Implement paginated full-tree traversal, binary structure classification, and completed-Job reopening.
- [x] Update the user-facing rename rules and rerun the complete rename and related Host/artifact checks.

### Task 6: Root-video wrapping and portable target components

**Files:**
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/src/telepiplex_rename/media_naming.py`
- Modify: `features/rename/tests/test_inventory.py`
- Modify: `features/rename/tests/test_media_auto_rename.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `features/rename/README.md`

**Interfaces:**
- A video directly under the selected root is always incomplete because the required release folder is absent.
- Inventory execution creates the metadata-derived release folder, renames the video, and moves it into the folder.
- `sanitize_target_name` removes control/forbidden characters and trailing spaces/dots, normalizes NFC, and guards Windows reserved device names.

- [x] Add a classifier regression test proving a root video cannot be completed by filename alone.
- [x] Add a failing end-to-end test for wrapping a root video and cleaning a reserved Windows target name.
- [x] Make the directory requirement explicit in the scan classifier and extend target-only portability sanitization.
- [x] Document the full structure and cross-platform target naming rules.
- [x] Run the complete rename and related Host/artifact verification.
