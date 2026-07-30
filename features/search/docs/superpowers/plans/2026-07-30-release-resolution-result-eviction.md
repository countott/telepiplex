# search Release Resolution Result Eviction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan inline. This Mac
> workspace forbids Git, so commit steps are replaced by local red-green and
> verification checkpoints.

**Goal:** Remove only the selected Prowlarr result when its download content
cannot be resolved, then return the user to the remaining release list.

**Architecture:** Keep resolution failure recovery inside `SearchFeature`.
Mutate only the request-scoped `results` and `release_by_id`, render the
remaining list through the existing report/keyboard functions, and let the
submission task transition back to `awaiting_input`.

**Tech Stack:** Python 3.12, asyncio, pytest, telepiplex Feature SDK.

## Global Constraints

- Product-facing text uses lowercase `telepiplex`.
- Do not change Prowlarr retrieval, release scoring, canonical metadata or
  download `/m`.
- Do not run Git or publish from the Mac workspace.

---

### Task 1: Add release-resolution recovery regressions

**Files:**
- Test: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Exercises `SearchFeature._start_submission_task(...)` and the real
  request-scoped release state.

- [x] Add a test with two visible results where the first resolver call raises.
- [x] Assert the failed ID is removed from `results` and `release_by_id`.
- [x] Assert the operation returns to `awaiting_input/release_selection` with
  the second result and exit button.
- [x] Add a test where the only result returns no magnet.
- [x] Assert the empty state contains only the exit button.
- [x] Run both tests and confirm they fail because the current implementation
  marks the operation failed and retains the selected result.

### Task 2: Implement selected-result eviction

**Files:**
- Modify: `features/search/src/telepiplex_search/service.py`

**Interfaces:**
- Produces a request-scoped recovery result consumed by
  `_submission_task(...)`.

- [x] Add a helper that removes one `release_id`, clears frozen selection
  state and renders the remaining list.
- [x] Route resolver exceptions and non-magnet results through the helper.
- [x] Make `_submission_task(...)` report the recovery as
  `awaiting_input/release_selection` instead of `failed`.
- [x] Record a sanitized structured removal log.
- [x] Run the focused tests to GREEN.

### Task 3: Verify search

**Files:**
- Verify all files changed above.

- [x] Run the complete search Feature test suite.
- [x] Compile the modified Python module.
- [x] Build `/tmp/search-1.2.0.tpx` and validate the archive.
- [x] Confirm `.git` and `.worktrees` are absent and `.stfolder` exists.
