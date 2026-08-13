# Real Search Pipeline Stress Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a repeatable, large real-world media corpus through the deterministic Search pipeline, fix every stable defect found, and release a new Search patch version.

**Architecture:** A checked-in corpus describes expected root works without storing provider payloads. An opt-in live audit uses public Wikipedia/Wikidata for broad discovery, then exact-reads a bounded representative subset through frozen identity, metadata v1, regular-series scope, final query, and downstream contract serialization. Ordinary unit tests remain network-free; every live defect becomes a deterministic fixture regression before production code changes.

**Tech Stack:** Python 3.12, asyncio, MediaWiki Action API, Wikidata entity API, pytest, telepiplex plugin SDK.

## Global Constraints

- Mac-local only; do not run Git or create `.git` or `.worktrees`.
- Use `apply_patch` for source, test, corpus, and documentation edits.
- Search accepts explicit titles only and never calls AI.
- Public-source stress tests must not submit Prowlarr results or downloads.
- Exact provider credentials are read only from an explicitly selected local Search config and never logged.
- Season 0 and specials remain excluded.
- Every production fix requires a witnessed failing regression test first.
- After stable fixes, bump Search from `1.9.0` to the next patch version in every version identity.

---

### Task 1: Checked-in Real-world Corpus and Audit Report

**Files:**
- Create: `features/search/src/telepiplex_search/live_pipeline_audit.py`
- Create: `features/search/tools/run_live_pipeline_audit.py`
- Create: `features/search/tests/fixtures/real_media_corpus.json`
- Create: `features/search/tests/test_live_pipeline_audit.py`
- Modify: `features/search/tests/test_live_search_usability.py`

**Interfaces:**
- `load_real_media_corpus(path) -> list[dict]` validates explicit query, expected year/type, country group, and audit depth.
- `audit_root_case(case, feature) -> dict` reports parse, Wikipedia, Wikidata, filtering, ordering, and candidate contract stages.
- `audit_full_case(case, feature) -> dict` additionally exact-reads the expected frozen candidate and validates metadata/query/scope serialization without release submission.

- [ ] Add a failing corpus contract test requiring at least 60 works, at least 12 country groups, movies and series, ambiguous titles, one-season/multi-season series, explicit seasons/episodes, and Japanese animation.
- [ ] Run the focused test and confirm the corpus/audit module is absent.
- [ ] Add the validated corpus and minimal report dataclasses/helpers.
- [ ] Run the focused network-free corpus tests green.

### Task 2: Broad Public Root Discovery

**Files:**
- Modify only after a failing fixture proves a defect: `features/search/src/telepiplex_search/work_discovery.py`
- Modify only after a failing fixture proves a defect: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Modify only after a failing fixture proves a defect: `features/search/src/telepiplex_search/adapters/wikidata.py`
- Add regression fixtures/tests beside the affected module.

**Interfaces:**
- Root discovery must find the expected `(year, media_type)` identity from real Wikipedia/Wikidata results.
- Ambiguous queries must retain distinct valid roots in provider relevance order.
- Non-media entities and season/episode pages must not become root works.

- [ ] Run at least 60 real root cases and capture structured failures per boundary.
- [ ] For each stable failure, reproduce with the smallest saved provider fixture and watch the regression fail.
- [ ] Apply one root-cause fix at a time and run the focused test green.
- [ ] Re-run the entire live root corpus and record pass/fail totals and latency.

### Task 3: Bounded Full Pipeline Audit

**Files:**
- Modify only after failures: Search enrichment, hydration, metadata, scope, or query modules.
- Modify: `features/search/tests/test_live_pipeline_audit.py`

**Interfaces:**
- The selected expected root remains the anchor through exact-link hydration.
- `media_metadata v1` has confirmed identity, placement, deterministic evidence, and bounded query variants.
- Series scope uses TVDB, TMDB, or explicit Wikipedia structure; special coordinates are absent.
- The contract survives SDK attach/extract round-trip and produces Download/Rename/Sync-compatible payload keys.

- [ ] Run at least 20 representative real works through frozen selection and exact page hydration.
- [ ] Validate movie, whole-series, season, and episode query shapes plus simplified Chinese-or-English display policy.
- [ ] Convert every stable failure to a network-free failing regression, fix minimally, and re-run focused tests.
- [ ] Re-run the bounded full corpus and save a machine-readable summary outside the repository temporary path.

### Task 4: Version, Documentation, and Full Verification

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `features/search/src/telepiplex_search.egg-info/SOURCES.txt`
- Modify: `features/search/README.md`
- Modify: Host version/release contract tests.

**Interfaces:**
- Search version identities match the new patch version.
- The package contains the audit module/tool but no removed Search AI modules.

- [ ] Add the next-version expectation and witness it fail.
- [ ] Update version identities, README audit instructions, package sources, and Host release expectations.
- [ ] Run Search, Host, all five Feature suites, and build the `.tpx` artifact.
- [ ] Inspect the inner wheel for required audit modules and absence of deleted Search AI modules.
- [ ] Verify `.git` and `.worktrees` are absent and `.stfolder` remains present.
