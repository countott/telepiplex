# Peer Metadata Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TMDB and conditional AniList evidence to search, converge peer-provider metadata into one frozen contract, use bounded query variants, and make Plex prefer frozen metadata.

**Architecture:** Douban remains the discovery anchor. After selection, search enriches the anchor with Wikipedia, TVDB, TMDB, and conditional AniList facts, exact-reads frozen source links, and emits one backward-compatible `media_metadata v1` contract. Prowlarr, rename, and sync consume that same contract.

**Tech Stack:** Python 3.12, requests, asyncio, pytest, YAML, JSON Schema, telepiplex Feature SDK.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`.
- Do not execute Git commands, create worktrees, publish, tag, or connect the Mac checkout to GitHub.
- Use lowercase `telepiplex` in user-facing prose.
- Use TDD: every production behavior starts with a focused failing test and a verified RED run.
- Preserve `media_metadata` schema version 1 and make new fields backward-compatible.
- search version becomes `1.8.0`; sync version becomes `1.1.0`; rename version remains unchanged.
- Complete with local tests and remind the user to wait for Syncthing `Up to Date / 最新`.

---

### Task 1: TMDB and AniList adapters plus stable links

**Files:**
- Create: `features/search/src/telepiplex_search/adapters/tmdb.py`
- Create: `features/search/src/telepiplex_search/adapters/anilist.py`
- Modify: `features/search/src/telepiplex_search/input_contract.py`
- Modify: `features/search/src/telepiplex_search/direct_link.py`
- Test: `features/search/tests/test_tmdb_adapter.py`
- Test: `features/search/tests/test_anilist_adapter.py`
- Test: `features/search/tests/test_input_contract.py`
- Test: `features/search/tests/test_direct_link.py`

**Interfaces:**
- Produces: `search_tmdb(query: str, media_type: str, year: str = "") -> list[dict]`
- Produces: `get_tmdb_entity(media_type: str, entity_id: str) -> dict | None`
- Produces: `search_anilist(query: str, year: str = "") -> list[dict]`
- Produces: `get_anilist_media(entity_id: str) -> dict | None`
- Extends: `metadata_link_from_url()` and `resolve_direct_link()` for `tmdb` and `anilist`.

- [ ] Write focused adapter and stable-link tests using complete provider response fixtures.
- [ ] Run the new tests and confirm failures are caused by missing adapters/providers.
- [ ] Implement minimal authenticated TMDB and public AniList clients with normalized facts and typed failures.
- [ ] Implement stable URL parsing and direct exact reads.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Peer fact convergence and field provenance

**Files:**
- Modify: `features/search/src/telepiplex_search/entity_graph.py`
- Modify: `features/search/src/telepiplex_search/anchored_candidate.py`
- Modify: `features/search/src/telepiplex_search/media_metadata_v1.py`
- Test: `features/search/tests/test_entity_graph.py`
- Test: `features/search/tests/test_media_metadata_v1.py`

**Interfaces:**
- Extends: `EvidenceFact` with optional descriptive metadata fields.
- Produces: `identity.query_titles` and complete optional identity metadata.
- Produces: `evidence.field_resolutions` with selected/source/conflict data.

- [ ] Write failing tests for TMDB/AniList stable IDs, cross-ID clustering, descriptive metadata, and field provenance.
- [ ] Run the focused tests and confirm expected RED failures.
- [ ] Extend fact normalization and deterministic source-neutral field resolution.
- [ ] Add TMDB/AniList provider statuses, source facts, and downstream identity fields.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Canonical title and bounded query variants

**Files:**
- Modify: `features/search/src/telepiplex_search/title_policy.py`
- Modify: `features/search/src/telepiplex_search/prowlarr_query.py`
- Modify: `features/search/src/telepiplex_search/release_gate.py`
- Test: `features/search/tests/test_title_policy.py`
- Test: `features/search/tests/test_media_metadata_v1.py`
- Test: `features/search/tests/test_release_gate.py`

**Interfaces:**
- Consumes: `identity.query_titles` and all frozen aliases.
- Produces: at most three scoped Prowlarr queries.

- [ ] Replace the old kana-transliteration expectation with failing tests that require source-backed AniList romaji and English fallback.
- [ ] Write failing tests for bounded multiple query variants and release-gate acceptance of every frozen query title.
- [ ] Run tests and confirm expected RED failures.
- [ ] Remove local kana romanization, implement source-backed title resolution, and build up to three deduplicated scoped queries.
- [ ] Feed `query_titles` into release identity aliases.
- [ ] Run focused tests and confirm they pass.

### Task 4: Selected-candidate peer enrichment

**Files:**
- Modify: `features/search/src/telepiplex_search/confirmed_enrichment.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/candidate_hydration.py`
- Test: `features/search/tests/test_confirmed_enrichment.py`
- Test: `features/search/tests/test_feature_service.py`
- Test: `features/search/tests/test_candidate_hydration.py`

**Interfaces:**
- Produces: frozen TMDB and conditional AniList `source_links` with stable external IDs.
- Preserves: mandatory Douban anchor and quarantined optional-provider failures.

- [ ] Write failing tests for TMDB enrichment, external-ID convergence, conditional AniList enrichment, and degraded provider status.
- [ ] Run focused tests and confirm expected failures.
- [ ] Add deterministic TMDB selection and AniList selection helpers.
- [ ] Add service providers, logs, source links, and exact-read hydration.
- [ ] Run focused tests and confirm they pass.

### Task 5: Search configuration and credentials

**Files:**
- Modify: `features/search/config.default.yaml`
- Modify: `features/search/config.schema.json`
- Modify: `features/search/src/telepiplex_search/config_wizard.py`
- Modify: `features/search/manifest.yaml`
- Test: `features/search/tests/test_config_schema_contract.py`
- Test: `features/search/tests/test_config_wizard.py`

**Interfaces:**
- Adds: `metadata.tmdb` and `metadata.anilist` configuration.
- Adds: TMDB to the search config wizard with write-only secret handling.

- [ ] Write failing schema/default/wizard tests.
- [ ] Run the focused tests and confirm expected RED failures.
- [ ] Add default configuration, schema, direct-message hosts, and wizard flow.
- [ ] Run focused tests and confirm they pass.

### Task 6: Plex consumes frozen identity first

**Files:**
- Modify: `features/sync/src/telepiplex_sync/sync_service.py`
- Test: `features/sync/tests/test_sync_service.py`

**Interfaces:**
- Consumes: `identity.original_language` from confirmed `media_metadata`.
- Falls back: `TmdbAdapter.details()` only when frozen language is absent.

- [ ] Write a failing test proving a frozen original language selects audio without a live TMDB details call.
- [ ] Run it and confirm RED against current live-only behavior.
- [ ] Prefer frozen language and retain live TMDB fallback.
- [ ] Run the focused sync test and confirm it passes.

### Task 7: Versions, documentation, and full validation

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/sync/manifest.yaml`
- Modify: `features/sync/pyproject.toml`
- Modify: `features/sync/README.md`
- Modify: `features/sync/tests/test_feature_runtime.py`

**Interfaces:**
- Publishes local source identities: search `1.8.0`, sync `1.1.0`.

- [ ] Update version-contract tests first and confirm they fail against old manifests/projects/docs.
- [ ] Update manifests, projects, and READMEs.
- [ ] Run all search and sync Feature tests with the bundled Python runtime.
- [ ] Run the complete repository test command from `AGENTS.md`.
- [ ] Run local package builds for search and sync into a temporary directory and inspect their exit codes.
- [ ] Verify `.git` and `.worktrees` are absent and `.stfolder` remains present.
- [ ] Report changed files, actual validation results, and Syncthing handoff instructions without publishing.

