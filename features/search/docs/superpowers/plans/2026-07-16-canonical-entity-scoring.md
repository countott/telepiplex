# Canonical Entity Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace strict single-plan matching with a request-scoped entity graph, fixed versioned scoring, poster confirmation, and a selected-entity-only SQLite registry.

**Architecture:** Provider responses become immutable facts in a request-scoped graph. Deterministic code performs exact entity clustering, HardGate checks, and 60 points of scoring; AI may return relation hypotheses and a fact-referenced 40-point scorecard but never `media_metadata`. The service renders ranked candidates, persists only the selected canonical entity, then builds the existing confirmed v1 handoff.

**Tech Stack:** Python 3.12, asyncio, sqlite3, requests, unittest, Telepiplex plugin SDK.

## Global Constraints

- Fixed scoring version is `media-entity-v1`; no online learning or dynamic weights.
- Candidate limit is 5, relation-hypothesis limit is 3, and directed results per provider/candidate are limited to 3.
- Thresholds are 85 recommended, 65 minimum, and 10-point lead; every interactive result still requires user confirmation.
- Total planning budget is 90 seconds with stage caps 15/20/15/25/15.
- Non-Japanese canonical search uses official English; Japanese uses official romanized original.
- Search facts, candidate graphs, AI scorecards, and unselected posters never enter SQLite.
- `resolve_metadata` only rehydrates an exact previously selected entity; it cannot select or persist a new candidate.
- Feature work remains local to `feature/search` and is not pushed.

---

### Task 1: Build Exact Request-Scoped Entity Graphs

**Files:**
- Create: `src/telepiplex_search/entity_graph.py`
- Create: `tests/test_entity_graph.py`
- Modify: `src/telepiplex_search/adapters/douban.py`
- Modify: `src/telepiplex_search/adapters/tvdb.py`

**Interfaces:**
- Consumes: provider result dictionaries with `source`, `status`, and `facts`.
- Produces: `build_search_graph(sources: list[dict]) -> SearchGraph`; `SearchGraph.candidates: tuple[CandidateEntity, ...]`; each candidate exposes `candidate_key`, `facts`, `titles`, `years`, `media_types`, `external_ids`, `poster_url`, `complex_signals`.

- [ ] **Step 1: Write failing graph tests**

```python
def test_same_title_movie_and_series_do_not_merge(self):
    graph = build_search_graph(self.sources_for_same_title_movie_and_series())
    self.assertEqual(len(graph.candidates), 2)

def test_title_year_and_type_merge_independent_sources(self):
    graph = build_search_graph(self.sources_for_grand_budapest())
    candidate = graph.candidates[0]
    self.assertEqual(candidate.providers, frozenset({"wikipedia", "douban", "tvdb"}))

def test_search_result_mentions_do_not_merge_into_exact_title(self):
    graph = build_search_graph(self.sources_for_shamate_with_wikipedia_noise())
    matched = [item for item in graph.candidates if "杀马特我爱你" in item.titles]
    self.assertEqual(len(matched), 1)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests.test_entity_graph -v`

Expected: FAIL because `entity_graph` does not exist.

- [ ] **Step 3: Implement immutable facts and exact clustering**

```python
@dataclass(frozen=True)
class EvidenceFact:
    fact_id: str
    provider: str
    titles: tuple[str, ...]
    year: str
    media_type: str
    external_ids: Mapping[str, str]
    source_url: str = ""
    poster_url: str = ""
    original_title: str = ""
    original_language: str = ""
    official_english_title: str = ""
    romanized_original_title: str = ""

@dataclass(frozen=True)
class CandidateEntity:
    candidate_key: str
    facts: tuple[EvidenceFact, ...]
    # read-only aggregate properties

@dataclass(frozen=True)
class SearchGraph:
    candidates: tuple[CandidateEntity, ...]
```

Merge only when a stable ID matches, or normalized title, non-empty year, and non-empty media type all match. Generate deterministic fact IDs as `<provider>:<provider-id-or-index>`.

- [ ] **Step 4: Preserve structured title-language fields in Douban and TVDB facts**

Add `original_title`, `original_language`, `official_english_title`, and `romanized_original_title` when the upstream payload exposes them. Detect Japanese only from explicit language values or Japanese script; never translate a title.

- [ ] **Step 5: Run graph and adapter tests**

Run: `python3 -m unittest tests.test_entity_graph tests.test_douban_adapter tests.test_tvdb_adapter -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_search/entity_graph.py src/telepiplex_search/adapters/douban.py src/telepiplex_search/adapters/tvdb.py tests/test_entity_graph.py tests/test_douban_adapter.py tests/test_tvdb_adapter.py
git commit -m "feat(search): build request entity graphs"
```

### Task 2: Resolve Canonical Search and Naming Titles

**Files:**
- Create: `src/telepiplex_search/title_policy.py`
- Create: `tests/test_title_policy.py`

**Interfaces:**
- Consumes: `CandidateEntity`.
- Produces: `resolve_title_policy(candidate: CandidateEntity) -> CanonicalTitles`; `CanonicalTitles` fields mirror the design contract; raises `TitlePolicyError("canonical_title_unavailable")` when authoritative data is insufficient.

- [ ] **Step 1: Write failing title-policy tests**

```python
def test_non_japanese_uses_official_english(self):
    titles = resolve_title_policy(self.grand_budapest_candidate())
    self.assertEqual(titles.canonical_search_title, "The Grand Budapest Hotel")
    self.assertEqual(titles.search_title_policy, "official_english")

def test_japanese_uses_romaji_not_english_translation(self):
    titles = resolve_title_policy(self.attack_on_titan_candidate())
    self.assertEqual(titles.official_english_title, "Attack on Titan")
    self.assertEqual(titles.romanized_original_title, "Shingeki no Kyojin")
    self.assertEqual(titles.canonical_latin_title, "Shingeki no Kyojin")
    self.assertEqual(titles.search_title_policy, "romanized_original")

def test_japanese_without_verified_romaji_is_not_finalizable(self):
    with self.assertRaisesRegex(TitlePolicyError, "canonical_title_unavailable"):
        resolve_title_policy(self.japanese_without_romaji())
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests.test_title_policy -v`

Expected: FAIL because `title_policy` does not exist.

- [ ] **Step 3: Implement exact source-backed title selection**

```python
@dataclass(frozen=True)
class CanonicalTitles:
    chinese_title: str
    original_title: str
    original_language: str
    official_english_title: str
    romanized_original_title: str
    canonical_search_title: str
    canonical_latin_title: str
    search_title_policy: str
```

Select only values present in candidate facts. Japanese requires `original_language == "ja"` and a non-empty source-backed romanized title. Non-Japanese requires a source-backed official English title.

- [ ] **Step 4: Run title tests**

Run: `python3 -m unittest tests.test_title_policy -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/telepiplex_search/title_policy.py tests/test_title_policy.py
git commit -m "feat(search): enforce canonical title policy"
```

### Task 3: Persist Only Selected Canonical Entities

**Files:**
- Create: `src/telepiplex_search/entity_registry.py`
- Create: `tests/test_entity_registry.py`
- Modify: `src/telepiplex_search/runtime.py`

**Interfaces:**
- Consumes: selected canonical snapshots.
- Produces: `CanonicalEntityRegistry(path: Path)` with `upsert_selected(entity: dict, relation: dict)`, `get(entity_key: str)`, `resolve_exact(query: str)`, and `count() -> int`.

- [ ] **Step 1: Write failing SQLite lifecycle tests**

```python
def test_only_explicit_upsert_creates_a_row(self):
    registry = CanonicalEntityRegistry(self.path)
    self.assertEqual(registry.count(), 0)
    registry.upsert_selected(self.entity, self.relation)
    self.assertEqual(registry.count(), 1)

def test_upsert_is_idempotent_and_does_not_store_evidence(self):
    registry.upsert_selected(self.entity, self.relation)
    registry.upsert_selected({**self.entity, "poster_url": "https://image/new.jpg"}, self.relation)
    self.assertEqual(registry.count(), 1)
    columns = registry.raw_columns_for_test("canonical_entities")
    self.assertNotIn("evidence", columns)
    self.assertNotIn("scorecard", columns)

def test_exact_resolution_requires_canonical_title_and_year(self):
    self.assertIsNotNone(registry.resolve_exact("The Grand Budapest Hotel 2014"))
    self.assertIsNone(registry.resolve_exact("Grand Budapest"))
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests.test_entity_registry -v`

Expected: FAIL because `entity_registry` does not exist.

- [ ] **Step 3: Implement transactional schema and UPSERT**

Use the exact `canonical_entities` and `canonical_relations` fields from the design document. Enable WAL, foreign keys, and a busy timeout. Do not create candidate, evidence, query, or score tables. `resolve_exact` compares normalized canonical Latin title plus explicit year, or an exact stable-ID token.

- [ ] **Step 4: Inject the state-path registry at runtime startup**

```python
registry = CanonicalEntityRegistry(context.state_path / "media_entities.db")
feature = SearchFeature(config=config, host=context.host, registry=registry)
```

- [ ] **Step 5: Run registry tests**

Run: `python3 -m unittest tests.test_entity_registry -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_search/entity_registry.py src/telepiplex_search/runtime.py tests/test_entity_registry.py
git commit -m "feat(search): persist selected canonical entities"
```

### Task 4: Implement Fixed Program and AI Scorecards

**Files:**
- Create: `src/telepiplex_search/candidate_score.py`
- Create: `tests/test_candidate_score.py`
- Modify: `src/telepiplex_search/ai.py`
- Modify: `tests/test_search_ai_pipeline.py`

**Interfaces:**
- Consumes: `CandidateEntity`, parsed intent, verified relation facts, and AI scorecard JSON.
- Produces: `program_score(candidate, intent, relation) -> ProgramScore`; `validate_ai_scorecard(payload, valid_fact_ids) -> AIScore`; `rank_candidates(...) -> list[RankedCandidate]`; `score_candidates_with_ai(context: dict) -> dict | None`; `infer_relation_hypotheses_with_ai(context: dict) -> dict | None`.

- [ ] **Step 1: Write failing score tests**

```python
def test_program_score_is_fixed_sixty_point_model(self):
    score = program_score(self.three_source_candidate(), self.intent(), None)
    self.assertEqual(score.total, 60)
    self.assertEqual(score.version, "media-entity-v1")

def test_wrong_user_year_penalizes_without_hard_gate(self):
    score = program_score(self.glory_2022(), self.intent(year="2019"), None)
    self.assertFalse(score.excluded)
    self.assertEqual(score.release_consistency, 0)

def test_ai_unknown_fact_reference_is_rejected(self):
    with self.assertRaisesRegex(ScorecardError, "unknown_fact_id"):
        validate_ai_scorecard(self.ai_payload(fact_ids=["invented:1"]), {"tvdb:1"})

def test_thresholds_and_lead_are_fixed(self):
    ranked = apply_thresholds([self.score(90), self.score(82)])
    self.assertFalse(ranked[0].recommended)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests.test_candidate_score tests.test_search_ai_pipeline -v`

Expected: FAIL because scorecard functions do not exist.

- [ ] **Step 3: Implement HardGate and the exact 60-point table**

Represent component scores explicitly and cap them at 25/15/10/10. Candidate type conflicts with an explicitly requested movie/series are excluded; a year mismatch is not excluded.

- [ ] **Step 4: Replace AI full-contract generation with strict relation and score prompts**

```python
def infer_relation_hypotheses_with_ai(context: dict):
    return _strict_json_request(RELATION_SCOUT_PROMPT, context, max_tokens=1800)

def score_candidates_with_ai(context: dict):
    return _strict_json_request(SCORECARD_PROMPT, context, max_tokens=2200)
```

Prompts must forbid new facts and stable IDs. Normalize only scorecard arrays; do not accept `media_metadata`, `title_zh`, or `title_en` as alternate schema fields.

- [ ] **Step 5: Run score and AI tests**

Run: `python3 -m unittest tests.test_candidate_score tests.test_search_ai_pipeline -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_search/candidate_score.py src/telepiplex_search/ai.py tests/test_candidate_score.py tests/test_search_ai_pipeline.py
git commit -m "feat(search): add fixed candidate scorecards"
```

### Task 5: Build Budgeted Ranked Candidate Plans

**Files:**
- Modify: `src/telepiplex_search/planner.py`
- Modify: `src/telepiplex_search/deterministic.py`
- Modify: `src/telepiplex_search/search_plan.py`
- Create: `tests/test_ranked_planner.py`
- Modify: `tests/test_search_planner_service.py`

**Interfaces:**
- Consumes: graph/title/score modules and existing provider callables.
- Produces: `build_confirmable_search_plan(...) -> dict` containing `candidates` (max 5), each with `candidate_key`, `score`, `media_metadata`, `prowlarr_queries`, `poster_url`, and `reasons`; total runtime bounded by `PlanningBudget`.

- [ ] **Step 1: Write failing ranked-planner tests**

```python
async def test_black_glory_wrong_year_keeps_title_match_not_same_year_noise(self):
    plan = await self.build("黑暗荣耀 2019", self.sources)
    self.assertEqual(plan["candidates"][0]["media_metadata"]["identity"]["english_title"], "The Glory")
    self.assertNotIn("Terminator", [item["candidate_key"] for item in plan["candidates"]])

async def test_relation_scout_runs_before_scoring_only_for_complex_signals(self):
    await self.build("想见你 电影版", self.related_sources)
    self.assertLess(self.calls.index("relation_scout"), self.calls.index("scorecard"))

async def test_total_budget_times_out_structurally(self):
    with self.assertRaisesRegex(SearchPlanningError, "planning_timed_out"):
        await self.build_with_clock(self.slow_clock)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests.test_ranked_planner -v`

Expected: FAIL because planner returns a single final contract.

- [ ] **Step 3: Add `PlanningBudget` and concurrent stage wrappers**

```python
class PlanningBudget:
    TOTAL = 90.0
    STAGES = {
        "base_evidence": 15.0,
        "relation_scout": 20.0,
        "relation_verification": 15.0,
        "scorecard": 25.0,
        "candidate_finalize": 15.0,
    }

    def remaining_for(self, stage: str) -> float:
        return max(0.0, min(self.STAGES[stage], self.deadline - self.clock()))
```

Use `asyncio.timeout` around each stage. Keep provider calls in `asyncio.gather`/threads. Convert stage timeout to status or `planning_timed_out`; do not leak `TimeoutError`.

- [ ] **Step 4: Build contracts only from validated candidates**

Map `CanonicalTitles.canonical_latin_title` to `identity.english_title`, append all optional title-policy fields, and generate Prowlarr queries from `canonical_search_title`. Relation facts determine `relation`, `placement`, and `items` before final scores.

- [ ] **Step 5: Run planner tests**

Run: `python3 -m unittest tests.test_ranked_planner tests.test_search_planner_service tests.test_deterministic_planner tests.test_search_plan -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/telepiplex_search/planner.py src/telepiplex_search/deterministic.py src/telepiplex_search/search_plan.py tests/test_ranked_planner.py tests/test_search_planner_service.py tests/test_deterministic_planner.py tests/test_search_plan.py
git commit -m "feat(search): build budgeted ranked plans"
```

### Task 6: Add Poster Candidate Selection and Selected-Only Persistence

**Files:**
- Modify: `src/telepiplex_search/service.py`
- Modify: `tests/test_feature_service.py`

**Interfaces:**
- Consumes: ranked plan candidate list and `CanonicalEntityRegistry`.
- Produces: `browse:<plan_id>:<index>` and `select:<plan_id>:<index>` callbacks; `send_photo`/`edit_photo` actions; selected entity persistence before release search.

- [ ] **Step 1: Write failing service interaction tests**

```python
async def test_ranked_plan_renders_top_candidate_poster(self):
    await self.feature._prepare_plan("query", self.request, plan_id="p1", operation_id="o1")
    report = self.host.reports[-1]
    self.assertEqual(report["details"]["photo_url"], "https://image.example/top.jpg")

async def test_browse_does_not_persist_and_select_persists_once(self):
    await self.feature.callback({**self.request, "payload": "browse:p1:1"})
    self.assertEqual(self.registry.count(), 0)
    await self.feature.callback({**self.request, "payload": "select:p1:1"})
    self.assertEqual(self.registry.count(), 1)

async def test_cancel_discards_all_candidates(self):
    await self.feature.callback({**self.request, "payload": "cancel:p1"})
    self.assertNotIn("p1", self.feature.plans)
    self.assertEqual(self.registry.count(), 0)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests.test_feature_service -v`

Expected: FAIL because only `confirm` and text cards exist.

- [ ] **Step 3: Render deterministic candidate cards**

Add `_candidate_action(stored, index, *, edit: bool) -> dict`. Bind callback indices to immutable candidate keys, include score/year/type/relation in the caption, and choose photo actions only when an HTTPS poster exists.

- [ ] **Step 4: Persist atomically at selection**

Validate the chosen candidate index and candidate key, confirm its `media_metadata`, call `registry.upsert_selected`, replace `stored["plan"]` with the selected candidate plan, and only then start Prowlarr search. On persistence failure, keep the interaction open and do not search releases.

- [ ] **Step 5: Constrain noninteractive resolution**

Change `metadata_capability` to call `registry.resolve_exact(query)`. Return a confirmed contract only for an exact selected entity; otherwise raise `FeatureError("metadata_unresolved", ...)`. It must not call the planner.

- [ ] **Step 6: Run service tests**

Run: `python3 -m unittest tests.test_feature_service -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/telepiplex_search/service.py tests/test_feature_service.py
git commit -m "feat(search): confirm poster-backed candidates"
```

### Task 7: Verify Media Search End to End

**Files:**
- Modify: `README.md` only if the command behavior described there changed.
- Verify all implementation files.

**Interfaces:**
- Produces: locally tested `feature/search` with no unselected persistent data.

- [ ] **Step 1: Run focused behavior suites**

Run: `python3 -m unittest tests.test_entity_graph tests.test_title_policy tests.test_entity_registry tests.test_candidate_score tests.test_ranked_planner tests.test_feature_service -v`

Expected: PASS.

- [ ] **Step 2: Run the full Feature suite**

Run: `python3 -m unittest discover -s tests -t . -v`

Expected: PASS.

- [ ] **Step 3: Run syntax, dependency, and whitespace checks**

Run: `python3 -m compileall -q src tests`

Expected: exit 0.

Run: `python3 -m pip check`

Expected: no broken requirements.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 4: Verify branch-local persistence scope**

Run: `rg -n "CREATE TABLE" src/telepiplex_search`

Expected: only `canonical_entities` and `canonical_relations` are created.
