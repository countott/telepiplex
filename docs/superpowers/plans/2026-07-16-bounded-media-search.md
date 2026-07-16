# Bounded Media Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace broad candidate guessing with a bounded seven-candidate search gate, explicit movie/series interaction states, disposable task metadata, and separate retrieval versus placement contracts.

**Architecture:** Deterministic parsing and direct metadata links enter one request-scoped entity resolver. AI is optional and may only return a validated intent hint or one selected-candidate relation hypothesis. The service confirms an entity, then runs either the movie path or the TVDB-backed series scope state machine before building a disposable handoff contract.

**Tech Stack:** Python 3.12, asyncio, unittest/pytest, requests, PyYAML, Telegram Feature action contract, GitHub Actions Feature/Core release workflows.

## Global Constraints

- Show every qualified candidate when the deduplicated count is 1–7.
- Reject more than 7 qualified candidates; never truncate or let AI select one.
- Bare numbers never imply installment, season, or episode without source verification.
- AI never returns stable IDs, final metadata, Prowlarr queries, paths, or Season 00 numbers.
- Series scope is always explicit before Prowlarr; bare series titles never fall back to the first TVDB episode.
- Retrieval identity and placement identity remain separate.
- Search state is disposable; remove the canonical entity registry.
- `S00E100+` is task-local placement metadata and never a Prowlarr query.
- Clear queries and direct links must work when AI is unavailable.

---

### Task 1: Lock the bounded input and candidate contracts

**Files:**
- Create: `src/telepiplex_media_search/input_contract.py`
- Modify: `src/telepiplex_media_search/search_resolution.py`
- Modify: `src/telepiplex_media_search/search_query.py`
- Test: `tests/test_input_contract.py`

**Interfaces:**
- Produces: `ParsedInput`, `MetadataLink`, `classify_search_input(raw_query)`, `has_ambiguous_bare_number(raw_query, parsed)`.
- Consumes: existing deterministic season/episode parsing helpers.

- [ ] Write failing tests for plain titles, explicit years, season/episode expressions, supported Douban/TVDB work/season/episode links, `蝙蝠侠1`, and `变形金刚3`.
- [ ] Run `python3 -m unittest tests.test_input_contract -v` and verify failures describe missing classification behavior.
- [ ] Implement immutable parsed-input/link values and deterministic classification.
- [ ] Run the focused test and existing search-resolution tests.

### Task 2: Replace score-first truncation with the seven-candidate display gate

**Files:**
- Modify: `src/telepiplex_media_search/planner.py`
- Modify: `src/telepiplex_media_search/candidate_score.py`
- Modify: `src/telepiplex_media_search/entity_graph.py`
- Test: `tests/test_ranked_planner.py`
- Test: `tests/test_candidate_score.py`

**Interfaces:**
- Produces: `MAX_DISPLAY_CANDIDATES = 7`, `too_many_candidates` planning error, and candidates whose score only orders qualified entities.
- Consumes: request-scoped evidence facts and canonical title policy.

- [ ] Replace the five-candidate test with tests proving seven candidates are returned and eight qualified candidates raise `too_many_candidates`.
- [ ] Add a test proving every qualified candidate remains selectable even when scores are close.
- [ ] Run focused tests and verify the old truncation behavior fails.
- [ ] Remove pre-score slicing and per-query mandatory AI scorecards; apply deterministic qualification and stable ordering.
- [ ] Run focused tests.

### Task 3: Add direct-link identity resolution

**Files:**
- Create: `src/telepiplex_media_search/direct_link.py`
- Modify: `src/telepiplex_media_search/adapters/douban.py`
- Modify: `src/telepiplex_media_search/adapters/tvdb.py`
- Modify: `src/telepiplex_media_search/planner.py`
- Test: `tests/test_direct_link.py`

**Interfaces:**
- Produces: `resolve_direct_link(link, providers) -> DirectEntity`.
- Consumes: Douban subject IDs and TVDB movie/series/season/episode IDs parsed by Task 1.

- [ ] Write failing tests proving links lock one entity and malformed links never fall back to site-brand text.
- [ ] Run focused tests and verify failure.
- [ ] Implement direct Douban subject lookup and TVDB ID lookups with media/scope metadata.
- [ ] Route direct links before text planning and enrich without replacing the linked identity.
- [ ] Run focused tests.

### Task 4: Implement the series scope state machine

**Files:**
- Create: `src/telepiplex_media_search/series_scope.py`
- Modify: `src/telepiplex_media_search/service.py`
- Modify: `src/telepiplex_media_search/planner.py`
- Test: `tests/test_series_scope.py`
- Test: `tests/test_feature_service.py`

**Interfaces:**
- Produces: `SeriesInventory`, `ScopeChoice`, `series_scope_options(contract)`, `apply_series_scope(contract, choice)`.
- Consumes: selected series TVDB episodes and explicit parsed scope.

- [ ] Write failing tests for a one-season bare series, multi-season bare series, explicit season, explicit episode, aired-episode validation, and no `items[0]` fallback.
- [ ] Run focused tests and verify the current `S01E01` bug.
- [ ] Implement inventory calculation and validation.
- [ ] Add callback/message states for whole series, season number, and episode number without per-episode buttons.
- [ ] Build Prowlarr queries only from confirmed scope.
- [ ] Run focused service and series-scope tests.

### Task 5: Separate retrieval and placement for related movies

**Files:**
- Modify: `src/telepiplex_media_search/planner.py`
- Modify: `src/telepiplex_media_search/search_plan.py`
- Modify: `src/telepiplex_media_search/service.py`
- Test: `tests/test_search_plan.py`
- Test: `tests/test_feature_service.py`

**Interfaces:**
- Produces: `media_metadata.retrieval` and task-local related-movie placement choices.
- Consumes: one verified relation for the selected movie and `TemporarySpecialAllocator`.

- [ ] Write failing tests proving related movies search by movie title/year, official TVDB Specials affect only placement, and local mappings use `S00E100+`.
- [ ] Write failing interaction tests for “归入Specials” versus “按独立电影整理”.
- [ ] Run focused tests and verify failure.
- [ ] Add retrieval metadata, defer relation handling until selection, and implement placement choice callbacks.
- [ ] Preserve release/renaming handoff metadata.
- [ ] Run focused tests.

### Task 6: Restrict AI to intent and selected-candidate relation hints

**Files:**
- Modify: `src/telepiplex_media_search/ai.py`
- Modify: `src/telepiplex_media_search/planner.py`
- Test: `tests/test_search_ai_pipeline.py`
- Test: `tests/test_ranked_planner.py`

**Interfaces:**
- Produces: validated `IntentHint` and selected-candidate relation hint payloads.
- Consumes: only raw user language and auditable selected-candidate facts.

- [ ] Write failing tests rejecting stable IDs, final metadata, Prowlarr queries, paths, and invented season/episode facts.
- [ ] Write tests proving clear queries never require AI and one fallback pass is the maximum.
- [ ] Run focused tests and verify failure.
- [ ] Replace mandatory scorecard calls with optional intent fallback and selected-candidate relation verification.
- [ ] Run focused tests.

### Task 7: Remove the entity registry

**Files:**
- Delete: `src/telepiplex_media_search/entity_registry.py`
- Delete: `tests/test_entity_registry.py`
- Modify: `src/telepiplex_media_search/runtime.py`
- Modify: `src/telepiplex_media_search/service.py`
- Modify: `tests/test_feature_service.py`

**Interfaces:**
- Produces: a stateless `MediaSearchFeature` whose selected task contract is carried only through open115.
- Consumes: confirmed request-scoped `media_metadata`.

- [ ] Change tests to assert browsing, selection, cancellation, and related placement never create a database.
- [ ] Run focused tests and verify registry assumptions fail.
- [ ] Remove registry construction, exact rehydration, upserts, and persistence error paths.
- [ ] Run focused tests.

### Task 8: Fix Core photo-to-status rendering

**Files:**
- Modify: `/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/app/handlers/plugin_handler.py`
- Test: `/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/tests/test_plugin_handler.py`
- Test: `/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/tests/test_operation_pipeline_e2e.py`

**Interfaces:**
- Produces: safe transition from poster-backed candidate messages to text progress/results without calling text edit on a photo message.
- Consumes: existing safe action kinds and operation details.

- [ ] Write a failing regression test reproducing Telegram `BadRequest: There is no text in the message to edit`.
- [ ] Run focused Core tests and verify failure.
- [ ] Preserve media details through operation updates and send a new text message when the current message is photo-backed.
- [ ] Run focused Core tests.

### Task 9: Version, verify, build, publish

**Files:**
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `/Users/young/Documents/telepiplex/.worktrees/telepiplex-core/app/115bot.py`
- Modify: relevant version contract tests.

**Interfaces:**
- Produces: media-search `1.3.0`, Core `1.2.1`, immutable Feature/Core tags, GitHub Releases, GHCR image, and updated catalog.

- [ ] Bump media-search to `1.3.0` and Core to `1.2.1`.
- [ ] Run media-search focused tests, full tests, compile, dependency check, and `git diff --check`.
- [ ] Run Core focused tests, full tests, compile, dependency check, config-template comparison, and `git diff --check`.
- [ ] Build and verify `media-search-1.3.0.tpx`.
- [ ] Commit and push `feature/media-search` and `feature/telepiplex-core`.
- [ ] Create and push `core-v1.2.1` and `media-search-v1.3.0` tags from the Core release-infrastructure commit.
- [ ] Monitor GitHub Actions until both releases and the catalog update succeed.
