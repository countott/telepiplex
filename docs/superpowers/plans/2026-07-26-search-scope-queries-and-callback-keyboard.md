# Search Scope Queries and Callback Keyboard Implementation Plan

> **Execution:** Run inline with `superpowers:test-driven-development` and
> verify with `superpowers:verification-before-completion`. Mac-local Git
> operations are prohibited by `AGENTS.md`.

**Goal:** Add the approved bounded series query variants, recognize textual
season numbers at the release gate, and retire stale Telegram callback
keyboards after successful progression.

**Architecture:** Generate query variants deterministically from the confirmed
series contract, keep Prowlarr categories as coarse filters, extend the
existing scope parser with textual season evidence, and enforce callback
keyboard cleanup in the Host action renderer.

**Tech Stack:** Python 3.12, asyncio, unittest, pytest, python-telegram-bot
22.3, Telepiplex Feature operation API.

## Global constraints

- Do not run Git or create Git metadata in the Mac workspace.
- Do not remove or bypass Prowlarr movie/TV categories.
- Do not weaken identity, year, media type, scope, or special-content gates.
- Do not add movie-collection search or broad query synonyms.
- Preserve current bounded concurrency and partial-failure behavior.

### Task 1: Lock query generation with failing tests

**Files:**
- Modify: `features/search/tests/test_feature_service.py`

- [ ] Prove episode scope emits only `Title SxxEyy`.
- [ ] Prove season scope emits `Title Sxx` and `Title Season xx`.
- [ ] Preserve the one-season whole-series three-query contract.
- [ ] Prove multi-season whole-series emits `Title Sfirst-Slast` and
  `Title Complete` with no bare title.
- [ ] Run the focused tests and confirm the new cases fail.

### Task 2: Lock textual season gating with failing tests

**Files:**
- Modify: `features/search/tests/test_release_gate.py`

- [ ] Accept `Season 02` and `Complete Season 02` for a season-2 request.
- [ ] Reject the same releases for a different requested season.
- [ ] Run the focused tests and confirm the new cases fail.

### Task 3: Lock callback keyboard cleanup with failing tests

**Files:**
- Modify: `tests/test_plugin_handler.py`

- [ ] Prove a successful callback `send_message` clears the source keyboard
  after sending the next message.
- [ ] Prove an `edit_message` without a new keyboard explicitly clears it.
- [ ] Prove an edit with a next-stage keyboard keeps that keyboard.
- [ ] Run the focused tests and confirm the missing cleanup cases fail.

### Task 4: Implement the minimal behavior

**Files:**
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/release_gate.py`
- Modify: `app/handlers/plugin_handler.py`

- [ ] Generate only the approved series query lists.
- [ ] Parse textual season-number markers into the existing season set.
- [ ] Resolve the clicked keyboard once per successfully rendered callback
  action sequence.
- [ ] Run all focused tests.

### Task 5: Release identity and documentation

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `features/search/README.md`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `tests/test_technical_identity_migration.py`

- [ ] Bump Search Feature from `1.0.7` to `1.0.8`.
- [ ] Document the complete approved query matrix and textual season gate.

### Task 6: Full verification and handoff

- [ ] Run all Search tests.
- [ ] Run root tests and every Feature test suite.
- [ ] Build and inspect `/tmp/search-1.0.8.tpx`.
- [ ] Confirm `.git` and `.worktrees` are absent and `.stfolder` exists.
- [ ] Hand off through Syncthing without publishing.

