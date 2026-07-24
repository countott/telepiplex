# Query and Candidate Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve explicit episode and standalone episode-title queries through
relevance-ranked, source-verified candidates without a seven-candidate hard
failure.

**Architecture:** Keep scope parsing and candidate verification program-owned.
Prefer exact normalized base-title candidates, rank controlled evidence
expansion deterministically, and use AI only to propose a parent-series query
for standalone episode titles before program code verifies the TVDB episode
inventory.

**Tech Stack:** Python 3.12, asyncio, requests, unittest, pytest.

## Global Constraints

- Do not execute Git commands in `/Users/young/Documents/telepiplex`.
- Do not weaken the two-source ordinary-text gate.
- Series confirmation still requires a TVDB Series ID.
- AI cannot invent stable IDs, episode numbers, metadata contracts, or
  Prowlarr queries.
- Source adapters, release gating, download handoff, rename behavior, and
  configuration templates stay outside this change.

---

### Task 1: Lock base-title and explicit-scope behavior

**Files:**

- Modify: `tests/test_search_utils.py`
- Modify: `tests/test_ranked_planner.py`
- Modify: `src/telepiplex_search/planner.py`

**Interfaces:**

- Consumes: `parse_search_intent(raw_query) -> dict`.
- Produces: exact normalized base-title preference while retaining
  `scope`, `season_number`, and `episode_number`.

- [ ] Add parser assertions for `Rick and Morty S09E08` and
  `瑞克和莫迪第九季第八集`.
- [ ] Add a planner regression with one exact series and seven qualified
  prefix-noise series; expect one exact candidate and retrieval query
  `Rick and Morty S09E08`.
- [ ] Add a missing-inventory regression requiring
  `tvdb_scope_not_verified`.
- [ ] Run the focused tests and confirm the planner test fails with
  `too_many_candidates`.
- [ ] Prefer `exact` only when the request has explicit scope, year, or media
  type; otherwise retain `title_matches` for bare title-family confirmation.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Rank controlled expansion by query relevance

**Files:**

- Modify: `tests/test_ranked_planner.py`
- Modify: `src/telepiplex_search/planner.py`

**Interfaces:**

- Produces:
  `_ordered_expansion_candidates(candidates, intent) -> list[CandidateEntity]`.
- Consumes: normalized title, requested year/media type, provider count, and
  stable candidate key.

- [ ] Add a test whose candidate-key order conflicts with title relevance.
- [ ] Run the test and confirm the existing `candidates[:3]` order fails.
- [ ] Implement a deterministic sort key:
  exact title, year/type compatibility, shortest prefix remainder,
  provider count, candidate key.
- [ ] Feed the first three ordered candidates into `_expanded_hypotheses`.
- [ ] Re-run the focused planner tests.

### Task 3: Resolve standalone episode titles from verified inventory

**Files:**

- Modify: `tests/test_source_orchestrator.py`
- Modify: `tests/test_ranked_planner.py`
- Modify: `src/telepiplex_search/source_orchestrator.py`
- Modify: `src/telepiplex_search/planner.py`

**Interfaces:**

- Produces:
  `_resolve_episode_title_intent(raw_query, intent, candidates)
  -> tuple[dict, str]`.
- Consumes: an AI `scope=episode` hint with missing numbers plus TVDB episode
  inventory already attached to request-scoped candidate facts.

- [ ] Add a prompt-contract test requiring parent-series hypothesis and TVDB
  inventory guidance for standalone episode titles.
- [ ] Add planner tests for unique, missing, and ambiguous inventory matches.
- [ ] Run the focused tests and confirm the new behavior is absent.
- [ ] Update the orchestrator prompt without expanding tool permissions.
- [ ] Implement exact normalized episode-name matching; return a copied intent
  with verified season/episode numbers and the matched parent candidate key.
- [ ] Filter to the unique parent candidate before qualification.
- [ ] Raise `tvdb_scope_not_verified` for no match and
  `ambiguous_candidates` for multiple matches.
- [ ] Re-run orchestrator and planner tests.

### Task 4: Remove the seven-candidate failure and score truncation

**Files:**

- Modify: `tests/test_ranked_planner.py`
- Modify: `tests/test_search_ai_pipeline.py`
- Modify: `src/telepiplex_search/planner.py`
- Modify: `src/telepiplex_search/ai.py`
- Modify: `src/telepiplex_search/service.py`

**Interfaces:**

- Produces complete qualified candidate lists and one AI score per supplied
  candidate.

- [ ] Change the eight-qualified-candidate test to require eight selectable
  confirmation candidates.
- [ ] Add an AI parser test returning eight score objects and require all
  eight.
- [ ] Run both tests and confirm `too_many_candidates` and seven-score slicing.
- [ ] Remove `MAX_DISPLAY_CANDIDATES`, its hard error, and the unused service
  message.
- [ ] Return the complete validated AI score list.
- [ ] Re-run the focused tests.

### Task 5: Add candidate-funnel diagnostics

**Files:**

- Modify: `tests/test_ranked_planner.py`
- Modify: `src/telepiplex_search/planner.py`

**Interfaces:**

- Produces:
  `_candidate_qualification_reason(candidate, intent, direct_anchor) -> str`
  and sanitized aggregate log counts.

- [ ] Add a logger-backed test with qualified, single-source, and missing-TVDB
  candidates.
- [ ] Run the test and confirm existing logs omit the funnel.
- [ ] Refactor `_candidate_is_qualified` through a reason-returning helper.
- [ ] Log aggregate counts without titles, queries, IDs, or source facts.
- [ ] Re-run planner tests.

### Task 6: Verify the complete Feature

- [ ] Run focused unittest modules for search utilities, planner, AI transport,
  and source orchestrator.
- [ ] Run complete `unittest discover` for `features/search/tests`.
- [ ] Run complete `pytest -q -p no:cacheprovider` for
  `features/search/tests`.
- [ ] Compile modified Python files without writing into the workspace.
- [ ] Run `python -m pip check`.
- [ ] Confirm `.git` and `.worktrees` are absent and `.stfolder` exists.
- [ ] Report all created and modified files and remind the user to wait for
  Syncthing `Up to Date / 最新`.
