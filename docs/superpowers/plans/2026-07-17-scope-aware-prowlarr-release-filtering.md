# Scope-Aware Prowlarr Release Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a bounded `/s` pipeline that sends simple canonical queries to Prowlarr, rejects wrong media identities and scopes before quality scoring, restores detailed Indexer/score output, and hands renaming a structured identity probe instead of a concatenated file tree.

**Architecture:** `media-search` keeps input parsing, canonical identity resolution, release correctness, and release quality as separate units. A new `release_gate.py` classifies every Prowlarr result against confirmed `media_metadata`; only eligible results enter the existing configurable quality scorer. `renaming` adds a focused `content_probe.py` that derives one identity query from the download root and carries file-tree shape separately.

**Tech Stack:** Python 3.12.13, stdlib `unittest`, Telepiplex plugin SDK 1.1.0, requests, PyYAML.

## Global Constraints

- Supported retrieval scopes are exactly `movie`, `whole_series`, `season`, and `episode`.
- Special, Specials, SP, OVA, OAD, Extra, Extras, and Bonus are not positive retrieval scopes.
- Non-Japanese media use verified official English titles; Japanese media use verified official romaji.
- Prowlarr queries do not append `Complete`, a default year, quality terms, release groups, or S00 markers.
- Entity candidates are request-scoped, capped at 7, and never persisted.
- Release identity and scope are hard gates; they cannot add or subtract Preference Score.
- Exact-scope release results are capped at 12 after scoring and are rendered in three-button rows.
- No exact release result means no automatic scope downgrade.
- `/s` downloads carry confirmed `media_metadata`; renaming cannot re-resolve their identity.
- Manual `/m` resolution uses a root-derived query plus structured shape; 0 or multiple candidates go to `/未整理`.
- All behavioral changes are developed with failing tests first.

---

### Task 1: Normalize the Bounded User Query Contract

**Files:**
- Create: `src/telepiplex_media_search/query_normalization.py`
- Modify: `src/telepiplex_media_search/input_contract.py`
- Modify: `src/telepiplex_media_search/search_resolution.py`
- Modify: `src/telepiplex_media_search/direct_link.py`
- Modify: `src/telepiplex_media_search/service.py`
- Test: `tests/test_input_contract.py`
- Test: `tests/test_direct_link.py`

**Interfaces:**
- Produces: `normalize_query_text(value: str) -> str`.
- Produces: `has_unsupported_range_syntax(value: str) -> bool`.
- `classify_search_input()` returns `ParsedInput(kind="unsupported_text", reason="unsupported_scope_syntax")` for ranges and English number-word season syntax.
- Direct TVDB S00 episode links raise `DirectLinkError("unsupported_special_scope")`.

- [ ] **Step 1: Write failing normalization and grammar tests**

```python
def test_nfkc_and_punctuation_keep_full_title(self):
    parsed = classify_search_input("《蝙蝠侠：黑暗骑士》")
    self.assertEqual(parsed.title, "蝙蝠侠 黑暗骑士")

def test_numeric_english_season_and_episode_are_supported(self):
    season = classify_search_input("The Glory Season 01")
    episode = classify_search_input("The Glory Season 1 Episode 2")
    self.assertEqual((season.scope, season.season_number), ("season", 1))
    self.assertEqual(
        (episode.scope, episode.season_number, episode.episode_number),
        ("episode", 1, 2),
    )

def test_ranges_and_number_words_are_rejected(self):
    for query in ("Title S01-S03", "Title S01E01-E05", "Title Season One"):
        with self.subTest(query=query):
            parsed = classify_search_input(query)
            self.assertEqual(parsed.kind, "unsupported_text")
            self.assertEqual(parsed.reason, "unsupported_scope_syntax")

def test_1x02_is_not_a_supported_user_scope(self):
    parsed = classify_search_input("Title 1x02")
    self.assertEqual(parsed.kind, "unsupported_text")
```

```python
@patch("telepiplex_media_search.direct_link.get_tvdb_episode")
@patch("telepiplex_media_search.direct_link.get_tvdb_series")
def test_tvdb_s00_episode_link_is_rejected(self, series, episode):
    episode.return_value = {
        "tvdb_episode_id": "1",
        "tvdb_series_id": "2",
        "season_number": 0,
        "episode_number": 1,
    }
    series.return_value = {
        "tvdb_series_id": "2",
        "english_title": "Series",
        "episodes": [],
    }
    with self.assertRaisesRegex(DirectLinkError, "unsupported_special_scope"):
        resolve_direct_link(MetadataLink(
            provider="tvdb",
            media_type="series",
            entity_id="1",
            scope="episode",
            url="https://thetvdb.com/episodes/1",
        ))
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_input_contract tests.test_direct_link -v
```

Expected: FAIL because punctuation is not normalized, range syntax is accepted as text, and S00 links are not rejected.

- [ ] **Step 3: Add deterministic query normalization**

Create:

```python
"""Shared normalization for bounded user-entered media queries."""

from __future__ import annotations

import re
import unicodedata


_OUTER_MARKS = str.maketrans({
    "《": " ", "》": " ", "〈": " ", "〉": " ",
    "「": " ", "」": " ", "『": " ", "』": " ",
    "“": " ", "”": " ", "‘": " ", "’": " ",
    ":": " ", "：": " ", "—": " ", "–": " ",
})

_UNSUPPORTED = (
    re.compile(r"(?i)\bS\d{1,2}\s*-\s*S?\d{1,2}\b"),
    re.compile(r"(?i)\bS\d{1,2}E\d{1,3}\s*-\s*(?:S\d{1,2})?E?\d{1,3}\b"),
    re.compile(r"(?i)\b\d{1,2}\s*x\s*\d{1,3}\b"),
    re.compile(r"(?i)\bseason\s+(?:one|two|three|four|five|six|seven|eight|nine|ten)\b"),
    re.compile(r"第.+?到.+?[季集]|前\\s*[一二三四五六七八九十\\d]+\\s*集|最新几集"),
)


def normalize_query_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(_OUTER_MARKS)
    text = re.sub(r"[()（）]", " ", text)
    return " ".join(text.split())


def has_unsupported_range_syntax(value: str) -> bool:
    text = normalize_query_text(value)
    return any(pattern.search(text) for pattern in _UNSUPPORTED)
```

- [ ] **Step 4: Wire the bounded parser**

In `classify_search_input()`:

```python
raw_query = normalize_query_text(raw_query)
if has_unsupported_range_syntax(raw_query):
    return ParsedInput(
        kind="unsupported_text",
        raw_query=raw_query,
        reason="unsupported_scope_syntax",
    )
```

Remove `1x02` and English number-word parsing from the user-query path in `search_resolution.py`. Keep numeric `Season 01`, `Episode 2`, `S01`, `S01E02`, and Chinese numeric forms.

After resolving a TVDB episode link:

```python
if season_number == 0:
    raise DirectLinkError("unsupported_special_scope")
```

Add user-facing messages for `unsupported_scope_syntax` and `unsupported_special_scope` in `_PLANNING_ERROR_MESSAGES`.

- [ ] **Step 5: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_media_search/query_normalization.py src/telepiplex_media_search/input_contract.py src/telepiplex_media_search/search_resolution.py src/telepiplex_media_search/direct_link.py src/telepiplex_media_search/service.py tests/test_input_contract.py tests/test_direct_link.py
git commit -m "feat(media-search): bound user query grammar"
```

### Task 2: Centralize Canonical Prowlarr Query Construction

**Files:**
- Create: `src/telepiplex_media_search/prowlarr_query.py`
- Modify: `src/telepiplex_media_search/planner.py`
- Modify: `src/telepiplex_media_search/search_resolution.py`
- Modify: `src/telepiplex_media_search/series_scope.py`
- Modify: `src/telepiplex_media_search/direct_link.py`
- Modify: `src/telepiplex_media_search/service.py`
- Test: `tests/test_media_search_utils.py`
- Test: `tests/test_series_scope.py`
- Test: `tests/test_direct_link.py`
- Test: `tests/test_feature_service.py`

**Interfaces:**
- Produces: `build_prowlarr_query(title: str, scope: str, season_number: int | None = None, episode_number: int | None = None) -> str`.
- Every query path uses the same function.
- Whole-series and movie queries omit the year.

- [ ] **Step 1: Write failing query tests**

```python
def test_canonical_queries_are_minimal(self):
    self.assertEqual(build_prowlarr_query("Kill Bill Vol. 1", "movie"), "Kill Bill Vol 1")
    self.assertEqual(build_prowlarr_query("The Office US", "whole_series"), "The Office US")
    self.assertEqual(
        build_prowlarr_query("The Office US", "season", season_number=1),
        "The Office US S01",
    )
    self.assertEqual(
        build_prowlarr_query(
            "The Office US", "episode", season_number=1, episode_number=2
        ),
        "The Office US S01E02",
    )
```

Update existing assertions:

```python
self.assertEqual(value["retrieval"]["query"], "The Glory")
self.assertEqual(self.search_queries, [("The Glory", "series")])
self.assertEqual(self.search_queries, [("English Title", "movie")])
```

- [ ] **Step 2: Run focused tests and verify failure**

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_media_search_utils tests.test_series_scope tests.test_direct_link tests.test_feature_service.MediaSearchFeatureTest.test_confirmed_plan_searches_prowlarr_in_english_only tests.test_feature_service.MediaSearchFeatureTest.test_bare_series_requires_scope_before_prowlarr -v
```

Expected: FAIL because current movie and whole-series paths append years.

- [ ] **Step 3: Implement the canonical builder**

```python
"""Build the only supported Prowlarr query shapes."""

from __future__ import annotations

import re


def _clean_title(value: str) -> str:
    value = re.sub(r"[^\w\u3400-\u9fff]+", " ", str(value or ""))
    return " ".join(value.split())


def build_prowlarr_query(
    title: str,
    scope: str,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> str:
    title = _clean_title(title)
    if not title:
        raise ValueError("canonical_title_missing")
    if scope in {"movie", "work", "whole_series"}:
        return title
    if scope == "season" and season_number is not None:
        return f"{title} S{int(season_number):02d}"
    if (
        scope == "episode"
        and season_number is not None
        and episode_number is not None
    ):
        width = 2 if int(episode_number) < 100 else 3
        return (
            f"{title} S{int(season_number):02d}"
            f"E{int(episode_number):0{width}d}"
        )
    raise ValueError("bounded_scope_incomplete")
```

- [ ] **Step 4: Replace all query variants**

Use `build_prowlarr_query()` from:

- planner `_candidate_query`;
- `candidate_to_prowlarr_query`;
- `DirectEntity.query`;
- `apply_series_scope`;
- `MediaSearchFeature._english_prowlarr_query`.

Do not retain a fallback that reuses mixed Chinese AI queries or adds a year.

- [ ] **Step 5: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_media_search/prowlarr_query.py src/telepiplex_media_search/planner.py src/telepiplex_media_search/search_resolution.py src/telepiplex_media_search/series_scope.py src/telepiplex_media_search/direct_link.py src/telepiplex_media_search/service.py tests/test_media_search_utils.py tests/test_series_scope.py tests/test_direct_link.py tests/test_feature_service.py
git commit -m "refactor(media-search): centralize Prowlarr queries"
```

### Task 3: Add the Fact-Bound AI Candidate Scorecard

**Files:**
- Modify: `src/telepiplex_media_search/ai.py`
- Modify: `src/telepiplex_media_search/candidate_score.py`
- Modify: `src/telepiplex_media_search/planner.py`
- Modify: `tests/test_search_planner_service.py`
- Modify: `tests/test_ranked_planner.py`

**Interfaces:**
- Produces: `infer_candidate_scorecard_with_ai(context: dict) -> dict | None`.
- Produces immutable `CandidateAiScore` with fixed 40-point dimensions.
- AI may cite only candidate keys and fact IDs supplied by the program.
- Invalid keys, fact IDs, dimensions, or totals discard that AI scorecard.
- The 60-point deterministic score and 40-point AI score are reported separately.
- Hard-gate-qualified candidates remain selectable when AI is unavailable; AI can only order and recommend the complete one-to-seven-candidate set.

- [ ] **Step 1: Write failing scorecard validation and planner tests**

```python
def test_ai_scorecard_is_recomputed_from_fact_bound_dimensions(self):
    score = validate_ai_candidate_score(
        {
            "candidate_key": "tvdb:series:270117",
            "title_equivalence": 18,
            "intent_relevance": 9,
            "relation_consistency": 8,
            "fact_ids": ["fact:title:1", "fact:year:1", "fact:type:1"],
            "total": 99,
        },
        candidate_key="tvdb:series:270117",
        allowed_fact_ids={"fact:title:1", "fact:year:1", "fact:type:1"},
    )
    self.assertEqual(score.total, 35)

def test_unknown_fact_or_out_of_range_dimension_discards_ai_score(self):
    for payload in (
        {
            "candidate_key": "tvdb:series:270117",
            "title_equivalence": 21,
            "intent_relevance": 9,
            "relation_consistency": 8,
            "fact_ids": ["fact:title:1"],
        },
        {
            "candidate_key": "tvdb:series:270117",
            "title_equivalence": 18,
            "intent_relevance": 9,
            "relation_consistency": 8,
            "fact_ids": ["fact:invented"],
        },
    ):
        with self.subTest(payload=payload):
            self.assertIsNone(validate_ai_candidate_score(
                payload,
                candidate_key="tvdb:series:270117",
                allowed_fact_ids={"fact:title:1"},
            ))

async def test_ai_can_reorder_but_not_remove_candidates(self):
    planner = planner_with_two_hard_gate_candidates(
        ai_scores={"candidate:a": 12, "candidate:b": 38}
    )
    result = await planner.plan("杀死比尔")
    self.assertEqual(
        [item["candidate_key"] for item in result["candidates"]],
        ["candidate:b", "candidate:a"],
    )
    self.assertEqual(len(result["candidates"]), 2)

async def test_ai_unavailable_keeps_clear_candidate_selectable(self):
    planner = planner_with_one_hard_gate_candidate(ai_scores=None)
    result = await planner.plan("黑暗荣耀")
    self.assertEqual(len(result["candidates"]), 1)
    self.assertTrue(result["candidates"][0]["selectable"])
    self.assertEqual(result["candidates"][0]["score"]["ai_total"], 0)
```

- [ ] **Step 2: Run focused tests and verify failure**

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_search_planner_service tests.test_ranked_planner -v
```

Expected: FAIL because candidate scoring has no AI dimensions, validation, or planner integration.

- [ ] **Step 3: Implement the fixed 40-point scorecard**

In `candidate_score.py` add:

```python
@dataclass(frozen=True)
class CandidateAiScore:
    title_equivalence: int
    intent_relevance: int
    relation_consistency: int
    fact_ids: tuple[str, ...]

    @property
    def total(self) -> int:
        return (
            self.title_equivalence
            + self.intent_relevance
            + self.relation_consistency
        )
```

Validate the fixed ranges:

- `title_equivalence`: 0–20;
- `intent_relevance`: 0–10;
- `relation_consistency`: 0–10;
- every `fact_id` must exist in the candidate's program-produced fact set;
- `candidate_key` must exactly match;
- ignore any model-supplied `total` and recompute it.

Return `None` on any contract violation. Do not retain partial invalid scores.

- [ ] **Step 4: Add the fact-only AI call**

In `ai.py`, send only:

- normalized user intent;
- stable candidate keys;
- verified provider/type/title/year/alias/relation facts;
- stable fact IDs generated by the program;
- the fixed dimension definitions and ranges.

Require one JSON score object per supplied candidate. The model cannot return titles, years, IDs, provider entities, scopes, or Prowlarr queries. Parse failures return `None` and do not block planning.

- [ ] **Step 5: Integrate without giving AI gate authority**

In the planner:

- run deterministic source and identity gates first;
- build the AI context only for the remaining one-to-seven candidates;
- validate and attach `program_total`, `ai_total`, and `final_total`;
- sort by `final_total`, then deterministic tie-breakers;
- keep every hard-gate-qualified candidate selectable;
- never use `final_total` to hide a candidate or to select it on the user's behalf;
- when AI is unavailable or invalid, use `ai_total = 0` and preserve deterministic ordering.

Delete or update tests that assert `ai_total` must not exist. Preserve tests proving that a clear query succeeds when the AI client raises or returns invalid JSON.

- [ ] **Step 6: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/telepiplex_media_search/ai.py src/telepiplex_media_search/candidate_score.py src/telepiplex_media_search/planner.py tests/test_search_planner_service.py tests/test_ranked_planner.py
git commit -m "feat(media-search): score candidates from verified facts"
```

### Task 4: Add the Independent Release Correctness Gate

**Files:**
- Create: `src/telepiplex_media_search/release_gate.py`
- Create: `tests/test_release_gate.py`
- Modify: `src/telepiplex_media_search/release_score.py`

**Interfaces:**
- Produces: `gate_releases(items: list[dict], contract: dict) -> ReleaseGateResult`.
- Produces immutable `ReleaseClassification` and `ReleaseGateResult` dataclasses.
- Eligible item copies contain `release_scope`, `scope_label`, and `gate_evidence`.
- Rejections are counted by stable reason code.

- [ ] **Step 1: Write the release identity and scope tests**

```python
def test_office_wife_does_not_match_the_office(self):
    result = gate_releases(
        [{"title": "The.Office.Wife.2025.1080p", "magnet_url": "magnet:?x"}],
        series_contract(scope="season", expected_seasons=(1,), season=1),
    )
    self.assertEqual(result.eligible, ())
    self.assertEqual(result.rejection_counts["identity_mismatch"], 1)

def test_single_season_series_s01_is_whole_series(self):
    result = gate_releases(
        [{"title": "The.Glory.S01.1080p", "magnet_url": "magnet:?x"}],
        series_contract(scope="whole_series", expected_seasons=(1,)),
    )
    self.assertEqual(result.eligible[0]["scope_label"], "全剧（S01）")

def test_nine_season_range_is_complete_without_complete_keyword(self):
    result = gate_releases(
        [{"title": "The.Office.US.S01-S09.1080p", "magnet_url": "magnet:?x"}],
        series_contract(scope="whole_series", expected_seasons=tuple(range(1, 10))),
    )
    self.assertEqual(len(result.eligible), 1)

def test_partial_extra_and_special_ranges_are_rejected(self):
    target = series_contract(
        scope="whole_series", expected_seasons=tuple(range(1, 10))
    )
    items = [
        {"title": "The.Office.US.S01-S08", "magnet_url": "magnet:?1"},
        {"title": "The.Office.US.S02-S09", "magnet_url": "magnet:?2"},
        {"title": "The.Office.US.S01-S10", "magnet_url": "magnet:?3"},
        {"title": "The.Office.US.S00-S09", "magnet_url": "magnet:?4"},
        {"title": "The.Office.US.Complete.Series.Extras", "magnet_url": "magnet:?5"},
    ]
    result = gate_releases(items, target)
    self.assertEqual(result.eligible, ())
    self.assertEqual(result.rejection_counts["scope_mismatch"], 3)
    self.assertEqual(result.rejection_counts["unsupported_special_content"], 2)

def test_season_results_do_not_mix_scopes(self):
    result = gate_releases(
        [
            {"title": "Title.S01", "magnet_url": "magnet:?1"},
            {"title": "Title.S01E01", "magnet_url": "magnet:?2"},
            {"title": "Title.S01-S09", "magnet_url": "magnet:?3"},
        ],
        series_contract(scope="season", expected_seasons=(1, 2), season=1),
    )
    self.assertEqual([item["title"] for item in result.eligible], ["Title.S01"])

def test_episode_only_accepts_exact_single_episode(self):
    result = gate_releases(
        [
            {"title": "Title.S01E01", "magnet_url": "magnet:?1"},
            {"title": "Title.1x01", "magnet_url": "magnet:?2"},
            {"title": "Title.S01E01-E02", "magnet_url": "magnet:?3"},
            {"title": "Title.S01", "magnet_url": "magnet:?4"},
        ],
        series_contract(
            scope="episode", expected_seasons=(1,), season=1, episode=1
        ),
    )
    self.assertEqual(len(result.eligible), 2)
    self.assertEqual(result.rejection_counts["scope_mismatch"], 2)
```

- [ ] **Step 2: Run the gate tests and verify failure**

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_release_gate -v
```

Expected: FAIL because `release_gate` does not exist.

- [ ] **Step 3: Implement immutable gate results**

```python
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class ReleaseClassification:
    identity_match: bool
    release_scope: str
    observed_seasons: tuple[int, ...]
    observed_episodes: tuple[tuple[int, int], ...]
    scope_match: bool
    evidence: tuple[str, ...]
    rejection_reason: str = ""


@dataclass(frozen=True)
class ReleaseGateResult:
    raw_count: int
    eligible: tuple[dict, ...]
    rejection_counts: dict[str, int]
    classifications: tuple[ReleaseClassification, ...]
```

Implement:

- token-boundary alias matching;
- semantic-tail rejection before technical markers;
- year mismatch rejection;
- `SxxExx`, `1x02`, multi-episode, single-season, season-range, and whole-series lexical parsing;
- Special/S00/OVA/OAD/Extras/Bonus negative detection;
- expected ordinary seasons from confirmed `items`;
- exact target-scope predicates;
- URL eligibility;
- infohash/download URL deduplication before classification.

Do not use file count or size to infer scope.

- [ ] **Step 4: Retire title-prefix filtering**

Remove `filter_relevant_releases()` and `_identity_prefix()` from `release_score.py` after the service has no callers. Keep quality scoring independent.

- [ ] **Step 5: Run gate tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_media_search/release_gate.py src/telepiplex_media_search/release_score.py tests/test_release_gate.py
git commit -m "feat(media-search): gate release identity and scope"
```

### Task 5: Restore Explainable Quality Scoring and Indexer Reports

**Files:**
- Modify: `src/telepiplex_media_search/release_score.py`
- Create: `src/telepiplex_media_search/release_report.py`
- Create: `tests/test_release_report.py`
- Modify: `tests/test_media_search_utils.py`

**Interfaces:**
- Produces: `score_release_details(item: dict) -> tuple[int, list[dict]]`.
- `rank_releases()` preserves `score`, legacy `features`, and structured `score_details`.
- Produces: `format_release_report(query, gate, ranked, indexer_summary) -> str`.
- Produces: `release_keyboard(plan_id, count) -> list[list[dict]]`.

- [ ] **Step 1: Write failing score-detail and report tests**

```python
def test_ranked_release_keeps_weighted_score_details(self):
    ranked = rank_releases([{
        "title": "Title.2160p.WEB-DL",
        "magnet_url": "magnet:?x",
        "indexer": "M-Team",
        "seeders": 20,
        "size": 50 * 1024 ** 3,
    }], 12)
    details = ranked[0]["score_details"]
    self.assertIn({"kind": "keyword", "label": "2160p", "score": 35}, details)
    self.assertIn({"kind": "indexer", "label": "M-Team", "score": 30}, details)
    self.assertTrue(any(item["kind"] == "seeders" for item in details))
    self.assertTrue(any(item["kind"] == "size" for item in details))

def test_keyboard_has_twelve_circled_buttons_in_three_columns(self):
    keyboard = release_keyboard("plan", 12)
    self.assertEqual([len(row) for row in keyboard[:-1]], [3, 3, 3, 3])
    self.assertEqual(keyboard[0][0]["text"], "①")
    self.assertEqual(keyboard[3][2]["text"], "⑫")
    self.assertEqual(keyboard[-1][0]["text"], "退出")

def test_report_contains_gate_indexer_and_score_sections(self):
    text = format_release_report(
        "The Office US S01",
        gate_result(),
        ranked_results(),
        {
            "enabled_indexers": ["A", "B"],
            "result_sources": {"A": 10, "B": 2},
            "down_indexers": [{"source": "C", "message": "timeout"}],
            "error": "",
        },
    )
    self.assertIn("Prowlarr Query：The Office US S01", text)
    self.assertIn("正确性门禁", text)
    self.assertIn("最终得分", text)
    self.assertIn("Indexer", text)
    self.assertLessEqual(len(text), 4096)
```

- [ ] **Step 2: Run focused tests and verify failure**

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_media_search_utils tests.test_release_report -v
```

Expected: FAIL because structured score details and formatter do not exist.

- [ ] **Step 3: Add structured score contributions**

Preserve `score_release(item) -> tuple[int, list[str]]` for compatibility. Add:

```python
def score_release_details(item: dict) -> tuple[int, list[dict]]:
    details = []
    for keyword, value in _keyword_score_entries(_get_scoring_config()):
        if _contains_keyword(item.get("title") or "", keyword):
            details.append({"kind": "keyword", "label": keyword, "score": value})
    indexer_value, indexer = _indexer_score(
        item.get("indexer"), _get_scoring_config()
    )
    if indexer:
        details.append({
            "kind": "indexer", "label": indexer, "score": indexer_value
        })
    details.append({
        "kind": "seeders",
        "label": str(_safe_int(item.get("seeders"))),
        "score": _score_seeders(_safe_int(item.get("seeders"))),
    })
    details.append({
        "kind": "size",
        "label": str(_safe_int(item.get("size"))),
        "score": _score_size(_safe_int(item.get("size"))),
    })
    return sum(item["score"] for item in details), details
```

`rank_releases()` calls this function, writes `score_details`, and derives legacy `features` from non-zero keyword/indexer details.

- [ ] **Step 4: Implement compact Telegram formatting**

Use circled labels `①` through `⑫`, three buttons per row, and one exit row. Format every result with:

- truncated title;
- scope label;
- final score;
- non-zero keyword/indexer contributions;
- Indexer, seeders, and human-readable size.

Render raw count, rejection counts, enabled Indexers, per-Indexer raw counts, and health failures. Enforce `len(text) <= 4096` by shortening titles and contribution lists, never by dropping gate counts.

- [ ] **Step 5: Run focused tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_media_search/release_score.py src/telepiplex_media_search/release_report.py tests/test_media_search_utils.py tests/test_release_report.py
git commit -m "feat(media-search): explain release quality scores"
```

### Task 6: Integrate Gate, Reports, and Strict No-Fallback Selection

**Files:**
- Modify: `src/telepiplex_media_search/service.py`
- Modify: `src/telepiplex_media_search/adapters/prowlarr.py`
- Modify: `config.default.yaml`
- Modify: `config.schema.json`
- Modify: `tests/test_feature_service.py`
- Modify: `tests/test_config_schema_contract.py`

**Interfaces:**
- `MediaSearchFeature` accepts injectable `indexer_summary`.
- `_confirm_and_search()` performs raw search, health summary, gate, rank, report, then storage.
- `result_limit` defaults to and is capped at 12.
- Gate-zero responses show a report and have no release buttons.

- [ ] **Step 1: Write failing service integration tests**

```python
async def test_wrong_scope_never_enters_release_rank(self):
    self.feature.release_search = lambda *_: [
        release("Title.S01E01"),
        release("Title.S01"),
        release("Title.S01-S09"),
    ]
    ranked_inputs = []
    self.feature.release_rank = (
        lambda items, limit: ranked_inputs.extend(items) or list(items)
    )
    await self._run_confirmed_season_search()
    self.assertEqual([item["title"] for item in ranked_inputs], ["Title.S01"])

async def test_no_exact_scope_reports_counts_without_fallback_buttons(self):
    self.feature.release_search = lambda *_: [release("Title.S01E01")]
    result = await self.feature._confirm_and_search(
        self.plan_id, self.feature.plans[self.plan_id]
    )
    action = result["actions"][0]
    self.assertIn("未自动展示其他范围", action["text"])
    self.assertNotIn("keyboard", action.get("data") or {})

async def test_twelve_results_render_four_rows_of_three(self):
    self.feature.release_search = lambda *_: [
        release(f"Title.S01.1080p.Group{index}") for index in range(20)
    ]
    result = await self.feature._confirm_and_search(
        self.plan_id, self.feature.plans[self.plan_id]
    )
    keyboard = result["actions"][0]["data"]["keyboard"]
    self.assertEqual([len(row) for row in keyboard[:-1]], [3, 3, 3, 3])
```

- [ ] **Step 2: Run service tests and verify failure**

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_feature_service tests.test_config_schema_contract -v
```

Expected: FAIL because service still uses prefix filtering, defaults to 8, and renders one long-title button per row.

- [ ] **Step 3: Integrate the independent stages**

Change `_confirm_and_search()` to:

```python
raw_items = await asyncio.to_thread(self.release_search, query, media_type)
summary = await asyncio.to_thread(self.indexer_summary, raw_items)
gate = gate_releases(raw_items, contract)
limit = min(12, max(1, configured_result_limit))
results = self.release_rank(list(gate.eligible), limit)
text = format_release_report(query, gate, results, summary)
```

When `results` is empty, release plan state and return the report without a release keyboard. When results exist, store `confirmed_contract`, `results`, `gate_report`, and `indexer_summary`.

Use `release_keyboard(plan_id, len(results))`.

- [ ] **Step 4: Set the public result cap**

Set:

```yaml
result_limit: 12
```

Set JSON Schema maximum to 12. The runtime still clamps existing user configs larger than 12.

- [ ] **Step 5: Run service tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_media_search/service.py src/telepiplex_media_search/adapters/prowlarr.py config.default.yaml config.schema.json tests/test_feature_service.py tests/test_config_schema_contract.py
git commit -m "feat(media-search): enforce exact release cohorts"
```

### Task 7: Replace Renaming File-Tree Queries with Structured Probes

**Worktree:** `/Users/young/Documents/telepiplex/.worktrees/renaming`

**Files:**
- Create: `src/telepiplex_renaming/content_probe.py`
- Modify: `src/telepiplex_renaming/service.py`
- Modify: `tests/test_feature_processor.py`
- Modify: `/Users/young/Documents/telepiplex/.worktrees/media-search/src/telepiplex_media_search/service.py`
- Modify: `/Users/young/Documents/telepiplex/.worktrees/media-search/tests/test_feature_service.py`

**Interfaces:**
- Produces: `build_metadata_probe(payload: dict) -> dict`.
- Capability payload remains backward compatible with `payload["query"]` and adds `payload["probe"]`.
- media-search noninteractive resolution still requires exactly one hard-gate candidate.

- [ ] **Step 1: Write failing probe tests in renaming**

```python
def test_probe_uses_root_identity_and_separates_content_shape(self):
    probe = build_metadata_probe({
        "download_root": "/Downloads/The.Office.US",
        "resource_name": "The.Office.US",
        "release": {"title": "The.Office.US.S01-S09.1080p"},
        "file_tree": [
            {"relative_path": "S01/The.Office.S01E01.mkv", "is_dir": False},
            {"relative_path": "S09/The.Office.S09E23.mkv", "is_dir": False},
        ],
    })
    self.assertEqual(probe["identity_query"], "The Office US")
    self.assertEqual(probe["content_shape"], "multi_season_pack")
    self.assertEqual(probe["observed_seasons"], [1, 9])
    self.assertNotIn("S09E23", probe["identity_query"])

async def test_direct_download_sends_structured_probe_not_file_tree_sentence(self):
    await self._run_direct_download()
    self.assertEqual(core.metadata_payload["query"], "Movie 2024")
    self.assertEqual(core.metadata_payload["probe"]["content_shape"], "movie")
    self.assertNotIn("|", core.metadata_payload["query"])
```

- [ ] **Step 2: Run focused renaming tests and verify failure**

```bash
cd /Users/young/Documents/telepiplex/.worktrees/renaming
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_feature_processor.RenamingFeatureTest.test_direct_magnet_requeries_media_search_with_release_and_file_tree -v
```

Expected: FAIL because `_metadata_query()` concatenates release, resource, and every file.

- [ ] **Step 3: Implement the structured probe**

Create:

```python
"""Build bounded metadata-resolution hints without concatenating file trees."""

from __future__ import annotations

from pathlib import PurePosixPath
import re
import unicodedata


_VIDEO = re.compile(r"(?i)\.(?:mkv|mp4|avi|mov|m4v|ts)$")
_EPISODE = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
_SEASON = re.compile(r"(?i)\bS(\d{1,2})\b")
_TECHNICAL = re.compile(
    r"(?i)\b(?:2160p|1080p|720p|WEB[- .]?DL|BluRay|REMUX|x26[45]|HEVC|AVC)\b"
)


def _identity_query(payload: dict) -> str:
    value = (
        payload.get("resource_name")
        or PurePosixPath(str(
            payload.get("download_root") or payload.get("final_path") or ""
        )).name
    )
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = re.sub(r"(?i)\.(?:mkv|mp4|avi|mov|m4v|ts)$", "", value)
    value = _TECHNICAL.sub(" ", value)
    value = re.sub(r"[._-]+", " ", value)
    return " ".join(value.split())


def build_metadata_probe(payload: dict) -> dict:
    seasons = set()
    episodes = set()
    videos = []
    for node in payload.get("file_tree") or []:
        if not isinstance(node, dict) or node.get("is_dir"):
            continue
        path = str(node.get("relative_path") or node.get("name") or "")
        if not _VIDEO.search(path):
            continue
        videos.append(path)
        for season, episode in _EPISODE.findall(path):
            seasons.add(int(season))
            episodes.add((int(season), int(episode)))
        if not _EPISODE.search(path):
            seasons.update(int(value) for value in _SEASON.findall(path))
    if len(seasons) > 1:
        shape = "multi_season_pack"
    elif len(episodes) > 1:
        shape = "season_pack"
    elif len(episodes) == 1:
        shape = "single_episode"
    elif len(videos) == 1:
        shape = "movie"
    else:
        shape = "unknown"
    return {
        "identity_query": _identity_query(payload),
        "year_hint": "",
        "content_shape": shape,
        "observed_seasons": sorted(seasons),
        "observed_episodes": [
            {"season_number": season, "episode_number": episode}
            for season, episode in sorted(episodes)
        ],
    }
```

- [ ] **Step 4: Use the structured capability payload**

Replace `_metadata_query()` with `build_metadata_probe()`:

```python
probe = build_metadata_probe(payload)
resolved = await self.core.call_capability(
    "media.search",
    "resolve_metadata",
    {"query": probe["identity_query"], "probe": probe},
    deadline=float(self.config.get("metadata_timeout") or 120),
    idempotency_key=f"{job_id}:metadata",
)
```

Keep `/s` behavior unchanged: when confirmed `media_metadata` is already present, do not call `media.search`.

In media-search `metadata_capability()`, accept and log the optional structured `probe` without appending it to the query. Continue only when exactly one candidate is selectable; otherwise raise `metadata_unresolved`.

- [ ] **Step 5: Run focused tests in both worktrees**

```bash
cd /Users/young/Documents/telepiplex/.worktrees/renaming
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_feature_processor -v

cd /Users/young/Documents/telepiplex/.worktrees/media-search
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_feature_service.MediaSearchFeatureTest.test_metadata_capability_resolves_once_without_registry -v
```

Expected: PASS.

- [ ] **Step 6: Commit each branch independently**

Renaming:

```bash
git add src/telepiplex_renaming/content_probe.py src/telepiplex_renaming/service.py tests/test_feature_processor.py
git commit -m "fix(renaming): structure metadata probes"
```

Media-search:

```bash
git add src/telepiplex_media_search/service.py tests/test_feature_service.py
git commit -m "feat(media-search): accept structured metadata probes"
```

### Task 8: Run Cross-Module Regression and Prepare Versions

**Files:**
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `tests/test_feature_service.py`
- Modify in renaming worktree: `manifest.yaml`
- Modify in renaming worktree: `pyproject.toml`
- Modify in renaming worktree: `README.md`
- Modify in renaming worktree: `tests/test_feature_processor.py`

**Interfaces:**
- Produces: media-search `1.4.0`.
- Produces: renaming `1.3.0`.
- Produces buildable `.tpx` archives.

- [ ] **Step 1: Run media-search focused suites**

```bash
cd /Users/young/Documents/telepiplex/.worktrees/media-search
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_input_contract tests.test_direct_link tests.test_series_scope tests.test_release_gate tests.test_release_report tests.test_feature_service -v
```

Expected: PASS.

- [ ] **Step 2: Run media-search full verification**

```bash
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s tests -t . -v
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest -q
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m compileall -q src tests
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pip check
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 3: Run renaming full verification**

```bash
cd /Users/young/Documents/telepiplex/.worktrees/renaming
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s tests -t . -v
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest -q
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m compileall -q src tests
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pip check
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 4: Bump version contracts**

Media-search:

- `manifest.yaml`: `1.4.0`;
- `pyproject.toml`: `1.4.0`;
- `README.md`: `dist/media-search-1.4.0.tpx`;
- source contract tests: `1.4.0`.

Renaming:

- `manifest.yaml`: `1.3.0`;
- `pyproject.toml`: `1.3.0`;
- `README.md`: `dist/renaming-1.3.0.tpx`;
- source contract tests: `1.3.0`.

- [ ] **Step 5: Build and inspect packages**

```bash
cd /Users/young/Documents/telepiplex/.worktrees/media-search
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /Users/young/Documents/telepiplex/.worktrees/telepiplex-core/tools/build_feature.py . dist/media-search-1.4.0.tpx
unzip -t dist/media-search-1.4.0.tpx

cd /Users/young/Documents/telepiplex/.worktrees/renaming
/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 /Users/young/Documents/telepiplex/.worktrees/telepiplex-core/tools/build_feature.py . dist/renaming-1.3.0.tpx
unzip -t dist/renaming-1.3.0.tpx
```

Expected: both builders succeed and both archive checks report no errors.

- [ ] **Step 6: Commit versions independently**

Media-search:

```bash
git add manifest.yaml pyproject.toml README.md tests/test_feature_service.py
git commit -m "chore(media-search): prepare 1.4.0"
```

Renaming:

```bash
git add manifest.yaml pyproject.toml README.md tests/test_feature_processor.py
git commit -m "chore(renaming): prepare 1.3.0"
```

### Task 9: Push and Publish the Module Releases

**Files:**
- Verify only after Tasks 1–8.

**Interfaces:**
- Produces: synchronized `feature/media-search` and `feature/renaming` branches.
- Produces immutable `media-search-v1.4.0` and `renaming-v1.3.0` tags.
- Produces successful GitHub Releases, package artifacts, and catalog updates.

- [ ] **Step 1: Verify exact branch divergence**

```bash
git -C /Users/young/Documents/telepiplex/.worktrees/media-search fetch origin --prune
git -C /Users/young/Documents/telepiplex/.worktrees/media-search rev-list --left-right --count feature/media-search...origin/feature/media-search

git -C /Users/young/Documents/telepiplex/.worktrees/renaming fetch origin --prune
git -C /Users/young/Documents/telepiplex/.worktrees/renaming rev-list --left-right --count feature/renaming...origin/feature/renaming
```

Expected: each branch is ahead locally and not behind. If a branch is behind, stop publication and reconcile non-destructively before pushing.

- [ ] **Step 2: Push both module branches**

```bash
git -C /Users/young/Documents/telepiplex/.worktrees/media-search push origin feature/media-search
git -C /Users/young/Documents/telepiplex/.worktrees/renaming push origin feature/renaming
```

Expected: both pushes succeed.

- [ ] **Step 3: Create annotated release tags on the current Core release-infrastructure commit**

```bash
git -C /Users/young/Documents/telepiplex/.worktrees/telepiplex-core tag -a media-search-v1.4.0 -m "Release media-search 1.4.0"
git -C /Users/young/Documents/telepiplex/.worktrees/telepiplex-core tag -a renaming-v1.3.0 -m "Release renaming 1.3.0"
git -C /Users/young/Documents/telepiplex/.worktrees/telepiplex-core push origin media-search-v1.4.0 renaming-v1.3.0
```

Expected: both tags are accepted and trigger the existing module release automation.

- [ ] **Step 4: Monitor release automation**

Use `gh run list`, `gh run watch`, and `gh release view` against `countott/telepiplex` until:

- media-search 1.4.0 release succeeds;
- renaming 1.3.0 release succeeds;
- both `.tpx` artifacts are attached;
- package/catalog update jobs are green.

- [ ] **Step 5: Final remote audit**

```bash
git -C /Users/young/Documents/telepiplex/.worktrees/media-search fetch origin --prune
git -C /Users/young/Documents/telepiplex/.worktrees/media-search rev-list --left-right --count feature/media-search...origin/feature/media-search

git -C /Users/young/Documents/telepiplex/.worktrees/renaming fetch origin --prune
git -C /Users/young/Documents/telepiplex/.worktrees/renaming rev-list --left-right --count feature/renaming...origin/feature/renaming
```

Expected: `0 0` for both branches.
