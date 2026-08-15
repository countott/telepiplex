# Search/Rename Regression Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver search 1.11.1 and rename 1.5.2 with correct Honey and Clover season parsing and Chinese presentation, root-year-safe TVDB enrichment, actionable media metadata validation, and verified source-directory cleanup.

**Architecture:** Keep deterministic identity and file-resolution stages intact, but add explicit semantic boundaries around locale, series root year, contract diagnosis, and post-move cleanup. Every behavior is introduced through a failing regression test, then exercised by bounded in-process pressure loops before the full repository test and package gates.

**Tech Stack:** Python 3.12, unittest/pytest, dataclasses, existing telepiplex Feature SDK, `tools/build_feature.py`.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`; do not run Git, create `.git`/`.worktrees`, publish, or connect this Mac checkout to GitHub.
- Keep user-facing product copy lowercase `telepiplex`; retain existing technical identifiers.
- Target search version is `1.11.1`; target rename version is `1.5.2`.
- Do not edit generated `build/`, `*.egg-info`, bytecode, or cache artifacts.
- Use `/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3` with `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`.
- Preserve the legacy `validate_media_metadata(...) -> dict | None` contract while adding structured validation details.
- Package only to `/tmp/search-1.11.1.tpx` and `/tmp/rename-1.5.2.tpx`.

---

### Task 1: Season marker grammar and Honey and Clover regression

**Files:**
- Modify: `features/rename/src/telepiplex_rename/content_probe.py`
- Create: `features/rename/tests/test_regression_pressure.py`

**Interfaces:**
- Consumes: `build_metadata_probe(payload: dict) -> dict`.
- Produces: season-range grammar where only `S1-S2` or `Season 1-Season 2` expands a range; `S1 - 01` remains season 1, episode 1.

- [ ] **Step 1: Add failing literal regressions**

```python
def test_honey_and_clover_s1_dash_episode_is_not_season_range():
    probe = build_metadata_probe({
        "resource_name": "Honey and Clover",
        "file_tree": [{
            "relative_path": "Honey and Clover S1 - 01.mkv",
            "is_dir": False,
        }],
    })
    assert probe["observed_seasons"] == [1]
    assert probe["observed_episodes"] == [{"season": 1, "episode": 1}]


def test_explicit_second_season_marker_expands_range():
    probe = build_metadata_probe({
        "resource_name": "Honey and Clover S1-S2",
        "file_tree": [],
    })
    assert probe["observed_seasons"] == [1, 2]
```

- [ ] **Step 2: Run both tests and verify the first fails because season 0 is synthesized**

Run: `cd features/rename && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_regression_pressure.py -k 'honey or explicit'`

- [ ] **Step 3: Require an explicit second season marker**

```python
_SEASON_RANGE = re.compile(
    r"(?ix)(?:\bS(\d{1,2})\s*(?:-|~|TO)\s*S(\d{1,2})\b|"
    r"\bSeason[ ._-]+(\d{1,2})\s*(?:-|~|TO)\s*Season[ ._-]+(\d{1,2})\b)"
)
```

Read either capture pair in `_observed_markers` and leave episode parsing unchanged.

- [ ] **Step 4: Add the 38-file Honey and Clover fixture and a 10,000-name pressure loop**

The fixture contains season 1 names `S1 - 01` through `S1 - 24` and season 2 names `S2 - 01` through `S2 - 14`; assert exactly seasons 1/2 and 38 unique coordinates. The pressure loop cycles explicit episodes, explicit ranges, `SxxEyy`, and `Season xx` literals and asserts no season 0 and no expansion from `S1 - 01`.

- [ ] **Step 5: Run the complete rename regression-pressure file and the existing content-probe tests**

Run: `cd features/rename && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_regression_pressure.py tests/test_feature_processor.py -k 'probe or pressure or honey'`

### Task 2: Chinese title semantics and bounded candidate localization

**Files:**
- Modify: `features/search/src/telepiplex_search/candidate_preview.py`
- Modify: `features/search/src/telepiplex_search/media_metadata_v1.py`
- Modify: `features/search/src/telepiplex_search/candidate_locale.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/search_plan.py`
- Modify: `features/search/src/telepiplex_search/identity_presentation.py`
- Modify: `features/search/tests/test_candidate_locale.py`
- Modify: `features/search/tests/test_media_metadata_v1.py`
- Modify: `features/search/tests/test_feature_service.py`
- Create: `features/search/tests/test_regression_pressure.py`

**Interfaces:**
- Consumes: frozen candidate dictionaries and deterministic Douban facts.
- Produces: `localize_candidate_from_verified_douban(candidate, fact, match_mode=...) -> dict`; candidate preview localization limited to the first five candidates; semantic `identity.chinese_title` never contains a Latin fallback.

- [ ] **Step 1: Add failing semantic-title tests**

Add one candidate with only `Honey and Clover`/`ハチミツとクローバー` and assert `identity.chinese_title == ""`, while presentation still displays the Latin title and reports `title_status == "latin_fallback"`. Add a verified Douban fact and assert the localized candidate displays `蜂蜜与四叶草` before selection.

- [ ] **Step 2: Run the new title tests and verify they fail on the current Latin fallback**

Run: `cd features/search && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_candidate_locale.py tests/test_media_metadata_v1.py tests/test_regression_pressure.py -k 'semantic or verified_douban or latin_fallback'`

- [ ] **Step 3: Remove semantic fallbacks and preserve presentation fallback**

Set preview and hydrated `identity.chinese_title` only from verified `fact.chinese_title`. Let draft-plan validation require at least one Chinese or English identity title. Add `title_status` to `build_identity_presentation`: `verified_chinese` when Chinese exists, otherwise `latin_fallback`; continue calculating the visible title through `_title`.

- [ ] **Step 4: Add verified Douban localization and bounded fallback lookup**

Factor exact and strong-field application through a shared locale updater. Exact P4529 lookup remains first. For candidates without P4529, query Douban only for candidates 1–5, call `select_unique_douban_fact`, and apply only a unique `strong_fields` match. Keep candidates after index 5 untouched and cap concurrent localization at four.

- [ ] **Step 5: Rebuild the selected contract after supplementation**

When selected-candidate supplementation verifies Douban, update the selected candidate through `localize_candidate_from_verified_douban`; the existing frozen-candidate hydration then rebuilds `media_metadata`, queries, and field-source evidence from the supplemented exact links.

- [ ] **Step 6: Add a 1,000-candidate-fact pressure matrix**

Cycle exact, unique strong-field, ambiguous, unavailable, and Latin-only cases. Assert only exact/unique facts assign Chinese, ambiguity never assigns a Chinese title, no Latin string appears in `chinese_title`, and no preview batch invokes the provider more than five times.

- [ ] **Step 7: Run focused search locale and service suites**

Run: `cd features/search && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_candidate_locale.py tests/test_media_metadata_v1.py tests/test_identity_presentation.py tests/test_feature_service.py tests/test_regression_pressure.py`

### Task 3: Series root-year-safe TVDB enrichment

**Files:**
- Modify: `features/search/src/telepiplex_search/media_metadata_v1.py`
- Modify: `features/search/src/telepiplex_search/confirmed_enrichment.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/tests/test_confirmed_enrichment.py`
- Modify: `features/search/tests/test_regression_pressure.py`

**Interfaces:**
- Consumes: confirmed identity external IDs, `root_year`, optional `scope_year`, and supplemental Wikipedia/TMDB facts.
- Produces: `build_tvdb_query(...)` with `tvdb_id` when available and a year that always comes from the series root, never a season/supplement fact.

- [ ] **Step 1: Add failing House of the Dragon tests**

Build a confirmed series with `root_year="2022"`, `scope_year="2024"`, a supplemental fact year `2011`, and assert the TVDB query uses `2022`. Add `external_ids={"tvdb": "371572"}` and assert direct TVDB-ID enrichment bypasses title/year search.

- [ ] **Step 2: Run the tests and verify the current query selects 2011 and lacks direct ID routing**

Run: `cd features/search && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_confirmed_enrichment.py tests/test_regression_pressure.py -k 'root_year or house_of_the_dragon or direct_tvdb'`

- [ ] **Step 3: Persist and consume root/scope year separately**

Add `root_year` and `scope_year` fields to generated identity data. Extend `ConfirmedIdentity` with defaulted fields. Build TVDB queries from stable TVDB ID first and otherwise from `root_year or year`; never take the supplemental fact year.

- [ ] **Step 4: Route stable TVDB IDs directly**

In selected-candidate supplementation, call `get_tvdb_series(tvdb_id)` directly when `build_tvdb_query` supplies `tvdb_id`; use `search_tvdb_series(title, root_year)` only when no stable ID exists.

- [ ] **Step 5: Extend the 1,000-case fact matrix and run the complete enrichment tests**

Assert every season-year mismatch retains the root year and every stable-ID case avoids search. Run: `cd features/search && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_confirmed_enrichment.py tests/test_candidate_hydration.py tests/test_regression_pressure.py`.

### Task 4: Structured media metadata contract diagnosis

**Files:**
- Modify: `sdk/src/telepiplex_plugin_sdk/media_metadata.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/__init__.py`
- Modify: `features/search/src/telepiplex_search/search_plan.py`
- Modify: `tests/test_host_media_metadata.py`
- Modify: `features/search/tests/test_search_plan.py`
- Create: `tests/test_media_metadata_pressure.py`

**Interfaces:**
- Consumes: any media metadata object and `require_confirmed` flag.
- Produces: `validate_media_metadata_detailed(value, require_confirmed=False) -> tuple[dict | None, dict | None]`; issue dictionaries contain exact `path`, `reason_code`, and `detail`; legacy validation remains unchanged.

- [ ] **Step 1: Add failing path/reason tests**

Cover wrong schema version, non-serializable values, missing metadata ID, unconfirmed contract, missing identity/placement, invalid category/library pair, invalid mapping, invalid items, invalid standalone scope, and invalid TVDB-official IDs. Assert literal paths such as `$.schema_version`, `$.metadata_id`, and `$.placement.category_kind`.

- [ ] **Step 2: Verify the detailed validator is missing**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_host_media_metadata.py tests/test_media_metadata_pressure.py features/search/tests/test_search_plan.py -k 'detailed or diagnostic'`.

- [ ] **Step 3: Add a non-breaking diagnostic layer**

Keep the current validator as the source of acceptance truth. `validate_media_metadata_detailed` returns `(validated, None)` on success; on failure it deterministically diagnoses the first violated rule and returns `(None, {"path": ..., "reason_code": ..., "detail": ...})`, with `$.` fallback only for an unclassified invariant.

- [ ] **Step 4: Surface details from search-plan finalization**

Use the detailed validator in `finalize_search_plan` and include path/reason/detail in the raised `ValueError`, while retaining existing `validate_draft_search_plan` and `validate_media_metadata` callers.

- [ ] **Step 5: Add and run 10,000 contract pressure cases**

Cycle a valid contract and one-field mutations of all diagnosed branches. Assert valid contracts round-trip identically, invalid contracts always return one complete issue, and legacy validation still returns `None` for every invalid mutation.

### Task 5: Explicit post-move cleanup and neutral completion results

**Files:**
- Modify: `features/rename/src/telepiplex_rename/file_executor.py`
- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/tests/test_file_executor.py`
- Modify: `features/rename/tests/test_file_first_processor.py`
- Modify: `features/rename/tests/test_regression_pressure.py`

**Interfaces:**
- Consumes: verified file outcomes, source resolution paths, automatic-download/manual-inventory boundary, and protected category roots.
- Produces: `cleanup_source_directories(...) -> DirectoryCleanupSummary`; `file_first["cleanup"]` and public `file_results["cleanup"]`; `cleanup_complete` reflects actual cleanup failures rather than only file moves.

- [ ] **Step 1: Add failing automatic/manual cleanup tests**

Automatic download: move the only file from `/Downloads/House.Release` and assert that release root is freshly listed, deleted, and reported. Manual inventory: move the only file from `/Series/UserSelected` and assert the scan root is preserved. Category root: assert `/Series` is never a deletion candidate. Provider delete failure: assert the cleanup summary is incomplete and names the failed path.

- [ ] **Step 2: Run tests and verify production currently never calls cleanup**

Run: `cd features/rename && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_file_executor.py tests/test_file_first_processor.py -k 'cleanup or release_root or scan_root'`.

- [ ] **Step 3: Add structured cleanup outcomes**

Represent every source-directory candidate as `deleted`, `retained_nonempty`, `protected`, `lookup_failed`, or `delete_failed`. List deepest paths first, perform a fresh provider listing before each deletion, and never delete a protected root.

- [ ] **Step 4: Invoke cleanup after verified file execution**

Automatic downloads include the selected release root as a candidate and protect `event.selected_path`; manual/inventory runs protect their scan root. Attach the summary to file-first output. Set `rename_plan["cleanup_complete"]` only when file execution has no failures and cleanup has no lookup/delete failures.

- [ ] **Step 5: Make completion messaging neutral and source-aware**

Use `媒体整理结果`/`电影整理结果` instead of unconditional success language. Include deleted/retained/cleanup-failed counts. Show a TVDB line only when `tvdb_series_id` exists.

- [ ] **Step 6: Add 10,000-file/500-directory cleanup pressure and replay tests**

Use a deterministic in-memory provider with mixed empty, retained, failed, and protected directories. Assert bottom-up ordering, exact counts, no category/manual-root deletion, no deletion before target verification, and zero second-run mutations for replayed file outcomes.

- [ ] **Step 7: Run focused rename execution and processor suites**

Run: `cd features/rename && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_file_executor.py tests/test_file_first_processor.py tests/test_feature_processor.py tests/test_regression_pressure.py`.

### Task 6: Version, documentation, full pressure, and package gates

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Modify: `features/search/src/telepiplex_search/adapters/wikidata.py`
- Modify: `features/search/tests/test_config_schema_contract.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/pyproject.toml`
- Modify: `features/rename/README.md`
- Modify: `features/rename/tests/test_feature_processor.py`

**Interfaces:**
- Produces: aligned source release identities for search 1.11.1 and rename 1.5.2, plus verified `.tpx` artifacts in `/tmp`.

- [ ] **Step 1: Update maintained version and release-instruction references**

Replace current source/test/README release identity with search `1.11.1` and rename `1.5.2`; leave archived historical specs/plans and generated artifacts untouched.

- [ ] **Step 2: Run the three explicit pressure files together**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src:features/search/src:features/rename/src "$PY" -m pytest -q -p no:cacheprovider features/search/tests/test_regression_pressure.py features/rename/tests/test_regression_pressure.py tests/test_media_metadata_pressure.py`.

- [ ] **Step 3: Run Core and all five Feature suites**

Run the exact repository command from `AGENTS.md` for Core, then `download`, `search`, `rename`, `sync`, and `caption`.

- [ ] **Step 4: Verify workspace boundary markers**

Run: `test ! -e .git && test ! -e .worktrees && test -d .stfolder`.

- [ ] **Step 5: Build and inspect release artifacts**

Run `tools/build_feature.py` for both `/tmp` targets, `unzip -t` both archives, extract each embedded `manifest.yaml` to stdout, and assert exact plugin ID/version pairs.

- [ ] **Step 6: Re-read this plan and the approved design against actual evidence**

Record every changed file, every command exit code and test count, pressure-case totals, package results, and any remaining limitation. Do not report completion until fresh full-suite and package evidence is available.

- [ ] **Step 7: Hand off locally**

Tell the user to wait for Syncthing `Up to Date / 最新`, then inspect and publish only from `/mnt/user/archives/life hacker/telepiplex` on Unraid.
