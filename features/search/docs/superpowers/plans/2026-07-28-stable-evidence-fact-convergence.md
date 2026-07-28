# Search Stable Evidence Fact Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan inline. This Mac
> workspace forbids Git, so Git and commit steps are replaced by local test
> checkpoints.

**Goal:** Make Search 1.1.2 safely converge repeated Provider facts and prove
the complete anchored search chain remains viable.

**Architecture:** Canonicalize typed Provider facts before entity clustering,
preserve complementary metadata, reject contradictory identity fields, and
keep graph-integrity errors outside AI repair. Verify behavior at graph,
planner, service, version, and full project levels.

**Tech Stack:** Python 3.12, asyncio, requests, pytest, telepiplex Feature SDK.

## Global Constraints

- Product-facing text uses lowercase `telepiplex`.
- Provider facts remain the only source of media identity and metadata.
- AI cannot change, repair or override the Provider fact graph.
- No Git command, worktree, branch, commit, push or publication is allowed.
- Tests disable bytecode output and pytest cache.

---

### Task 1: Define stable typed fact identity and convergence

**Files:**
- Modify: `src/telepiplex_search/entity_graph.py`
- Test: `tests/test_entity_graph.py`

**Interfaces:**
- Produces typed TVDB fact IDs `tvdb:<movie|series>:<id>`.
- Produces one canonical `EvidenceFact` per fact ID.
- Produces merge diagnostics on `SearchGraph.fact_merges`.
- Raises `EvidenceFactConflict` for contradictory identity fields.

- [x] Add failing tests for duplicate Wikipedia languages, duplicate TVDB
  hypotheses, complementary episode inventories, Provider-order independence,
  and same-number TVDB movie/series facts.
- [x] Run the focused entity graph tests and confirm the duplicate cases fail.
- [x] Implement stable fact IDs, deterministic fact/episode merging and
  structured identity conflicts.
- [x] Run the focused entity graph tests to green.

### Task 2: Expose graph merge diagnostics and keep AI out of graph errors

**Files:**
- Modify: `src/telepiplex_search/anchored_candidate.py`
- Modify: `src/telepiplex_search/planner.py`
- Test: `tests/test_anchored_candidate.py`
- Test: `tests/test_unified_search_pipeline.py`

**Interfaces:**
- Produces `CandidateBindingError.details`.
- Produces `search_fact_merge status=merged|conflict`.
- Produces deterministic `source_fact_conflict`.
- Prevents AI retry for `duplicate_fact_id`.

- [x] Add failing tests that require the conflicting fact ID in the binding
  error and prove graph-level duplicates never call the repair editor.
- [x] Add a failing supplement-chain regression where the same QID is returned
  by multiple language/query results and planning reaches a confirmed candidate.
- [x] Add a failing identity-conflict test that stops before candidate editing.
- [x] Run the focused tests and confirm failures describe missing behavior.
- [x] Implement graph construction/logging boundary and non-repairable binding
  classification.
- [x] Run both test modules to green.

### Task 3: Expand user-facing and handoff regressions

**Files:**
- Test: `tests/test_feature_service.py`
- Test: `tests/test_search_plan.py`
- Test: `tests/test_media_metadata_v1.py`
- Test: `tests/test_prowlarr_adapter.py`

**Interfaces:**
- Consumes the canonical candidate plan.
- Protects candidate UI, strict metadata, Prowlarr query and download handoff.

- [x] Add or strengthen table-driven regressions for movie/series ambiguity,
  multi-source metadata, episode scope, deterministic source conflict UI, and
  unchanged handoff payload.
- [x] Run each focused module and address only regressions caused by the new
  stable fact contract.

### Task 4: Advance Search immutable version identity

**Files:**
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `README.md`
- Test: `tests/test_feature_service.py`

**Interfaces:**
- Produces Search Feature version `1.1.2`.

- [x] Change current-version tests to expect 1.1.2 and confirm they fail on
  1.1.1.
- [x] Update authoritative version sources, checked-in package metadata and
  current build example.
- [x] Re-run version contract tests.

### Task 5: Full local verification

**Files:**
- Verify all files from Tasks 1-4.

- [x] Run focused entity graph, anchored candidate, unified pipeline, service,
  metadata, plan and Prowlarr tests.
- [x] Run the complete Search test suite.
- [x] Run only the root version and publisher assertions directly affected by
  Search 1.1.2; Host and the other Feature suites are outside this task.
- [x] Compile modified Python modules and validate manifest/YAML/package
  metadata through existing tests.
- [x] Verify `.git` and `.worktrees` are absent and `.stfolder` is present.
- [x] Report changed files, exact test counts and Syncthing handoff.

### Task 6: Add real-world Search usability gates

**Files:**
- Add: `tests/test_search_usability.py`
- Add: `tests/test_live_search_usability.py`
- Modify: `tests/test_search_ai_pipeline.py`
- Modify: `README.md`

- [x] Convert the named production cases `ODDTAXI`, `冰果`,
  `蜂蜜与四叶草`, `1917` and `想见你` into a repeatable offline scenario
  matrix using real Provider response shapes.
- [x] Assert non-empty selectable candidates, distinguishable title/year/type,
  strict metadata, non-empty Prowlarr queries, human UI labels and one exit
  action.
- [x] Prove DeepSeek object-valued content plus `reasoning_content` remains
  compatible with anchored candidate parsing.
- [x] Run public Wikipedia and Douban adapter smoke checks for the named cases.
- [x] Add an opt-in live TVDB and AI gate which reads credentials from an
  explicit Search config path and never prints them.
- [x] Keep missing live credentials visible as a skipped gate, not a pass.

### Task 7: Expand the complex-series usability corpus

**Files:**
- Modify: `tests/test_search_usability.py`
- Modify: `tests/test_live_search_usability.py`
- Modify: `README.md`

- [x] Add a failing corpus contract requiring at least 12 complex series
  families and all six high-risk relationship shapes.
- [x] Add 12 real named families: `进击的巨人`, `深夜食堂`, `三体`,
  `西部世界`, `雪国列车`, `汉尼拔`, `东京爱情故事`, `射雕英雄传`,
  `大奥`, `康斯坦丁`, `Fargo` and `Watchmen`.
- [x] Model multi-season series as multiple Douban season facts bound to one
  TVDB series root and verified against per-season inventory.
- [x] Expand the opt-in TVDB and AI gate to all 17 named families.
- [x] Add and execute the opt-in Wikipedia and Douban public network gate.
- [x] Run the complete Search suite and rebuild Search 1.1.2.
