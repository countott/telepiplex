# Wikipedia-first Deterministic Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace search AI with deterministic Wikipedia/Wikidata discovery while preserving exact links and the confirmed metadata handoff through Rename and Plex.

**Architecture:** Plain titles produce structurally filtered root works from Wikipedia/Wikidata; user selection or an exact link freezes identity before the existing multi-source enrichment stage. A separate scope catalog selects TVDB, TMDB, or explicit Wikipedia structure and emits the existing `media_metadata v1` downstream contract without AI evidence.

**Tech Stack:** Python 3.12, requests, asyncio, MediaWiki Action API, Wikidata entity API, TVDB v4, TMDB v3, pytest.

## Global Constraints

- Product-facing copy uses lowercase `telepiplex` where the product name appears.
- Mac-local only; do not run Git or create `.git` or `.worktrees`.
- Use `apply_patch` for source and test edits.
- Every production behavior change follows a witnessed failing test.
- Search accepts explicit titles only; it does not repair natural language or typos.
- Exact Douban, Wikipedia, TMDB, TVDB, and AniList link handling remains supported.
- Douban does not supply season or episode structure.
- AniList supplies only confirmed Japanese-animation romaji and its external ID.
- Search Feature version changes from `1.8.1` to `1.9.0`.

---

### Task 1: Structurally Correct Wikipedia and Wikidata Evidence

**Files:**
- Modify: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Create: `features/search/src/telepiplex_search/adapters/wikidata.py`
- Modify: `features/search/tests/test_wikipedia_adapter.py`
- Create: `features/search/tests/test_wikidata_adapter.py`

**Interfaces:**
- `lookup_wikipedia_evidence(...)` preserves `search_rank`, `page_id`, `is_disambiguation`, and `wikibase_item`.
- `enrich_wikidata_entities(qids, ...) -> dict[str, dict]` returns labels, aliases, instance types, year, countries, and structural season facts.
- `is_media_work(entity) -> str` returns `movie`, `series`, or an empty string.

- [ ] Add failing fixtures for lost generator rank, empty-string disambiguation properties, people/list false positives, and a valid movie/series QID.
- [ ] Run the focused adapter tests and record the expected failures.
- [ ] Preserve rank, filter or expand disambiguation pages in bounded batches, attach Wikidata entities, and classify with `P31`.
- [ ] Add retry/cache/User-Agent tests and make the focused adapter suite pass.

### Task 2: Deterministic Root-work Discovery and Explicit-title Input

**Files:**
- Create: `features/search/src/telepiplex_search/work_discovery.py`
- Modify: `features/search/src/telepiplex_search/input_contract.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Create: `features/search/tests/test_work_discovery.py`
- Modify: `features/search/tests/test_input_contract.py`
- Modify: `features/search/tests/test_feature_service.py`

**Interfaces:**
- `discover_root_works(parsed, wikipedia_lookup, wikidata_lookup) -> list[dict]` returns QID-deduplicated root candidates.
- Every candidate carries display title, English title, year, countries, media type, QID, poster, source URL, and score reasons.
- `classify_search_input` returns `unsupported_text` for descriptive requests outside the explicit-title grammar.

- [ ] Add failing tests for `副总统`, `The Office`, `Monster`, `信号`, same-title movie/series results, and descriptive natural language rejection.
- [ ] Run the tests and witness failure under the current Douban/AI discovery path.
- [ ] Implement deterministic Chinese/English query variants, structural filtering, QID deduplication, and root candidate ranking.
- [ ] Route plain-title service requests to root discovery and render a root-work menu; run focused tests green.

### Task 3: Preserve Exact Metadata Links as Frozen Identities

**Files:**
- Modify: `features/search/src/telepiplex_search/direct_link.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/tests/test_direct_link.py`
- Modify: `features/search/tests/test_unified_search_pipeline.py`

**Interfaces:**
- Exact provider links produce `DirectEntity` and bypass root discovery.
- Wikipedia disambiguation links return candidates rather than a frozen work.
- Share links without stable IDs fall back only to deterministic explicit-title discovery.

- [ ] Add failing tests that exact Douban/TMDB/TVDB/Wikipedia links never invoke root discovery or AI and that TVDB season zero is rejected.
- [ ] Add failing tests for Wikipedia disambiguation links and canonical share-link resolution.
- [ ] Implement minimal routing changes and verify exact-link identity cannot drift during enrichment.
- [ ] Run direct-link and unified-pipeline tests green.

### Task 4: Multi-source Enrichment after Identity Lock

**Files:**
- Modify: `features/search/src/telepiplex_search/confirmed_enrichment.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/adapters/anilist.py`
- Modify: `features/search/src/telepiplex_search/media_metadata_v1.py`
- Modify: `features/search/tests/test_confirmed_enrichment.py`
- Modify: `features/search/tests/test_media_metadata_v1.py`
- Modify: `features/search/tests/test_anilist_adapter.py`

**Interfaces:**
- Enrichment order is Wikipedia/Wikidata, TMDB, TVDB, unique Douban display fallback, and conditional AniList romaji.
- `media_metadata v1` records deterministic evidence and contains no search AI decision fields.
- Supplemental facts cannot replace the anchor provider or stable ID.

- [ ] Add failing tests for identity immutability, rich field fusion, no-Chinese fallback, and AniList romaji-only output.
- [ ] Run focused tests and record current AI/AniList contract failures.
- [ ] Restrict AniList contribution, add verified Douban display fallback, and emit deterministic evidence.
- [ ] Run focused enrichment and contract tests green.

### Task 5: TVDB to TMDB to Wikipedia Series Scope Catalog

**Files:**
- Modify: `features/search/src/telepiplex_search/adapters/tmdb.py`
- Create: `features/search/src/telepiplex_search/scope_catalog.py`
- Modify: `features/search/src/telepiplex_search/series_scope.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Create: `features/search/tests/test_scope_catalog.py`
- Modify: `features/search/tests/test_series_scope.py`
- Modify: `features/search/tests/test_feature_service.py`

**Interfaces:**
- `get_tmdb_series_inventory(tmdb_id) -> list[dict]` reads numbered regular seasons and episodes.
- `build_scope_catalog(...)` returns source, known seasons, episodes by season, and completeness flags.
- Season zero and special-format entries are absent from all menus and metadata items.

- [ ] Add failing source-priority, TMDB inventory, Wikipedia season-count-only, one-season, no-structure, and special-exclusion tests.
- [ ] Run focused tests and witness missing TMDB/Wikipedia fallback behavior.
- [ ] Implement the catalog and second-level menu semantics for unscoped, season-scoped, and episode-scoped input.
- [ ] Apply the selected scope to `media_metadata v1` and run the scope/service tests green.

### Task 6: Remove Search AI and Preserve Release Queries and Poster Ordering

**Files:**
- Delete: `features/search/src/telepiplex_search/ai.py` when no runtime imports remain
- Delete or simplify: search-only orchestration modules whose only consumer is AI
- Modify: `features/search/src/telepiplex_search/planner.py`
- Modify: `features/search/src/telepiplex_search/discovery_flow.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/config_wizard.py`
- Modify: `features/search/config.default.yaml`
- Modify: `features/search/config.schema.json`
- Modify: `features/search/src/telepiplex_search/prowlarr_query.py`
- Modify: affected Search tests

**Interfaces:**
- Search runtime has no AI client call, AI config page, AI fallback status, or AI decision evidence.
- Query cleaning preserves semantic punctuation such as `%`.
- Identity card poster order is TMDB, Douban, Wikipedia, placeholder, text.

- [ ] Add failing tests proving no AI callable is reached, search config has no AI section, `3%` survives query generation, and AniList art is ignored.
- [ ] Run the focused tests and record current failures.
- [ ] Remove runtime AI paths and configuration, clean dead modules/imports, preserve deterministic planner interfaces needed downstream, and fix query punctuation.
- [ ] Run all Search tests and repair only behavior intentionally superseded by this design.

### Task 7: Downstream Metadata Contract Regression

**Files:**
- Modify only if a failing contract test requires it:
  - `features/download/src/telepiplex_download/service.py`
  - `features/rename/src/telepiplex_rename/service.py`
  - `features/rename/src/telepiplex_rename/processor.py`
  - `features/sync/src/telepiplex_sync/sync_service.py`
- Modify corresponding Feature tests.

**Interfaces:**
- Download passes `media_metadata` and `naming_metadata` unchanged.
- Rename consumes confirmed identity/placement/items, fills resolved paths, and never overwrites identity from filenames.
- Sync/Plex receives the enriched post-Rename contract through `media.organized`.

- [ ] Add or strengthen an end-to-end contract test from selected release through Download payload, Rename update, and Sync enqueue.
- [ ] Run it and verify whether current downstream code already satisfies the contract.
- [ ] Apply only minimal downstream fixes exposed by the failing test.
- [ ] Run Download, Rename, and Sync focused tests green.

### Task 8: Version, Documentation, and Full Local Verification

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `features/search/src/telepiplex_search.egg-info/SOURCES.txt` if files change
- Modify: `features/search/README.md` where AI or source responsibilities are documented

**Interfaces:**
- All checked-in Search version identities are `1.9.0`.
- Documentation describes explicit-title deterministic search and the stable-ID/multi-source handoff.

- [ ] Add a failing version/config contract expectation for `1.9.0` and absence of Search AI configuration.
- [ ] Update version and documentation files.
- [ ] Run focused Search tests, then Host and all five Feature suites with bundled Python 3.12.
- [ ] Verify `.git` and `.worktrees` do not exist and `.stfolder` remains present.
- [ ] Record changed files, actual test totals, and Syncthing handoff instructions.

