# Operation Stage Sealing and Candidate Posters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make media pipeline messages follow durable stage boundaries, replace work candidates with one confirmed identity card, and enrich missing candidate posters before display.

**Architecture:** Host owns Telegram message cursors and a single idempotent milestone RPC with identity and stage modes. Features publish semantic milestones only; search enriches candidate presentation, download seals before its event, rename seals identity and organization phases, and sync keeps its existing owner-scoped message through terminal state.

**Tech Stack:** Python 3.12, asyncio, python-telegram-bot, SQLite, telepiplex Plugin SDK, pytest/unittest.

## Global Constraints

- Do not run Git or create `.git` or `.worktrees` in the Mac workspace.
- Keep product-facing prose lowercase `telepiplex` while preserving technical identifiers.
- Preserve the existing `media_metadata v1` authority chain and operation ownership rules.
- Release rename as `1.4.1`; preserve all `1.4.0` external subtitle planning, naming, preflight, execution and cleanup semantics.
- A failed milestone must not rotate the message cursor or start the downstream Feature.
- Candidate poster enrichment is bounded and best effort; missing posters never block candidate selection.

---

### Task 1: Host and SDK milestone modes

**Files:**
- Modify: `sdk/src/telepiplex_plugin_sdk/host_client.py`
- Modify: `app/runtime/runtime_broker.py`
- Modify: `app/runtime/interaction_coordinator.py`
- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/115bot.py`
- Test: `tests/test_runtime_broker.py`
- Test: `tests/test_interaction_coordinator.py`
- Test: `tests/test_interaction_handler.py`

**Interfaces:**
- Produces: `HostClient.publish_operation_milestone(..., mode="identity")` and `HostClient.seal_operation_stage(operation_id, milestone_id, text)`.
- Produces: `InteractionCoordinator.clear_message_id(operation_id)`.
- Consumes: existing `operation.milestone`, `operation_milestones` idempotency and operation render lock.

- [x] Add failing tests proving identity mode edits the current photo candidate, stage mode seals the current work message, successful delivery clears the cursor, duplicate delivery does nothing, and failed delivery preserves the cursor.
- [x] Run focused Host/SDK tests and confirm they fail because mode validation, semantic delivery and cursor clearing do not exist.
- [x] Implement the bounded RPC mode, coordinator cursor clear and in-place Telegram milestone renderer.
- [x] Run focused Host/SDK tests and confirm they pass.

### Task 2: search candidate enrichment and phase boundaries

**Files:**
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify if required by existing composition: `features/search/src/telepiplex_search/candidate_hydration.py`
- Modify: `features/search/src/telepiplex_search/candidate_preview.py`
- Modify: `features/search/src/telepiplex_search/identity_presentation.py`
- Test: `features/search/tests/test_feature_service.py`
- Test: `features/search/tests/test_candidate_hydration.py`
- Test: `features/search/tests/test_identity_presentation.py`

**Interfaces:**
- Consumes: existing Douban/TMDB/TVDB adapters and anchored candidate merge rules.
- Produces: one candidate grid after bounded parallel missing-poster enrichment and a media type label that degrades to movie/series when action form is uncertain.
- Produces: identity milestone before Prowlarr progress and stage milestone after provisional handoff acceptance but before the download capability call.

- [x] Add failing tests for bounded parallel poster supplementation, Provider failure fallback, conservative media type labels, identity-before-search ordering and search-seal-before-capability ordering.
- [x] Run the focused search tests and confirm behavior failures.
- [x] Implement the smallest enrichment orchestration and milestone calls using existing adapters and contracts.
- [x] Run the focused search tests and confirm they pass.

### Task 3: download completion seal

**Files:**
- Modify: `features/download/src/telepiplex_download/service.py`
- Test: `features/download/tests/test_feature_runtime.py`

**Interfaces:**
- Consumes: `HostClient.seal_operation_stage`.
- Produces: durable ordering `file tree persisted -> download stage sealed -> download.completed published`.

- [x] Add a failing test that records Host calls and proves `download.completed` cannot publish before the download completion seal succeeds.
- [x] Run the focused download test and confirm it fails on current ordering.
- [x] Seal the download operation after persisting the downloaded result and before publishing the event; propagate failure without publishing.
- [x] Run the focused download tests and confirm they pass.

### Task 4: rename identity and organization seals

**Files:**
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Test: `features/rename/tests/test_feature_processor.py`

**Interfaces:**
- Consumes: stable identity presentation milestone and `HostClient.seal_operation_stage`.
- Produces: identity card for auto and selected rename resolution when not already delivered; new organization message after identity sealing; organization seal before sync handoff.

- [x] Add failing tests for automatic identity sealing, selected-candidate identity replacement, duplicate upstream identity suppression and rename-seal-before-sync ordering.
- [x] Run focused rename service tests and confirm behavior failures.
- [x] Implement milestone result handling and organization sealing without touching subtitle planners or processor semantics.
- [x] Run focused rename service tests and confirm they pass.

### Task 5: end-to-end ordering and sync compatibility

**Files:**
- Modify: `tests/test_operation_pipeline_e2e.py`
- Modify only if a failing compatibility test requires it: `features/sync/src/telepiplex_sync/feature.py`
- Test: `features/sync/tests/test_feature_runtime.py`

**Interfaces:**
- Verifies: one message segment per Feature, identity card replacement, explicit completion seals and sync terminal reuse.

- [x] Extend Host cursor/milestone tests plus Feature timelines to cover `search -> download -> rename -> sync` boundaries.
- [x] Run them against the pre-change behavior and confirm the missing boundaries.
- [x] Apply only compatibility changes required by the tests; sync production code remains unchanged.
- [x] Run the ordering and sync tests and confirm they pass.

### Task 6: release identities, rename subtitle regression and full verification

**Files:**
- Test only; no subtitle production changes expected.

**Interfaces:**
- Verifies: Host/Core `3.4.24`, Host API `1.6`, SDK `1.2.2`, search `1.9.3`, download `1.0.9` and rename `1.4.1`; `.srt/.ass/.sup/.vtt`, bilingual preference, sparse mapping, subtitle-only plans and conflict guards remain intact.

- [x] Run `features/rename/tests/test_subtitles.py`, subtitle cases in `test_tvdb_rename.py`, and the full rename Feature suite.
- [x] Run Host, SDK, search, download and sync focused suites.
- [x] Run the repository-wide local validation required by the handoff boundary.
- [x] Verify `.git` and `.worktrees` remain absent and `.stfolder` remains present.
- [x] Report exact changed files, commands, results, rename version/capability status and the Syncthing handoff checkpoint.
