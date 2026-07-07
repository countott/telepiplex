# Search Entry Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a verified search preflight so `/search` resolves and confirms a movie or series scope before Prowlarr is queried.

**Architecture:** Add a focused `app/utils/search_resolution.py` module for intent parsing, provider normalization, candidate/range construction, and Prowlarr query generation. Keep Telegram conversation wiring in `app/handlers/search_handler.py`, reusing the current Prowlarr result selection and save-directory flow after confirmation.

**Tech Stack:** Python 3.12, python-telegram-bot conversation handlers, existing Douban/TVDB adapters, existing `app.utils.ai.chat_completion` JSON parsing helpers, unittest.

## Global Constraints

- Raw user text, cleaned page titles, or AI guesses must not be sent directly to Prowlarr.
- AI query normalization cannot verify entries or invent IDs, seasons, episodes, air dates, or Prowlarr queries.
- AI verified fallback must provide a verifiable external ID; otherwise block.
- Series confirmation candidates combine entry and scope, with a recommended first option.
- Unreleased episodes block and never call Prowlarr.
- Series links require confirmation; clearly resolved movie links display briefly and auto-advance.
- Magnet links remain outside this chain.

---

### Task 1: Search Resolution Core

**Files:**
- Create: `app/utils/search_resolution.py`
- Test: `tests/test_search_resolution.py`

**Interfaces:**
- Produces:
  - `parse_search_intent(raw_query: str) -> dict`
  - `build_confirmation_candidates(entries: list[dict], intent: dict, episodes_by_series: dict | None = None) -> list[dict]`
  - `candidate_to_prowlarr_query(candidate: dict) -> str`
  - `is_unreleased_episode(episode: dict, today: date | None = None) -> bool`

- [x] **Step 1: Write failing tests**

Cover parsing `S02E05`, Chinese season/episode text, movie query generation, season query generation, and unreleased episode detection.

- [x] **Step 2: Run tests to verify failure**

Run: `/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_search_resolution`

Expected: FAIL because `app.utils.search_resolution` does not exist.

- [x] **Step 3: Implement minimal core**

Create dataclass-free dict helpers so current tests and handlers can consume plain dictionaries.

- [x] **Step 4: Run tests to verify pass**

Run the same unittest command. Expected: OK.

### Task 2: AI Query Normalization Helpers

**Files:**
- Modify: `app/utils/ai.py`
- Test: `tests/test_search_resolution.py`

**Interfaces:**
- Produces:
  - `normalize_search_query_with_ai(raw_query: str) -> dict | None`
  - `infer_verified_search_match_with_ai(raw_query: str) -> dict | None`

- [x] **Step 1: Write failing tests**

Patch `chat_completion` to return JSON and verify strict schema normalization, blocked no-ID fallback, and no Prowlarr query output.

- [x] **Step 2: Run tests to verify failure**

Expected: FAIL because functions do not exist.

- [x] **Step 3: Implement prompts and JSON normalization**

Use existing `parse_ai_json_response`. Prompts must explicitly forbid hallucinated IDs, season counts, air dates, and Prowlarr query output.

- [x] **Step 4: Run tests to verify pass**

Run `tests.test_search_resolution`. Expected: OK.

### Task 3: Handler Preflight And Confirmation

**Files:**
- Modify: `app/handlers/search_handler.py`
- Test: `tests/test_search_handler.py`

**Interfaces:**
- Consumes Task 1 and Task 2 helpers.
- Produces:
  - `SEARCH_CONFIRM_ENTRY_SCOPE`
  - `pending_entry_confirmations`
  - `_resolve_entry_candidates(raw_query: str) -> dict`
  - `confirm_entry_scope(update, context)`

- [x] **Step 1: Write failing tests**

Update tests so plain query without verified metadata blocks instead of searching. Add tests for ambiguous movie confirmation, series `S02E05` confirmation, unreleased block, and movie link auto-advance.

- [x] **Step 2: Run tests to verify failure**

Expected: FAIL against current handler behavior.

- [x] **Step 3: Implement minimal preflight**

Route `/search` and direct metadata links through entry resolution. Store confirmation candidates. Only confirmed candidates call `_send_search_results`.

- [x] **Step 4: Run targeted tests**

Run `tests.test_search_handler`. Expected: OK.

### Task 4: Metadata Carry-Through

**Files:**
- Modify: `app/handlers/search_handler.py`
- Test: `tests/test_search_handler.py`

**Interfaces:**
- Confirmation candidate metadata must be copied into `pending_search_tasks` as `metadata` and `plex_metadata`.

- [x] **Step 1: Write failing test**

Confirming `S02E05` must generate query `<title> S02E05` and pass scope metadata to `download_task` after result/save selection.

- [x] **Step 2: Run test to verify failure**

Expected: FAIL if scope metadata is missing.

- [x] **Step 3: Implement metadata carry-through**

Include `media_type`, `scope`, `season_number`, `episode_number`, `external_ids`, and cover URL.

- [x] **Step 4: Run targeted tests**

Expected: OK.

### Task 5: Verification And Docs

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`

- [x] **Step 1: Update docs**

Document that `/search` verifies entries before Prowlarr and blocks unverified or unreleased requests.

- [x] **Step 2: Run full verification**

Run:

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover tests
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile app/handlers/search_handler.py app/utils/search_resolution.py app/utils/ai.py
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

Expected: all pass.
