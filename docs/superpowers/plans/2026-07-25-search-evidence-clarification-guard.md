# Search Evidence Clarification Guard Implementation Plan

> **For agentic workers:** Execute inline with test-driven development. Mac local
> Git operations are prohibited by `AGENTS.md`.

**Goal:** Make AI ambiguity output stricter and guarantee source-backed
movie/series conflicts produce clarification.

**Architecture:** Strengthen the AI intent contract, keep movie and series
clusters type-safe, and add a planner guard over bounded source-backed
candidates. Reuse the existing clarification plan and normal retry path.

**Tech Stack:** Python 3.12, asyncio, unittest, pytest.

## Global Constraints

- Do not execute Git in the Mac workspace.
- Do not add typo dictionaries or locale conversion rules.
- Do not modify Prowlarr behavior.
- Do not lower source qualification gates.
- Explicit media type, series scope, and stable direct identities remain
  authoritative.
- Search release identity becomes `1.0.4`.

---

### Task 1: Protect entity clustering from transitive media-type bridges

**Files:**
- Modify: `features/search/tests/test_entity_graph.py`
- Modify: `features/search/src/telepiplex_search/entity_graph.py`

**Interfaces:**
- Consumes: `CandidateEntity` facts and `EvidenceFact.media_type`.
- Produces: `_matches_candidate(candidate, fact)` that rejects a known type
  conflicting with any known type already in the candidate.

- [ ] Add a failing graph test with an untyped fact that shares one stable ID
  with a movie and another with a series.
- [ ] Run the focused test and confirm it creates one mixed cluster before the
  fix.
- [ ] Add the candidate-level media-type compatibility guard.
- [ ] Run all entity graph tests.

### Task 2: Strengthen the outbound AI ambiguity contract

**Files:**
- Modify: `features/search/tests/test_search_ai_pipeline.py`
- Modify: `features/search/src/telepiplex_search/ai.py`

**Interfaces:**
- Consumes: AI intent context.
- Produces: an outbound prompt that mandates `needs_clarification` for multiple
  plausible works and provides constrained/unconstrained examples.

- [ ] Add a failing boundary test that captures the real prompt sent for
  `康斯坦汀` and verifies the mandatory ambiguity decision and examples are
  included.
- [ ] Run the focused test and confirm the current weak prompt fails it.
- [ ] Add the decision invariants and examples without changing the JSON shape.
- [ ] Run the AI pipeline tests.

### Task 3: Add the source-evidence clarification safety net

**Files:**
- Modify: `features/search/tests/test_ranked_planner.py`
- Modify: `features/search/src/telepiplex_search/planner.py`

**Interfaces:**
- Produces: `_source_media_type_clarification_plan(...) -> dict | None`.
- Reuses: `_ai_clarification_plan(...)` for the two bounded option payload.

- [ ] Add a failing real-title test where AI returns `parsed` for
  `康斯坦汀` but corrected source evidence contains both a movie and a series.
- [ ] Add failing exact-title, explicit-movie, single-work, and same-title
  `想见你` cases.
- [ ] Add a failing test proving an empty ranked set does not call the AI
  scorecard.
- [ ] Run the focused tests and confirm failures describe the missing guard.
- [ ] Implement the source-backed media-type guard at deterministic,
  orchestrated, and typo-recovery candidate construction boundaries.
- [ ] Skip candidate scorecard invocation when no score candidates exist.
- [ ] Run the planner tests and repeat the real-title matrix.

### Task 4: Update release identity and run complete verification

**Files:**
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `tests/test_technical_identity_migration.py`

**Interfaces:**
- Produces: consistent Search `1.0.4` package identity.

- [ ] Update all five current-version references from `1.0.3` to `1.0.4`.
- [ ] Run the focused entity, AI, and planner tests.
- [ ] Repeat the real-title matrix multiple times.
- [ ] Run the complete Search suite and root tests with the bundled Python.
- [ ] Verify package build and workspace markers.
- [ ] Report exact files and actual results, then wait for Syncthing before
  Unraid publication.
