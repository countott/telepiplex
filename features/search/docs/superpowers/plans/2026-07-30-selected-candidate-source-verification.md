# Search Selected-Candidate Source Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan inline. This Mac
> workspace forbids Git, so Git/worktree/commit steps are replaced by local
> red-green and verification checkpoints.

**Goal:** Make search display machine/AI discovery candidates before source
supplementation, then exact-read and supplement only the candidate selected by
the user.

**Architecture:** Add a conflict-tolerant discovery graph whose conflicting
stable-source occurrences receive request-scoped IDs, while retaining the
strict converged graph for selected-candidate verification. Remove eager
all-candidate supplementation from planning, run the existing bounded
supplement query editor only after selection, and quarantine conflicting
non-anchor enrichment sources so they cannot poison the selected identity.

**Tech Stack:** Python 3.12, asyncio, dataclasses, pytest, requests,
telepiplex Feature SDK.

## Global Constraints

- Product-facing text uses lowercase `telepiplex`.
- Provider results remain the only source of titles, years, media types,
  external IDs, URLs, posters and TVDB inventory.
- AI may group and bind existing facts but cannot create or overwrite facts.
- User selection is request-scoped and does not create persistent learning.
- Prowlarr runs only after strict confirmed `media_metadata v1` exists.
- Do not modify download, rename, sync or caption Feature behavior.
- Do not run Git, create Git metadata, push, tag or publish.
- Tests use the bundled Python runtime with bytecode and pytest cache disabled.

---

### Task 1: Preserve conflicting Provider occurrences during discovery

**Files:**
- Modify: `src/telepiplex_search/entity_graph.py`
- Test: `tests/test_entity_graph.py`

**Interfaces:**
- Produces `EvidenceFact.stable_fact_id: str`.
- Produces `build_discovery_graph(sources: list[dict]) -> SearchGraph`.
- Keeps `build_search_graph(...)` strict for exact verification.
- Conflict occurrence IDs use
  `<stable_fact_id>@occurrence:<deterministic_digest>`.

- [ ] **Step 1: Write the failing discovery-graph regression**

```python
def test_discovery_graph_preserves_conflicting_same_qid_occurrences():
    graph = build_discovery_graph([{
        "source": "wikipedia",
        "status": "ok",
        "facts": [
            {"wikibase_item": "Q1", "title": "作品", "year": "2013",
             "media_type": "series", "url": "https://zh.wikipedia.org/wiki/A"},
            {"wikibase_item": "Q1", "title": "Work", "year": "2014",
             "media_type": "movie", "url": "https://en.wikipedia.org/wiki/A"},
        ],
    }])
    facts = [fact for entity in graph.candidates for fact in entity.facts]
    assert len(facts) == 2
    assert {fact.stable_fact_id for fact in facts} == {"wikipedia:Q1"}
    assert len({fact.fact_id for fact in facts}) == 2
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_entity_graph.py::EntityGraphTest::test_discovery_graph_preserves_conflicting_same_qid_occurrences
```

Expected: import or attribute failure because `build_discovery_graph` and
`stable_fact_id` do not exist.

- [ ] **Step 3: Implement deterministic discovery occurrences**

Add `stable_fact_id` with an empty default to `EvidenceFact`, populate it from
the Provider fact ID, and add:

```python
def build_discovery_graph(sources: list[dict]) -> SearchGraph:
    facts, diagnostics = _discovery_facts(sources)
    return _cluster_facts(facts, diagnostics)
```

`_discovery_facts` must reuse strict merging when a stable group is
non-conflicting. When `_merge_fact_group` raises `EvidenceFactConflict`, it
must retain each distinct occurrence with a deterministic request-scoped ID
and the shared `stable_fact_id`.

- [ ] **Step 4: Run graph tests to GREEN**

Run the complete `tests/test_entity_graph.py`.

### Task 2: Stop pre-selection all-candidate supplementation

**Files:**
- Modify: `src/telepiplex_search/planner.py`
- Test: `tests/test_unified_search_pipeline.py`

**Interfaces:**
- Discovery and AI recovery use `build_discovery_graph`.
- `_build_anchored_search_plan` returns frozen discovery candidates without
  calling `supplement_query_editor` or a second Provider pass.
- Each candidate retains `fact_snapshot`, `source_links`,
  `unresolved_sources`, and preview metadata for UI rendering.

- [ ] **Step 1: Add failing planner regressions**

Add tests proving:

```python
async def test_discovery_returns_candidates_before_any_source_supplement():
    # Wikipedia is initially not_found; TVDB and Douban identify the work.
    # Assert supplement editor and second Wikipedia call are both zero.
    # Assert the candidate is selectable and records Wikipedia as unresolved.

async def test_discovery_conflict_reaches_candidate_editor():
    # Same Wikipedia QID appears as series/2013 and movie/2014.
    # Assert candidate editor receives both unique occurrence IDs.
    # Assert planning returns candidates instead of source_fact_conflict.
```

- [ ] **Step 2: Run both tests and confirm RED**

Expected: current planner calls the supplement editor/provider and current
strict graph raises `source_fact_conflict`.

- [ ] **Step 3: Implement candidate-first planning**

Use a logged discovery graph for the initial and zero-result recovery passes.
Delete the eager `missing_by_candidate` supplement loop from
`_build_anchored_search_plan`. Preserve the existing AI candidate editor,
binding repair, preview metadata, frozen links and candidate UI payload.

- [ ] **Step 4: Run unified pipeline tests to GREEN**

Run `tests/test_unified_search_pipeline.py` and update obsolete tests that
assert eager supplement behavior so they assert post-selection behavior in
Task 3 instead.

### Task 3: Supplement only the selected candidate

**Files:**
- Modify: `src/telepiplex_search/entity_graph.py`
- Modify: `src/telepiplex_search/planner.py`
- Test: `tests/test_unified_search_pipeline.py`

**Interfaces:**
- Produces:

```python
async def supplement_selected_candidate(
    candidate: dict,
    raw_query: str,
    providers: dict[str, Callable],
    *,
    candidate_editor,
    supplement_query_editor,
) -> dict:
    ...
```

- Consumes only the selected candidate's frozen `fact_snapshot` and links.
- Returns the same candidate ID and anchor, with additional validated frozen
  links/facts when supplement succeeds.

- [ ] **Step 1: Add selected-only supplement tests**

Add failing tests asserting:

```python
async def test_selected_supplement_queries_only_selected_candidate():
    # Pass frozen c2 while c1/c3 exist only in unrelated fixtures.
    # Assert AI context contains only c2.
    # Assert only c2 facts and links are returned.

async def test_selected_supplement_no_match_keeps_candidate_unchanged():
    # Provider returns an unrelated stable work.
    # Assert selected anchor and existing links remain unchanged.
```

- [ ] **Step 2: Run and confirm RED**

Expected: `supplement_selected_candidate` is missing.

- [ ] **Step 3: Implement selected candidate reconstruction and supplement**

Expand `fact_snapshot` to preserve all `EvidenceFact` fields needed to rebuild
an `AnchoredCandidate`. Reconstruct the selected candidate, compute missing
Providers, call the bounded supplement hint editor once with one candidate,
query only those Providers, and call the candidate editor with the selected
anchor locked. Reject any result that changes candidate ID or anchor.

- [ ] **Step 4: Run selected supplement tests to GREEN**

Run the new tests plus all unified pipeline tests.

### Task 4: Integrate selected verification and quarantine enrichment conflicts

**Files:**
- Modify: `src/telepiplex_search/candidate_hydration.py`
- Modify: `src/telepiplex_search/service.py`
- Test: `tests/test_candidate_hydration.py`
- Test: `tests/test_feature_service.py`

**Interfaces:**
- `hydrate_frozen_candidate` matches exact facts to frozen links using
  Provider stable IDs, not discovery occurrence IDs.
- A strict conflict from a non-anchor enrichment Provider removes that
  Provider from the selected verification pass and records it unresolved.
- `_select_candidate` calls selected supplementation before exact hydration.
- One candidate and direct-link searches auto-select through the same path.

- [ ] **Step 1: Add hydration and service regressions**

Add failing tests for:

```python
def test_hydration_maps_occurrence_id_to_exact_stable_fact():
    # Frozen fact_id is wikipedia:Q1@occurrence:..., exact read is wikipedia:Q1.
    # Assert hydration binds the exact fact and succeeds.

def test_non_anchor_conflict_is_quarantined_when_anchor_contract_is_complete():
    # TVDB is the anchor; duplicate conflicting Wikipedia exact facts exist.
    # Assert TVDB/Douban form v1 and Wikipedia is unresolved.

async def test_select_candidate_supplements_only_after_user_selection():
    # Assert selection calls selected supplement, then exact hydration, then
    # starts Prowlarr with the hydrated contract.
```

- [ ] **Step 2: Run tests and confirm RED**

Expected: occurrence ID hydration has no matching binding, non-anchor conflict
raises, and service never calls selected supplementation.

- [ ] **Step 3: Implement stable-ID hydration and local quarantine**

Map each frozen link to an exact `EvidenceFact` through its Provider-specific
external ID. When strict graph construction raises for a Provider that is not
the selected anchor, remove only that Provider's exact sources, record
`<provider>:source_fact_conflict`, and retry strict construction. An anchor
Provider conflict remains a local candidate verification error.

Add a constructor-injectable selected supplement callable to `SearchFeature`;
the default calls `supplement_selected_candidate` with the Feature's normal
Provider handlers and AI editors. `_select_candidate` awaits it before
`hydrate_frozen_candidate`.

- [ ] **Step 4: Run candidate hydration and service tests to GREEN**

Run both complete modules.

### Task 5: Advance search version and current documentation

**Files:**
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `README.md`
- Modify: `tests/test_feature_service.py`
- Modify: `../../tests/test_technical_identity_migration.py`

**Interfaces:**
- Produces search Feature version `1.2.0`.
- README describes candidate-first, selected-only verification and current AI
  responsibilities.

- [ ] **Step 1: Change version contract tests to 1.2.0 and confirm RED**

Run the focused feature source contract and root technical identity test.
Expected: current files still report 1.1.2.

- [ ] **Step 2: Update authoritative version sources and README**

Set manifest, project metadata and checked-in package metadata to `1.2.0`.
Replace eager supplement and inactive source-orchestrator runtime claims with
the selected-candidate verification chain. Change the build example to
`/tmp/search-1.2.0.tpx`.

- [ ] **Step 3: Run version/document contract tests to GREEN**

Run the focused tests from Step 1.

### Task 6: Full verification and log follow-up

**Files:**
- Verify all files changed in Tasks 1–5.
- Build: `/tmp/search-1.2.0.tpx`

- [ ] **Step 1: Run focused regression stack**

Run entity graph, unified pipeline, candidate hydration, feature service,
metadata, search plan and release gate tests.

- [ ] **Step 2: Run the complete search suite**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests
```

- [ ] **Step 3: Run affected root tests**

Run `tests/test_technical_identity_migration.py` and any publisher/version
tests whose fixtures intentionally reference the current search version.

- [ ] **Step 4: Compile and build**

Compile all modified Python modules and run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/build_feature.py features/search /tmp/search-1.2.0.tpx \
  --commit 0000000000000000000000000000000000000000
```

- [ ] **Step 5: Verify workspace invariants**

Confirm `.git` and `.worktrees` are absent and `.stfolder` exists without
creating or modifying any of them.

- [ ] **Step 6: Recheck the July 29 log**

Report separately:

- Prowlarr torrent-to-magnet 429 and metadata-losing manual `/m` workaround;
- 18 indexer failures and excessive query/indexer fan-out;
- 144 release-gate recomputations;
- noninteractive metadata probe not constraining initial planning;
- Telegram polling errors as non-search noise.

- [ ] **Step 7: Deliver local handoff**

List every added/modified/deleted file, actual verification commands and
results, and remind the user to wait for Syncthing
`Up to Date / 最新`. Do not publish.
