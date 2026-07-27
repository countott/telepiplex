# Search 1.1.0 Resilience Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep useful one- or two-provider candidates visible while making Search failures, exact-link hydration, Telegram presentation, metadata identity, and Provider behavior reliable.

**Architecture:** Discovery materializes independently valid frozen candidates and labels them `v1` only when Wikipedia, Douban, and TVDB are all bound; otherwise it emits a displayable `v0`. Strict `media_metadata v1` moves to exact-link hydration after selection, while stable error envelopes and compact UI rendering preserve recovery paths.

**Tech Stack:** Python 3.12, asyncio, unittest/pytest, Telegram Host actions, Wikipedia/Douban/TVDB HTTP adapters.

## Global Constraints

- Do not execute Git or create Git/worktree metadata on the Mac.
- Preserve the unified text/direct-link candidate pipeline.
- AI may reference only verified fact IDs.
- Candidate selection freezes links; selection must not issue Provider title searches.
- Keep Provider HTTP fault timeouts; do not restore business-layer planning deadlines.
- Product name remains lowercase `telepiplex`.

---

### Task 1: Candidate completeness and isolation

**Files:**
- Modify: `features/search/src/telepiplex_search/anchored_candidate.py`
- Modify: `features/search/src/telepiplex_search/planner.py`
- Test: `features/search/tests/test_anchored_candidate.py`
- Test: `features/search/tests/test_unified_search_pipeline.py`

**Interfaces:**
- Produces: candidate fields `candidate_version`, `metadata_ready`, and `metadata_error`.
- Produces: candidate-level binding/materialization that drops only invalid shortlist entries.

- [ ] Add failing tests proving a one- or two-provider candidate is v0, a three-provider candidate is v1, and one incomplete candidate does not poison another.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement per-candidate materialization and discovery metadata preview without strict-v1 gating.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Error taxonomy and foreground recovery

**Files:**
- Modify: `features/search/src/telepiplex_search/planner.py`
- Modify: `features/search/src/telepiplex_search/direct_link.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Test: `features/search/tests/test_unified_search_pipeline.py`
- Test: `features/search/tests/test_feature_service.py`

**Interfaces:**
- Produces: `no_match`, `source_failure`, `source_rate_limited`, `ai_candidate_failure`, `candidate_binding_failed`, `fixed_link_read_failed`, `metadata_conflict`, `metadata_incomplete`, and `prowlarr_failure`.

- [ ] Add failing tests for zero-fact AI failure, all-source failure, TVDB direct-link failure normalization, and UI recovery controls.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement stable error normalization and foreground messages/buttons.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Provider and AI resilience

**Files:**
- Modify: `features/search/src/telepiplex_search/adapters/wikipedia.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/planner.py`
- Test: `features/search/tests/test_wikipedia_adapter.py`
- Test: `features/search/tests/test_tvdb_adapter.py`
- Test: `features/search/tests/test_unified_search_pipeline.py`

**Interfaces:**
- Produces: Wikipedia `rate_limited` status and corrected numeric-title/media-type classification.
- Produces: TVDB partial-result evidence with per-series unresolved inventory.
- Produces: bounded AI discovery context.

- [ ] Add failing tests for Wikipedia classification/429, TVDB partial inventory failure, and AI-context limits.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement the minimal Provider isolation and AI-context compaction.
- [ ] Run the focused tests and confirm they pass.

### Task 4: Input, Telegram, metadata IDs, and queries

**Files:**
- Modify: `features/search/src/telepiplex_search/input_contract.py`
- Modify: `features/search/src/telepiplex_search/search_resolution.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/media_metadata_v1.py`
- Modify: `features/search/src/telepiplex_search/prowlarr_query.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/handlers/interaction_handler.py`
- Test: `features/search/tests/test_input_contract.py`
- Test: `features/search/tests/test_feature_service.py`
- Test: `features/search/tests/test_media_metadata_v1.py`
- Test: `features/search/tests/test_search_utils.py`
- Test: `tests/test_plugin_handler.py`
- Test: `tests/test_interaction_handler.py`

**Interfaces:**
- Produces: quoted-numeric-title input normalization.
- Produces: valid HTML caption no longer than 1024 characters.
- Produces: `identity.external_id_records`.
- Produces: disambiguation-free Prowlarr query titles.

- [ ] Add failing tests for all four observable behaviors.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement the minimal contract changes and host guard.
- [ ] Run the focused tests and confirm they pass.

### Task 5: Verification and real-query regression

**Files:**
- Modify only if a failing regression requires a scoped correction.

**Interfaces:**
- Consumes: all preceding Search and Host behavior.
- Produces: local verification evidence and a throttled real-query report.

- [ ] Run the complete Search test suite.
- [ ] Run the complete Host test suite required by `AGENTS.md`.
- [ ] Run throttled real Wikipedia and Douban queries across exact, ambiguous, season, typo, numeric, multilingual, and no-match cases.
- [ ] Verify `.git` and `.worktrees` remain absent and `.stfolder` remains present.
- [ ] List every changed file and remind the user to wait for Syncthing `Up to Date / 最新`.

