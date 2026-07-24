# Search Clarification Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve meaningful movie/series ambiguity as an interactive choice and rerun the selected refinement through the existing evidence gates.

**Architecture:** Keep entity separation in `entity_graph.py`, normalize AI clarification in `ai.py`, return a bounded clarification plan from `planner.py`, and render/restart it in `service.py`. No raw-count threshold or lowered evidence requirement is introduced.

**Tech Stack:** Python 3.12, asyncio, unittest/pytest, Telepiplex Feature operation API.

## Global Constraints

- Mac workspace must not use Git.
- Clarification must use only media type and source-backed year dimensions.
- User selection must retain the same Search operation ID.
- Every refined query must rerun normal source validation.
- Search release version becomes `1.0.3`.

---

### Task 1: Keep movie and series entities separate

**Files:**
- Modify: `features/search/src/telepiplex_search/entity_graph.py`
- Test: `features/search/tests/test_entity_graph.py`

**Interfaces:**
- Consumes: `EvidenceFact.media_type` and stable external IDs.
- Produces: `_stable_id_match()` that rejects cross-media-type matches.

- [ ] Add a test with a TVDB movie and series sharing a numeric ID and assert two candidates.
- [ ] Run the test and verify the current graph incorrectly returns one mixed candidate.
- [ ] Require compatible media types before a stable-ID match.
- [ ] Run the focused entity-graph tests and verify they pass.

### Task 2: Preserve AI clarification as structured data

**Files:**
- Modify: `features/search/src/telepiplex_search/ai.py`
- Test: `features/search/tests/test_search_ai_pipeline.py`

**Interfaces:**
- Consumes: validated `status`, `title_hints`, `media_type_hint`, and `clarification_reason`.
- Produces: normalized `needs_clarification` result with hypotheses and `intent_hint`.

- [ ] Add a test whose AI response requests movie/series clarification.
- [ ] Run it and verify the current function returns `None`.
- [ ] Return normalized clarification data without treating it as evidence.
- [ ] Keep malformed and unsupported results fail-closed.
- [ ] Run focused AI pipeline tests.

### Task 3: Return a bounded clarification plan

**Files:**
- Modify: `features/search/src/telepiplex_search/planner.py`
- Test: `features/search/tests/test_ranked_planner.py`

**Interfaces:**
- Consumes: normalized AI result and explicit media type from the raw query.
- Produces: `{"status": "needs_clarification", "clarification": {"reason": str, "options": list}}`.

- [ ] Add a test that unresolved `needs_clarification` yields movie and series queries.
- [ ] Add a test that explicit movie input uses corrected title hints and continues source validation.
- [ ] Run both tests and verify the current planner fails.
- [ ] Add helpers that choose the corrected title hint and build at most six options.
- [ ] Return clarification only when the raw query lacks the relevant explicit constraint.
- [ ] Run focused planner tests.

### Task 4: Render and restart clarification in the same operation

**Files:**
- Modify: `features/search/src/telepiplex_search/service.py`
- Test: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Consumes: planner clarification plans.
- Produces: `clarify:<plan_id>:<index>` callbacks and a same-operation planning restart.

- [ ] Add a service test that asserts the clarification keyboard and operation stage.
- [ ] Add a callback test that chooses an option and asserts the operation ID is unchanged.
- [ ] Run both tests and verify the current service treats the plan as a legacy candidate.
- [ ] Store clarification plans separately from ranked candidates.
- [ ] Render option and exit buttons.
- [ ] Handle `clarify` callbacks by releasing the old plan and calling `_start_plan_task(..., reuse_owner=True)`.
- [ ] Run focused service tests.

### Task 5: Version and complete verification

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `tests/test_technical_identity_migration.py`

**Interfaces:**
- Produces: consistent Search `1.0.3` release identity.

- [ ] Change all five current-version references from `1.0.2` to `1.0.3`.
- [ ] Run Search tests with the bundled Python runtime.
- [ ] Run root tests and all Feature test suites.
- [ ] Verify `.git` and `.worktrees` are absent and `.stfolder` exists.

