# Search Verified Clarification Identity Implementation Plan

> **For agentic workers:** Execute inline with
> `superpowers:test-driven-development`. Mac-local Git operations are prohibited
> by `AGENTS.md`.

**Goal:** Keep clarification source-backed and prevent wrong-type or wrong-year
candidates from reaching AI scoring.

**Architecture:** Build generic AI clarification from the normalized user title,
build source ambiguity options from individual verified candidates, propagate a
stable identity through the Search callback, and filter excluded candidates
before AI scoring. AI title-hint validation rejects hallucinated query
dimensions.

**Tech Stack:** Python 3.12, asyncio, unittest, pytest, telepiplex Feature
operation API.

## Global Constraints

- Do not run Git or create Git metadata in the Mac workspace.
- Do not add typo dictionaries or convert source Chinese titles. Clarification
  labels follow the Chinese writing system used in the query.
- Do not lower independent-source, TVDB, title-policy, year, or scope gates.
- Preserve the same Search operation ID across clarification.
- Keep Search at the pending local release identity `1.0.6`.

---

### Task 1: Lock candidate qualification before AI

**Files:**
- Modify: `features/search/tests/test_ranked_planner.py`
- Modify: `features/search/src/telepiplex_search/planner.py`

**Interfaces:**
- Consumes: `_candidate_qualification_reason(candidate, intent, direct_anchor)`.
- Produces: explicit media-type rejection and selectable-only threshold results.

- [ ] Add a planner test where the query explicitly requests a 2022 series but
  all sources return only the 2022 movie.
- [ ] Run the focused test and verify it fails because the AI scorecard receives
  the movie.
- [ ] Reject an explicit media-type mismatch in candidate qualification.
- [ ] Filter threshold results to `selectable=True` before recovery success and
  AI scorecard construction.
- [ ] Run the focused planner tests and verify the scorecard is not called.

### Task 2: Build clarification from verified identities

**Files:**
- Modify: `features/search/tests/test_ranked_planner.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/src/telepiplex_search/planner.py`
- Modify: `features/search/src/telepiplex_search/service.py`

**Interfaces:**
- Produces: clarification options with `label`, `query`, `media_type`, `year`,
  and optional `locked_identity: {"key": str, "value": str}`.
- Consumes: `SearchFeature._start_plan_task(..., locked_identity=...)`.

- [ ] Add a source-backed `想见你` test with a 2019 series, a 2022 movie,
  simplified/traditional Chinese source titles, and distinct English titles.
- [ ] Add the inverse traditional-query case to prove the display policy does
  not force simplified Chinese.
- [ ] Assert literal option labels, years, queries, and stable identities.
- [ ] Change the AI-only clarification test so the callback query keeps the raw
  user title instead of a corrected AI hint.
- [ ] Run both focused tests and verify the existing synthesized-query behavior
  fails them.
- [ ] Build source options from individual related candidates and retain their
  source spelling.
- [ ] Propagate an optional stable identity through clarification callbacks to
  the default planner without changing injected two-argument test planners.
- [ ] Run planner and service clarification tests.

### Task 3: Reject polluted AI title hints and verify the package

**Files:**
- Modify: `features/search/tests/test_search_ai_pipeline.py`
- Modify: `features/search/src/telepiplex_search/ai.py`

**Interfaces:**
- Consumes: AI `title_hints` plus the original `raw_query`.
- Produces: clean title-only retrieval hints or a fail-closed `None`.

- [ ] Add a test whose AI response contains
  `想见你 想見你‎ (2022)` for raw query `想见你`.
- [ ] Run it and verify the polluted hint is currently accepted.
- [ ] Remove format-control characters and reject unrequested years or
  media-type suffixes in title hints.
- [ ] Strengthen the prompt so each hint is one title without concatenated
  script variants, years, or type suffixes.
- [ ] Run AI, planner, service, and complete Search tests.
- [ ] Repeat the real-title matrix, build `search-1.0.6.tpx`, verify the archive,
  and confirm `.git` and `.worktrees` are absent while `.stfolder` remains.
