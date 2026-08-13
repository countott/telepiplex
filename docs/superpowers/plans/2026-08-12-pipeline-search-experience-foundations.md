# Pipeline and Search Experience Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve one Telegram message per Feature stage, deliver a unique identity card before release search, and make verified season searches use and display release-friendly queries.

**Architecture:** The Host owns Telegram delivery, acknowledgement, poster rendering, and operation-message generations. Search owns the canonical season contract, final release queries, and user-facing query copy. Download and Rename report complete stage text; ownership changes make the Host allocate the next Feature a new mutable message while preserving the prior Feature result.

**Tech Stack:** Python 3.12, asyncio, SQLite, python-telegram-bot, Pillow, pytest/unittest.

## Global Constraints

- Product-facing copy uses lowercase `telepiplex` where the product name appears.
- Do not implement Douban multi-season aggregation or infer whole-series completeness from Douban result counts.
- Do not change versions, release workflows, or configuration templates.
- Do not run Git or create `.git`/`.worktrees` on the Mac.
- Use `apply_patch` for file edits.
- Every production change follows a witnessed failing test, minimal implementation, and passing regression run.
- Complete work is handed to `/mnt/user/archives/life hacker/telepiplex` only through Syncthing after it reports `Up to Date / 最新`.

---

### Task 1: Deliver Confirmed Identity Before Release Search

**Files:**
- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/115bot.py`
- Modify: `tests/test_interaction_handler.py`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/tests/test_feature_service.py`

**Interfaces:**
- `OperationMilestoneSink.attach(delivery)` installs an async Host delivery callback.
- The callback receives `(chat_id: int, photo_url: str | None, text: str)` and returns only after Telegram accepts a photo, generated placeholder, or text fallback.
- Search starts release discovery only after `publish_operation_milestone` returns `accepted=True` or `duplicate=True`.

- [x] Add Host tests proving a one-item local poster card is sent, remote failure retries with the existing title placeholder, and final media failure sends text.
- [x] Run the focused Host tests and witness RED because milestones currently acknowledge queue insertion.
- [x] Make the milestone sink await its attached delivery; configure it with the running Telegram application and reuse `build_poster_grid` for the one-item card and placeholder retry.
- [x] Add Search tests proving release lookup does not begin before the milestone awaitable completes and that a rejected milestone is not marked delivered.
- [x] Run focused Host and Search tests and verify GREEN.

### Task 2: Produce and Expose Consistent Season Queries

**Files:**
- Modify: `features/search/src/telepiplex_search/discovery_flow.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/release_report.py`
- Modify: `features/search/tests/test_unified_search_pipeline.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/tests/test_release_report.py`
- Modify: `features/search/tests/test_release_gate.py`

**Interfaces:**
- A selected provider fact explicitly named `Season N` or `第 N 季` becomes a proposed `season` binding; TVDB inventory must verify it before confirmation.
- A verified season contract has root English identity `Veep`, `retrieval.scope=season`, and `season_number=1`.
- Release queries are `Veep S01` and `Veep Season 01`.
- User status/report copy contains `搜索词：Veep S01 / Veep Season 01` and contains no backend service name.

- [x] Add a failing selected-season pipeline test covering the literal contract and query list.
- [x] Run it and witness RED with `Veep Season 1` plus `whole_series`.
- [x] Add deterministic season-title decomposition at candidate binding and retain the authoritative root identity from TVDB during hydration.
- [x] Add failing report/status tests for literal final query copy.
- [x] Render all actually executed queries in search progress and final/no-result output without a backend label.
- [x] Add release-gate coverage proving `Veep.S01...` passes identity and season scope with the corrected contract.
- [x] Run focused Search tests and verify GREEN.

### Task 3: Split Download and Rename Operation Messages

**Files:**
- Modify: `app/runtime/interaction_coordinator.py`
- Modify: `app/handlers/interaction_handler.py`
- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/download/tests/test_feature_service.py`
- Modify: `tests/test_interaction_coordinator.py`
- Modify: `tests/test_interaction_handler.py`
- Modify: `tests/test_operation_pipeline_e2e.py`

**Interfaces:**
- An accepted ownership change clears the stored operation `message_id` and `message_kind` before the new owner renders.
- A handed-off Download status freezes as `✅ 115 下载完成` plus the complete save path.
- Operation report acknowledgement waits for the handed-off status to render before the event can reach Rename.
- Rename's first accepted report creates a new message; later Rename reports edit that message.

- [x] Add coordinator RED coverage proving an ownership change currently retains the old message binding.
- [x] Clear the message binding atomically when ownership changes and verify GREEN.
- [x] Add sink RED coverage proving a handoff RPC returns before its renderer completes.
- [x] Make operation report notification awaitable and serialized per operation; verify GREEN.
- [x] Add Download RED coverage for the complete frozen handoff text and absence of a duplicate completion notification.
- [x] Report complete Download text before event publication and remove the normal-path duplicate notification.
- [x] Add end-to-end coverage proving Download and Rename receive different message IDs and Rename edits only its own message.
- [x] Run focused pipeline tests and verify GREEN.

### Task 4: Regression Verification and Local Handoff

**Files:**
- Verify all modified source and test files.

**Interfaces:**
- Produces fresh test evidence for Core and the Search, Download, and Rename Features.

- [x] Run the focused regression files for all three tasks.
- [x] Run Core tests and all five Feature test suites with the bundled Python 3.12 runtime.
- [x] Run `test ! -e .git`, `test ! -e .worktrees`, and `test -d .stfolder`.
- [x] List every changed file and its purpose, record exact test totals, and tell the user to wait for Syncthing `Up to Date / 最新` before checking Unraid.
