# Chinese Metadata and Airing Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make verified Douban titles authoritative for Simplified Chinese and make same-QID Wikipedia episode tables drive ongoing-series scope menus, with TVDB/TMDB only as downstream enrichment or explicit fallback.

**Architecture:** Keep Wikipedia candidate discovery and frozen-link hydration unchanged at the top level. Add a pure MediaWiki episode-table parser behind the exact Wikipedia link resolver, normalize its output into the existing `media_metadata.items`, then make `series_scope` derive completed/incomplete state from that canonical inventory. Extend the existing Douban exact-link path with season-title cleanup and IMDb identity evidence, and only let a verified Douban fact replace `identity.chinese_title`.

**Tech Stack:** Python 3.12, standard-library `html.parser`, `requests`, `unittest`, `pytest`, existing telepiplex search contracts.

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex`; do not run Git or create `.git`/`.worktrees`.
- Product-facing text must spell the product name as lowercase `telepiplex`.
- Wikipedia remains the initial identity and normal episode-inventory source.
- Douban is the only authority allowed to write the verified Simplified Chinese title.
- TVDB/TMDB may provide episode IDs and metadata, and may provide inventory only when Wikipedia is absent, unavailable, or records a preserved parser error.
- A recognized Wikipedia episode table that fails to parse is `wikipedia_parse_error`, not `wikipedia_table_absent`.
- Unit tests use fixed HTML/API fixtures; live Wikipedia checks are opt-in diagnostics.
- After local verification, hand off only through Syncthing to `/mnt/user/archives/life hacker/telepiplex`.

---

### Task 1: Normalize Douban Season Titles and IMDb Evidence

**Files:**
- Modify: `features/search/src/telepiplex_search/adapters/douban.py`
- Test: `features/search/tests/test_douban_adapter.py`

**Interfaces:**
- Produces: `clean_douban_series_title(title: str, media_type: str) -> tuple[str, int | None]`
- Produces normalized fact fields: `douban_title_raw`, `chinese_title`, `season_number`, and `external_ids.imdb`.

- [x] **Step 1: Add failing normalization tests**

```python
def test_series_title_separates_verified_season_suffix(self):
    fact = douban._normalize_payload({
        "id": "1", "title": "副总统 第一季", "original_title": "Veep",
        "year": "2012", "type": "tv", "imdb": "tt1759761",
    }, "https://movie.douban.com/subject/1/")
    assert fact["douban_title_raw"] == "副总统 第一季"
    assert fact["chinese_title"] == "副总统"
    assert fact["season_number"] == 1
    assert fact["external_ids"]["imdb"] == "tt1759761"

def test_series_title_does_not_strip_sequel_number_or_unverified_part(self):
    assert douban.clean_douban_series_title("庆余年2", "series") == ("庆余年2", None)
    assert douban.clean_douban_series_title("第二部", "series") == ("第二部", None)
```

- [x] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_douban_adapter.py
```

Expected: failures for missing season cleanup and IMDb external ID.

- [x] **Step 3: Implement conservative suffix cleanup and IMDb extraction**

Use anchored suffix patterns for `第N季`, `Season N`, and `SNN`; convert supported Chinese numerals without stripping `第二部`, `Part 2`, or bare sequel digits. Read IMDb from known JSON keys and visible subject-detail text, normalize only `tt` followed by digits, and merge it into `external_ids` without calling IMDb.

- [x] **Step 4: Re-run the focused adapter tests**

Expected: all Douban adapter tests pass.

### Task 2: Enforce Verified Douban Chinese-Title Authority

**Files:**
- Modify: `features/search/src/telepiplex_search/adapters/wikidata.py`
- Modify: `features/search/src/telepiplex_search/confirmed_enrichment.py`
- Modify: `features/search/src/telepiplex_search/title_policy.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Test: `features/search/tests/test_wikidata_adapter.py`
- Test: `features/search/tests/test_confirmed_enrichment.py`
- Test: `features/search/tests/test_title_policy.py`

**Interfaces:**
- `ConfirmedIdentity.external_ids` carries `wikidata`, `imdb`, `tmdb`, and `tvdb` when known.
- Produces: `douban_identity_match(fact: dict, identity: ConfirmedIdentity) -> str`, returning `imdb_exact`, `strong_fields`, or an empty string.
- `select_unique_douban_fact(...)` only returns facts with an approved match mode.

- [x] **Step 1: Add failing identity and priority tests**

```python
def test_douban_imdb_exact_match_beats_regional_wikipedia_title():
    selected = select_unique_douban_fact({"status": "ok", "facts": [{
        "subject_id": "1", "chinese_title": "副总统", "english_title": "Veep",
        "media_type": "series", "year": "2012",
        "external_ids": {"douban_subject": "1", "imdb": "tt1759761"},
    }]}, identity(chinese_title="副人之仁", english_title="Veep",
                 external_ids={"wikidata": "Q74801", "imdb": "tt1759761"}))
    assert selected["douban_match_mode"] == "imdb_exact"

def test_title_policy_prefers_verified_douban_over_wikipedia():
    candidate = CandidateEntity("veep", (
        EvidenceFact(
            fact_id="wikipedia:Q74801", provider="wikipedia",
            titles=("副人之仁", "Veep"), year="2012", media_type="series",
            external_ids={"wikidata": "Q74801", "imdb": "tt1759761"},
            chinese_title="副人之仁", official_english_title="Veep",
        ),
        EvidenceFact(
            fact_id="douban:5379824", provider="douban",
            titles=("副总统", "Veep"), year="2012", media_type="series",
            external_ids={"douban_subject": "5379824", "imdb": "tt1759761"},
            chinese_title="副总统", official_english_title="Veep",
        ),
    ))
    titles = resolve_title_policy(candidate)
    assert titles.chinese_title == "副总统"
```

- [x] **Step 2: Run the focused tests and verify the current weak matcher fails**

Run the three test modules with the feature Python runtime. Expected: Wikidata lacks P345, the matcher lacks match modes, and Wikipedia wins the Chinese-title policy.

- [x] **Step 3: Expose Wikidata P345 and implement the match gate**

Add normalized P345 to `external_ids.imdb`. Accept exact shared IMDb first. Without shared IMDb, require exact English/original root title, media type, one unique candidate, and at least two matching fields among premiere year, original language/country, cast/creator, and season number. Title-only matches return no fact.

- [x] **Step 4: Always perform the bounded Douban lookup after a Wikipedia identity is confirmed**

Remove the current `not has_simple_chinese` guard. Query Douban with the confirmed English/original title and year, append the fixed Douban link only when the new gate succeeds, and preserve `douban_identity_unverified` otherwise.

- [x] **Step 5: Change title selection priority and provenance**

Select Chinese titles in this order: verified Douban, user-entered exact title only when it equals the verified Douban root, then non-authoritative aliases for display fallback. Record `douban_title_raw`, subject ID, selected value, and `douban_match_mode` under field-source evidence.

- [x] **Step 6: Run focused identity/title tests**

Expected: `副总统` and `百年孤独` win only after verified Douban binding; title-only candidates remain unresolved.

### Task 3: Parse Exact Wikipedia Episode Tables

**Files:**
- Create: `features/search/src/telepiplex_search/wikipedia_episode_inventory.py`
- Modify: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Create: `features/search/tests/fixtures/wikipedia/one_hundred_years_zh.html`
- Create: `features/search/tests/fixtures/wikipedia/one_hundred_years_en.html`
- Test: `features/search/tests/test_wikipedia_episode_inventory.py`
- Test: `features/search/tests/test_wikipedia_adapter.py`

**Interfaces:**
- Produces: `parse_wikipedia_episode_html(html: str, *, language: str, source_url: str, revision_id: int) -> dict`
- Produces: `lookup_wikipedia_episode_page(language: str, title: str, *, timeout: float = 10) -> dict`
- Result keys: `status`, `items`, `season_totals`, `source_url`, `revision_id`, `error`.

- [x] **Step 1: Save fixed same-QID HTML fixtures and add failing parser tests**

```python
def test_same_qid_pages_merge_to_two_seasons_and_future_finale():
    fixture_root = Path(__file__).parent / "fixtures" / "wikipedia"
    zh = parse_wikipedia_episode_html(
        (fixture_root / "one_hundred_years_zh.html").read_text(encoding="utf-8"),
        language="zh", source_url="https://zh.wikipedia.org/wiki/百年孤独_(电视剧)",
        revision_id=100,
    )
    en = parse_wikipedia_episode_html(
        (fixture_root / "one_hundred_years_en.html").read_text(encoding="utf-8"),
        language="en", source_url="https://en.wikipedia.org/wiki/One_Hundred_Years_of_Solitude_(TV_series)",
        revision_id=200,
    )
    zh["wikibase_item"] = en["wikibase_item"] = "Q124175370"
    merged = merge_wikipedia_episode_results(
        zh, en, expected_qid="Q124175370"
    )
    assert merged["season_totals"] == {1: 8, 2: 8}
    items = {
        (item["season_number"], item["episode_number"]): item
        for item in merged["items"]
    }
    assert set(items) == (
        {(1, n) for n in range(1, 9)}
        | {(2, n) for n in range(1, 9)}
    )
    assert items[(2, 8)]["air_date"] == "2026-08-26"
```

- [x] **Step 2: Run the parser tests and verify the module is missing**

Expected: import failure for `wikipedia_episode_inventory`.

- [x] **Step 3: Implement a pure standard-library HTML parser**

Recognize MediaWiki episode tables from table classes and header labels, retain heading season context, read ISO dates from the rendered table content, ignore non-episode rows, and return `parse_error` when a recognized table yields no valid rows.

- [x] **Step 4: Add exact `action=parse` transport**

Request only the confirmed page with `action=parse`, `page=<exact title>`, `prop=text|revid|displaytitle`, `format=json`, and the existing throttle/user agent. Map 404, 429, timeout, and server errors to existing Wikipedia statuses. Do not issue candidate search queries.

- [x] **Step 5: Run parser and adapter tests**

Expected: fixed fixtures parse deterministically and transport tests prove one exact request per page.

### Task 4: Reconcile Wikipedia Inventory and Downstream IDs

**Files:**
- Modify: `features/search/src/telepiplex_search/wikipedia_episode_inventory.py`
- Modify: `features/search/src/telepiplex_search/direct_link.py`
- Modify: `features/search/src/telepiplex_search/media_metadata_v1.py`
- Test: `features/search/tests/test_wikipedia_episode_inventory.py`
- Test: `features/search/tests/test_direct_link.py`
- Test: `features/search/tests/test_media_metadata_v1.py`

**Interfaces:**
- Produces: `merge_wikipedia_episode_results(primary: dict, secondary: dict | None, *, expected_qid: str) -> dict`
- Produces canonical item fields: `item_id`, `season_number`, `episode_number`, `aired`, `inventory_source`, `source_url`, `revision_id`, optional TVDB/TMDB episode IDs.

- [x] **Step 1: Add failing merge and source-priority tests**

Cover partial Chinese plus complete English, wrong QID rejection, date conflicts becoming `unknown`, and a TVDB one-season inventory not overriding a Wikipedia two-season inventory.

- [x] **Step 2: Run tests and verify current `_inventory` chooses TVDB first**

Expected: the source-priority regression fails in `media_metadata_v1._inventory`.

- [x] **Step 3: Integrate exact page parsing into frozen Wikipedia link hydration**

After the exact Wikipedia page and Wikidata media type are confirmed as `series`, parse that page. If partial, read its exact English langlink, verify the same QID, and merge the English result. Store table status, source revisions, totals, and errors on the Wikipedia fact.

- [x] **Step 4: Make contract inventory Wikipedia-first**

Change `_inventory` provider order to Wikipedia, TVDB, TMDB. When Wikipedia items exist, attach uniquely matching TVDB/TMDB episode IDs by coordinate without changing Wikipedia dates or coordinates. When Wikipedia is absent/unavailable/parse-error, reconcile TVDB/TMDB fallback and preserve the Wikipedia error in evidence.

- [x] **Step 5: Run direct-link and contract tests**

Expected: Wikipedia coordinates survive hydration and downstream providers only enrich IDs.

### Task 5: Model Incomplete Seasons Conservatively

**Files:**
- Modify: `features/search/src/telepiplex_search/series_scope.py`
- Test: `features/search/tests/test_series_scope.py`

**Interfaces:**
- Extend `SeriesInventory` with `season_totals` and `state_by_season`.
- Produces state values `completed`, `incomplete`, and `unknown`.
- `_aired(value, today)` returns false for missing or malformed dates.

- [x] **Step 1: Add failing airing-state tests**

```python
def test_missing_date_is_unknown_not_aired():
    value = contract(seasons=())
    value["items"] = [{
        "item_id": "s1e1", "content_role": "main_episode",
        "season_number": 1, "episode_number": 1, "aired": "",
    }]
    value["evidence"]["series_inventory"] = {"season_totals": {1: 1}}
    inventory = series_inventory(value, today=date(2026, 8, 14))
    assert inventory.aired_by_season == {}
    assert inventory.state_by_season[1] == "unknown"

def test_one_hundred_years_is_completed_then_incomplete():
    value = contract(seasons=())
    value["items"] = [
        *({
            "item_id": f"s1e{number}", "content_role": "main_episode",
            "season_number": 1, "episode_number": number,
            "aired": "2024-12-11",
        } for number in range(1, 9)),
        *({
            "item_id": f"s2e{number}", "content_role": "main_episode",
            "season_number": 2, "episode_number": number,
            "aired": "2026-08-05" if number < 8 else "2026-08-26",
        } for number in range(1, 9)),
    ]
    value["evidence"]["series_inventory"] = {
        "season_totals": {1: 8, 2: 8},
    }
    inventory = series_inventory(value, today=date(2026, 8, 14))
    assert inventory.state_by_season == {1: "completed", 2: "incomplete"}
    assert inventory.aired_by_season[2] == tuple(range(1, 8))
```

- [x] **Step 2: Run scope tests and verify missing dates are currently treated as aired**

Expected: the new missing-date and state tests fail.

- [x] **Step 3: Implement totals and conservative state derivation**

Read Wikipedia totals from contract evidence, treat future and unknown episodes as incomplete/unknown, and only mark completed when all known regular episodes are aired with no known gap.

- [x] **Step 4: Run all `test_series_scope.py` tests**

Expected: existing exact episode queries still pass and new state assertions pass.

### Task 6: Render the Ongoing-Season Three-Level Menu

**Files:**
- Modify: `features/search/src/telepiplex_search/service.py`
- Test: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Top-level incomplete-season callback remains `search:scope:<plan_id>:season:<N>`.
- Incomplete-season callback returns an episode keyboard containing only verified aired coordinates.
- Completed-season callback immediately applies the whole-season scope.

- [x] **Step 1: Add failing menu behavior tests**

For the fixed 2026-08-14 contract assert top-level buttons are `第一季（全季）` and `第二季（已播 7/8）`, no `全剧` button exists, clicking season 2 yields seven episode buttons, and S02E08 cannot be selected.

- [x] **Step 2: Run the focused service tests and verify the current menu exposes the wrong shape**

Expected: current service shows `全剧` and a generic season menu, or exposes future episodes from `all_by_season`.

- [x] **Step 3: Implement state-aware callbacks**

Keep completed seasons as direct `season` searches. Route incomplete/unknown seasons to an episode submenu built from `aired_by_season`. Hide `whole_series` whenever any season is incomplete/unknown, including one-season ongoing series. Preserve manual numeric input validation through `apply_series_scope`.

- [x] **Step 4: Add structured scope diagnostics**

Log inventory source, Wikipedia table status/revision, per-season state, aired/total counts, fallback reason, hidden whole-series reason, and hidden future/unknown counts through `log_search_event`.

- [x] **Step 5: Run focused feature-service tests**

Expected: the three-level menu and callbacks pass without changing movie or completed-series flows.

### Task 7: End-to-End Regression and Local Handoff Verification

**Files:**
- Test: `features/search/tests/test_live_search_usability.py`
- Modify: `features/search/docs/superpowers/specs/2026-08-14-chinese-metadata-and-airing-scope-design.md`
- Modify: `features/search/docs/superpowers/plans/2026-08-14-chinese-metadata-and-airing-scope.md`

**Interfaces:**
- No new public Host capability or IMDb credential.
- Existing `/s` callbacks and Prowlarr query formats remain compatible.

- [x] **Step 1: Run the focused regression set**

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_douban_adapter.py \
  tests/test_wikidata_adapter.py \
  tests/test_confirmed_enrichment.py \
  tests/test_title_policy.py \
  tests/test_wikipedia_adapter.py \
  tests/test_wikipedia_episode_inventory.py \
  tests/test_direct_link.py \
  tests/test_media_metadata_v1.py \
  tests/test_series_scope.py \
  tests/test_feature_service.py
```

- [x] **Step 2: Run the complete search feature suite**

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
```

- [x] **Step 3: Run opt-in live Wikipedia diagnostics for the exact accepted sample**

Read the current same-QID Chinese/English pages for *One Hundred Years of Solitude*. If the page still presents standard episode tables but the parser returns no inventory, classify it as an implementation defect and fix it before completion. Do not make the live request a mandatory offline unit test.

- [x] **Step 4: Verify the Mac-local boundary**

```bash
cd /Users/young/Documents/telepiplex
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

- [x] **Step 5: Record actual results and hand off**

List every modified/created file, the exact tests run and their pass counts, any live diagnostic status, and remind the user to wait for Syncthing `Up to Date / 最新`. Do not publish or run Git.

## Actual Result (2026-08-14)

- Full Search feature suite: `414 passed, 2 skipped, 65 subtests passed`.
- Live same-QID Wikipedia diagnostic: Chinese revision `93821395` parsed as 15 partial flat rows; English revision `1367933110` parsed as 16 complete rows with `{1: 8, 2: 8}`; merged inventory was complete.
- Fixed-date acceptance at `2026-08-14`: season 1 `completed`, season 2 `incomplete`, aired counts `{1: 8, 2: 7}`, scheduled counts `{2: 1}`.
- Mac-local boundary: `.git` absent, `.worktrees` absent, `.stfolder` present.
- No Git, publication, release, or external write was performed.
