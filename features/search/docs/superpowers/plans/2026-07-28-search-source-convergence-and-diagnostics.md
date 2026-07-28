# Search Source Convergence and Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan inline. This Mac
> workspace forbids Git, so every Git/commit step from the generic skill is
> replaced by a local test checkpoint.

**Goal:** Make Search 1.1.1 converge from Chinese/Japanese discovery facts to
strict source-backed metadata while producing actionable logs and human-facing
candidate UI.

**Status:** Implemented and verified locally on 2026-07-28.

**Architecture:** Keep the unified anchored candidate pipeline. Add one
bounded AI query-hint pass only when a candidate lacks a Provider, validate
every hint through that Provider, repair invalid fact binding once, and keep
strict metadata construction source-backed.

**Tech Stack:** Python 3.12, asyncio, requests, pytest, telepiplex Feature SDK.

## Global Constraints

- Product-facing text uses lowercase `telepiplex`.
- AI query hints are never persisted as media facts.
- Provider facts remain the only source for titles, years, IDs, URLs and TVDB
  inventory.
- No Git command, worktree, branch, commit, push or publication is allowed on
  this Mac.
- All code changes use local tests with bytecode and pytest cache disabled.

---

### Task 1: Normalize Douban display titles

**Files:**
- Modify: `src/telepiplex_search/adapters/douban.py`
- Test: `tests/test_douban_adapter.py`

**Interfaces:**
- Produces: `_normalize_title_and_year(title: str, year_value: object) -> tuple[str, str]`.
- Consumed by: `_normalize_payload`.

- [ ] Add a failing adapter test with `冰果 氷菓‎ (2012)` and no separate year;
  assert title fields contain `冰果 氷菓`, year is `2012`, and aliases contain
  no invisible format control.
- [ ] Run the single test and confirm the unmodified adapter retains the
  duplicate year.
- [ ] Implement NFKC, `Cf` removal and trailing-year extraction in the adapter.
- [ ] Run `tests/test_douban_adapter.py`.

### Task 2: Add source-verified cross-language supplement queries

**Files:**
- Modify: `src/telepiplex_search/ai.py`
- Modify: `src/telepiplex_search/planner.py`
- Modify: `src/telepiplex_search/service.py`
- Test: `tests/test_search_ai_pipeline.py`
- Test: `tests/test_unified_search_pipeline.py`

**Interfaces:**
- Produces: `infer_source_supplement_queries_with_ai(context: dict) -> dict | None`.
- Produces: supplement payload
  `{"queries":[{"candidate_id": str, "provider": str, "title_hints":[str]}]}`.
- Consumes: candidate IDs, missing Provider names and current source facts.

- [ ] Add a failing AI parser test proving unknown candidate IDs, Provider
  names, years, URLs and stable IDs cannot leave the query-hint boundary.
- [ ] Add a failing unified pipeline test in which the first TVDB search misses
  `冰果`, AI proposes `Hyouka`, and the second TVDB call receives literal
  structured hypothesis `{title: Hyouka, year: 2012,
  content_identity: series}`.
- [ ] Run both tests and confirm the supplement planner is absent.
- [ ] Implement the bounded prompt, parser, candidate-bound query builder and
  missing-Provider routing.
- [ ] Log each Provider's structured supplement queries without URLs or
  credentials.
- [ ] Run both test files.

### Task 3: Repair and expose AI fact-binding failures

**Files:**
- Modify: `src/telepiplex_search/ai.py`
- Modify: `src/telepiplex_search/planner.py`
- Modify: `src/telepiplex_search/service.py`
- Test: `tests/test_unified_search_pipeline.py`
- Test: `tests/test_feature_service.py`

**Interfaces:**
- Produces: one bounded `stage=binding_repair` candidate-editor retry.
- Produces log events `search_binding status=received|invalid|repairing|ok`.
- Produces log event `search_planning status=failed`.

- [ ] Add a failing unified test where the first response binds one fact to two
  candidates and the repair response returns a valid shortlist.
- [ ] Add a failing service test capturing the final planning error log with
  plan ID, `candidate_binding_failed` and the concrete binding reason.
- [ ] Run the tests and confirm no repair or diagnostic log exists.
- [ ] Implement one strict repair attempt and per-candidate binding logs.
- [ ] Implement the final planning failure log.
- [ ] Run both test files.

### Task 4: Make Japanese Latin-title fallback source-backed

**Files:**
- Modify: `src/telepiplex_search/title_policy.py`
- Test: `tests/test_title_policy.py`

**Interfaces:**
- Produces `search_title_policy=official_english_fallback` only when a Japanese
  work lacks verified/derivable romanization but has a Provider-confirmed
  official English title.

- [ ] Change the existing Japanese-kanji test to require official-English
  fallback and add a separate failing test where both Latin fields are absent.
- [ ] Run the two tests and confirm the fallback case fails.
- [ ] Implement the fallback without changing kana-only romanization priority.
- [ ] Run `tests/test_title_policy.py`.

### Task 5: Localize candidate and deterministic-error UI

**Files:**
- Modify: `src/telepiplex_search/service.py`
- Test: `tests/test_feature_service.py`

**Interfaces:**
- Produces Chinese labels for Provider, media type, candidate role, relation,
  source state, unresolved status and metadata fields.
- Produces retry only for `fixed_link_read_failed`.

- [ ] Add failing candidate-grid and candidate-detail assertions that forbid
  `series_root`, `v0`, `standalone`, `wikipedia:not_bound` and
  `canonical_latin_title`.
- [ ] Add a failing hydration-error test proving deterministic metadata
  incompleteness does not retain the same select/retry callback.
- [ ] Run the focused tests and confirm current code-like output fails.
- [ ] Implement label helpers, human-readable error text and retry filtering.
- [ ] Run `tests/test_feature_service.py`.

### Task 6: Verify the complete Search change

**Files:**
- Verify all files from Tasks 1-5.

- [ ] Run focused tests:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
    "$PY" -m pytest -q -p no:cacheprovider \
    tests/test_douban_adapter.py \
    tests/test_search_ai_pipeline.py \
    tests/test_unified_search_pipeline.py \
    tests/test_title_policy.py \
    tests/test_feature_service.py
  ```

- [ ] Run the complete Search suite:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
    "$PY" -m pytest -q -p no:cacheprovider tests
  ```

- [ ] Run the project-level core and Feature suites from the repository
  instructions.
- [ ] Verify `.git` and `.worktrees` are absent and `.stfolder` is present.
- [ ] Report changed files, actual test counts and the Syncthing handoff.
