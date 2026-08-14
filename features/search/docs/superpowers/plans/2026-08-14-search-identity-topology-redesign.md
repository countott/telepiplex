# Search Identity and Series Topology Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild search 1.11.0 around a verified identity graph and complete series-order profiles so recall, Simplified Chinese titles, direct seasonal links, and season menus remain consistent end to end.

**Architecture:** Union Wikipedia and Wikidata into a bounded relationship graph, hydrate exact Douban bindings before candidate display, preserve seasonal direct-link scope through metadata v1, and replace provider coordinate intersection with a `SeriesTopology` profile selector. External HTTP calls stay in adapters; graph matching, topology selection, and query policy remain pure and directly testable.

**Tech Stack:** Python 3.12, standard library, requests, unittest/pytest, existing telepiplex SDK and search contracts.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`; never run Git or create `.git`/`.worktrees`.
- Make every behavior change test-first and observe the focused test fail for the intended reason.
- Do not add title-specific exceptions for the five reported examples.
- Keep Wikipedia primary, Douban authoritative for verified Simplified Chinese titles, and TVDB/TMDB downstream unless Wikipedia genuinely cannot provide topology.
- Bump manifest, package, adapters, documentation, and version-contract tests together to 1.11.0.
- Finish with Search full tests, package build, root boundary checks, and Syncthing handoff only.

---

### Task 1: Write End-to-End Regression Contracts

**Files:**
- Modify: `features/search/tests/test_work_discovery.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/tests/test_direct_link.py`
- Modify: `features/search/tests/test_media_metadata_v1.py`

- [x] Add a discovery test where a partial `贼王` Wikipedia hit coexists with exact Wikidata `海贼王` seeds and two-hop related anime/film entities; assert the weak hit is absent and all verified media remain.
- [x] Add an always-union test where Wikipedia supplies three `男儿本色` roots and Wikidata supplies a fourth exact movie.
- [x] Add a candidate rendering test where Wikidata P4529 resolves a Douban root title before user selection.
- [x] Add a direct-link-to-Prowlarr test for a seasonal Douban entity with an English `Season 3` title.
- [x] Add an inventory test proving incompatible provider profiles never collapse to a coordinate intersection.
- [x] Run each focused test and record the expected assertion failure against 1.10.0.

### Task 2: Build Unified Root Discovery

**Files:**
- Modify: `features/search/src/telepiplex_search/work_discovery.py`
- Modify: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/tests/test_work_discovery.py`
- Modify: `features/search/tests/test_feature_service.py`

- [x] Replace substring relevance with exact normalized title/alias matching for selectable seeds.
- [x] Always union bounded Wikipedia QIDs and Wikidata search QIDs before candidate construction.
- [x] Traverse typed `adaptation_ids`/`part_ids` breadth-first to depth two with a 60-QID budget and retain edge provenance.
- [x] Convert relation-reached media and exact media into one candidate shape, deduplicate by QID, and sort deterministically.
- [x] Retain up to 40 verified candidates and paginate candidate buttons five per page without changing the frozen list.
- [x] Add discovery diagnostics for seeds, weak rejections, edges, and budget status.
- [x] Run the focused discovery and service tests until green.

### Task 3: Hydrate Exact Douban Titles Before Display

**Files:**
- Modify: `features/search/src/telepiplex_search/adapters/wikidata.py`
- Modify: `features/search/src/telepiplex_search/confirmed_enrichment.py`
- Create: `features/search/src/telepiplex_search/candidate_locale.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/tests/test_wikidata_adapter.py`
- Modify: `features/search/tests/test_confirmed_enrichment.py`
- Create: `features/search/tests/test_candidate_locale.py`
- Modify: `features/search/tests/test_feature_service.py`

- [x] Add a failing adapter test for P4529 to `external_ids.douban_subject`.
- [x] Add a failing pure locale-hydration test for an exact subject ID and conservative season cleanup.
- [x] Normalize P4529 and accept identical subject IDs as `wikidata_exact` identity evidence without any IMDb API call.
- [x] Hydrate exact bindings before candidate rendering, update the candidate identity and provenance, and freeze the exact Douban link for final hydration.
- [x] Keep fuzzy Douban search post-selection only when no exact binding exists.
- [x] Run Wikidata, locale, enrichment, and candidate-rendering tests until green.

### Task 4: Preserve Seasonal Direct Links and Foreign Query Identity

**Files:**
- Modify: `features/search/src/telepiplex_search/direct_link.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/prowlarr_query.py`
- Modify: `features/search/tests/test_direct_link.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/tests/test_prowlarr_query.py`

- [x] Add failing tests that a Douban season entity returns `scope=season`, root titles, the season number, and no root year constraint.
- [x] Normalize seasonal source titles at resolution while preserving raw source facts.
- [x] During root supplementation, add a verified season binding when a Wikipedia/TVDB/TMDB inventory proves that season exists.
- [x] Require a verified Latin-script root title for non-Chinese foreign works and return `foreign_search_title_missing` instead of a Chinese-only Prowlarr query.
- [x] Prove the completed plan sends `House of the Dragon S03`.
- [x] Run direct-link, query, and service tests until green.

### Task 5: Replace Coordinate Intersection with SeriesTopology

**Files:**
- Create: `features/search/src/telepiplex_search/series_topology.py`
- Modify: `features/search/src/telepiplex_search/wikipedia_episode_inventory.py`
- Modify: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Modify: `features/search/src/telepiplex_search/direct_link.py`
- Modify: `features/search/src/telepiplex_search/media_metadata_v1.py`
- Create: `features/search/tests/test_series_topology.py`
- Modify: `features/search/tests/test_wikipedia_episode_inventory.py`
- Modify: `features/search/tests/test_media_metadata_v1.py`

- [x] Add failing pure tests for identical-profile merging, unique full-profile selection, unresolved ties, and preservation of all selected coordinates.
- [x] Implement immutable topology profile normalization and deterministic scoring from trusted totals, aired coverage, and requested scope.
- [x] Remove TVDB/TMDB set intersection from metadata v1; select or reject whole profiles.
- [x] Parse an explicit episode-list link from the exact Wikipedia root page, resolve only that exact linked page, and mark relationship provenance.
- [x] Keep Wikipedia coordinates authoritative and attach downstream episode IDs only at exact coordinates.
- [x] Emit `provider_order_conflict` rather than constructing a partial menu.
- [x] Run topology, Wikipedia inventory, metadata v1, and scope tests until green.

### Task 6: Align Version, Documentation, and Diagnostics

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Modify: `features/search/src/telepiplex_search/adapters/wikidata.py`
- Modify: `features/search/README.md`
- Modify: `features/search/tests/test_config_schema_contract.py`
- Modify: `features/search/tests/test_feature_service.py`

- [x] Add/adjust structured log assertions for discovery, locale, direct scope, topology choice, and final query title source.
- [x] Change every maintained 1.10.0 product/package reference to 1.11.0 without editing generated `build/` or egg-info artifacts.
- [x] Update README behavior and build example for the redesigned pipeline.
- [x] Run version-contract and logging tests.

### Task 7: Full Verification and Handoff

- [x] Run all Search tests with the bundled Python 3.12 runtime and bytecode/cache disabled.
- [x] Build `/tmp/search-1.11.0.tpx` with `tools/build_feature.py` and verify the archive.
- [x] Run the relevant root SDK/Host compatibility tests if Search contract changes cross that boundary.
- [x] Verify `.git` and `.worktrees` are absent and `.stfolder` remains present.
- [x] Review the acceptance checklist against actual test names and results.
- [x] List every changed file and its purpose, then remind the user to wait for Syncthing `Up to Date / 最新` before checking `/mnt/user/archives/life hacker/telepiplex` on Unraid.

## Verification Record

- Search suite: `430 passed, 2 skipped, 65 subtests passed`.
- Root Host/SDK suite: `461 passed, 1 skipped, 176 subtests passed`.
- Release/version contract focus: `13 passed, 3 subtests passed`.
- Package: `/tmp/search-1.11.0.tpx`; `unzip -t` passed and embedded manifest reports `plugin_id: search`, `version: 1.11.0`.
- Workspace boundary: `.git` absent, `.worktrees` absent, `.stfolder` present.
