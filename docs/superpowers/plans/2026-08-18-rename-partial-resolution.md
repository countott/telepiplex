# Rename Partial Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organize every uniquely matched episode while retaining and explaining ambiguous files without failing the work group.

**Architecture:** Search returns the canonical intersection plus structured unresolved inventory evidence. Rename reuses file-first `keep_original` outcomes, classifies the durable result as complete or partial, and invokes one explanation-only AI request after deterministic execution.

**Tech Stack:** Python 3.12, pytest/unittest, telepiplex Feature SDK, SQLite rename job store.

**Spec:** `docs/superpowers/specs/2026-08-18-rename-partial-resolution-and-native-move-design.md`

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`.
- Do not run Git, create `.git`/`.worktrees`, publish, or trigger sync/Plex.
- Product-facing copy uses lowercase `telepiplex`.
- AI may explain ambiguity but may not create or change a file mapping.
- Existing strict search/download series scope remains unchanged.

---

### Task 1: Partial inventory reconciliation

**Files:**
- Modify: `features/search/src/telepiplex_search/series_scope.py`
- Modify: `features/search/tests/test_series_scope.py`
- Modify: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Consumes: `apply_inventory_probe_scope(contract: dict, probe: dict) -> dict`.
- Produces: `evidence.inventory_reconciliation` with literal matched/unresolved coordinates and reason codes.

- [x] Add a failing Honey fixture with canonical S1=24/S2=12 and observed S1=26/S2=12; assert 36 selected items and two unresolved coordinates.
- [x] Add a failing zero-intersection test; assert it still raises `probe_inventory_mismatch missing=all`.
- [x] Run the focused tests and confirm failure is the existing whole-group exception.
- [x] Implement matched/unresolved partitioning without changing `apply_series_scope`.
- [x] Run focused Search scope and capability tests until green.

### Task 2: Explanation-only AI result

**Files:**
- Modify: `features/rename/src/telepiplex_rename/ai.py`
- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/tests/test_ai_structured_output.py`
- Modify: `features/rename/tests/test_file_first_processor.py`

**Interfaces:**
- Produces: `explain_unresolved_episode_files_with_ai(context: dict) -> dict | None`.
- Produces: `file_results.ambiguity_explanation` and `file_results.unresolved_files`.

- [x] Add a failing structured-output test that accepts only summary/causes/checks and strips any mapping-shaped fields.
- [x] Add a failing processor test proving two unmatched videos stay in place and the AI function is called exactly once after 36 deterministic resolutions.
- [x] Run both tests and confirm the explanation API/result fields are missing.
- [x] Implement the bounded prompt, validation, deterministic fallback, and user-facing unresolved list.
- [x] Re-run the focused AI and processor tests until green.

### Task 3: Partial durable result semantics

**Files:**
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/src/telepiplex_rename/jobs.py`
- Modify: `features/rename/tests/test_feature_processor.py`

**Interfaces:**
- Produces: durable job state `partial_completed`.
- Produces: Host terminal report `state=completed`, `stage=partial_completed`, and `details.completion_kind=partial_completed`.
- Produces: capability result `partial_completed: true`, `complete: false`.

- [x] Replace the former partial-accounting failure expectation with a failing partial-success expectation.
- [x] Add replay and inventory-batch tests for `partial_completed`.
- [x] Run focused service tests and confirm current code records `failed`.
- [x] Implement safe accounting where verified + kept equals total and hard failures remain zero.
- [x] Update terminal/replay/inventory paths and run focused tests until green.

### Task 4: Documentation, version and verification

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Modify: `features/search/src/telepiplex_search/adapters/wikidata.py`
- Modify: Search version tests.
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/pyproject.toml`
- Modify: `features/rename/README.md`
- Modify: Rename version tests.

**Interfaces:**
- Produces: aligned search `1.11.4` and rename `1.5.6` source release identities.

- [x] Update maintained version references and README behavior descriptions.
- [x] Run focused P0 tests.
- [x] Run complete Search and Rename Feature suites.
- [x] Build `/tmp/search-1.11.4.tpx` and `/tmp/rename-1.5.6.tpx`, then verify both archives.
- [x] Verify `.git` and `.worktrees` are absent and `.stfolder` is present.
