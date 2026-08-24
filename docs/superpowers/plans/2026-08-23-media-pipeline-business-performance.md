# Media Pipeline Business Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the correct `search -> download -> rename` business flow while removing non-business waits, recording durable handoffs, reducing rename storage round trips, and scheduling Search/115 work for lower user-visible latency.

**Architecture:** Confirmed identity/scope and durable operation receipts remain the authority; Telegram, posters, and localization become asynchronous projections. Search gets purpose-aware source scheduling and Prowlarr waves, Download gets endpoint pacing and adaptive polling, and Rename reuses one preflight transaction snapshot while retaining fresh mutation postconditions.

**Tech Stack:** Python 3.12, asyncio, sqlite3/WAL, requests, pytest/unittest, telepiplex Host RPC and Feature runtime.

**Spec:** `docs/superpowers/specs/2026-08-23-media-pipeline-business-performance-design.md`

## Global Constraints

- Work only in `/Users/young/Documents/telepiplex` and use `apply_patch` for source/document edits.
- Do not execute any Git command, create `.git` or `.worktrees`, connect this checkout to GitHub, publish, tag, push, or create a PR.
- The automatic business terminal remains verified rename completion. Do not publish `media.organized`, hand off to `sync`, call `library.sync`, or trigger Plex.
- Preserve the existing hard identity/year/type/scope/special/URL/duplicate release gates and stable release callback IDs.
- Telegram, poster, and optional localization failure may be visible but cannot reject a durable business handoff.
- Rename must retain target-conflict, provider-ID/fingerprint, source-absence, fresh directory listing, and cleanup postconditions.
- P1-4 concurrent foreground/background user operations is excluded.
- Use TDD for every behavior change: add one focused failing test, run it and record the expected failure, implement the minimum behavior, then rerun the focused and adjacent suites.
- Mac-local checkpoints are test evidence plus changed-file records, never commits.
- Use the bundled Python interpreter:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
```

## Baseline evidence

- Core: 465 passed, 1 skipped, 180 subtests; 3 pre-existing failures caused by stale rename `1.5.6` assertions while the manifest is already `1.5.7`.
- Search: 448 passed, 2 skipped, 65 subtests.
- Download: 72 passed, 25 subtests.
- Rename: 264 passed, 8 subtests.
- The successful attached log measured about 53.7 seconds to build candidates, 9.3 seconds of poster wait, 27.0 seconds from candidate click to scope menu, 5.75 seconds to first eligible Prowlarr release, about 15 seconds to detect download completion, and about 111.8 seconds in rename with 76 storage RPCs.

## Requirement coverage

| Approved item | Implemented by | Completion evidence |
|---|---|---|
| P0-0 rename is the automatic terminal | Task 1, Task 5 | real-manifest E2E and negative sync assertions |
| P0-1 scope single truth | Task 1 | season/episode presentation and milestone-ID tests |
| P0-2 authority versus enrichment | Task 2 | blocked poster/Douban and frozen-query tests |
| P0-3 Telegram is projection | Task 4, Task 5 | blocked renderer/milestone E2E |
| P0-4 rename logical transaction | Task 6 | identity/postcondition matrix and call budget |
| P0-5 durable receipt/failure visibility | Task 3, Task 5 | receipt transitions and poison-event failure test |
| P1-1 source scheduling/single-flight | Task 7 | source scheduler concurrency tests |
| P1-2 Prowlarr scheduling only | Task 7 | wave timing plus existing incremental-selection tests |
| P1-3 adaptive 115 behavior | Task 8 | pacer, polling, throttle, and cancellation tests |
| P1-4 excluded | Global Constraints | no multi-operation scheduling changes |

---

### Task 1: Freeze the business terminal and make scope presentation authoritative

**Files:**
- Modify: `features/search/src/telepiplex_search/identity_presentation.py`
- Modify: `features/search/tests/test_identity_presentation.py`
- Modify: `tests/test_operation_pipeline_e2e.py`
- Modify: `tools/pressure_operation_pipeline.py`
- Modify: `tests/test_pressure_operation_pipeline.py`
- Modify: `tests/test_technical_identity_migration.py`
- Modify: `tests/test_unraid_publish_script.py`

**Interfaces:**
- Consumes: confirmed `media_metadata v1` retrieval and evidence decision fields.
- Produces: `build_identity_presentation(contract) -> {text, milestone_id, ...}` whose scope comes from `retrieval.scope` and `evidence.decision`.
- Produces: an E2E contract whose terminal owner is `rename` and which has no automatic sync event/call.

- [x] **Step 1: Add failing season/episode presentation tests**

Add contracts where `placement` is empty or stale and assert:

```python
season = build_identity_presentation(scoped_contract(
    scope="season", season_number=5, placement_season=None,
))
episode = build_identity_presentation(scoped_contract(
    scope="episode", season_number=5, episode_number=3,
    placement_season=1, placement_episode=1,
))
assert "第 5 季" in season["text"]
assert "S05E03" in episode["text"]
assert season["milestone_id"] != episode["milestone_id"]
assert season["milestone_id"] != whole_series["milestone_id"]
```

- [x] **Step 2: Verify RED**

Run from `features/search`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_identity_presentation.py
```

Expected failure: season/episode fall back to `全剧` or use stale placement coordinates.

- [x] **Step 3: Implement one scope-coordinate reader**

In `identity_presentation.py`, derive `season_number` and `episode_number` from `evidence.decision`; use placement only as a compatibility fallback when the decision lacks the field and retrieval scope already names the same bounded scope. Include raw scope and numeric coordinates in the milestone digest rather than only the rendered label.

- [x] **Step 4: Replace the stale synthetic Plex E2E**

Update `test_operation_pipeline_e2e.py` to activate only search, download, and rename. Assert owner order, rename terminal state, absence of `media.organized`, and absence of a sync route/capability call.

Apply the same terminal contract to `tools/pressure_operation_pipeline.py` and
its test: the pressure harness must stop at rename `completed`, publish only
`download.completed`, and contain no sync runtime/client/event expectation.

- [x] **Step 5: Align the three already-stale rename version expectations**

Change only the assertions that still expect `rename 1.5.6` to the current pre-task baseline `1.5.7`; do not perform the final version bump in this task.

- [x] **Step 6: Run the Task 1 gate**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_identity_presentation.py tests/test_feature_service.py
```

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_operation_pipeline_e2e.py tests/test_technical_identity_migration.py tests/test_unraid_publish_script.py
```

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_pressure_operation_pipeline.py
```

Expected: all commands exit 0 and no test or pressure harness expects automatic
Plex work.

### Task 2: Split Search authority from optional presentation work

**Files:**
- Create: `features/search/src/telepiplex_search/enrichment_policy.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/tests/test_regression_pressure.py`

**Interfaces:**
- Produces: `needs_authoritative_scope_enrichment(candidate: dict) -> bool`.
- Produces: `apply_deferred_presentation(contract: dict, enrichment: dict) -> dict`, limited to empty `identity.chinese_title` and empty HTTPS poster.
- Search stores `deferred_enrichment_task` and `deferred_contract` per plan; query/gate always use the original `confirmed_contract` snapshot.

- [x] **Step 1: Reverse the unsafe supplement-before-anchor test**

Replace `test_selected_candidate_is_supplemented_before_exact_read` with a failing test whose event sequence is:

```python
assert events[:2] == [("hydrate", False), ("authoritative", True)]
```

The anchor read must fail closed before any optional source can “repair” a different identity.

- [x] **Step 2: Add failing critical-path tests**

Cover these independent behaviors:

```python
assert prowlarr_task_created.is_set()
assert not slow_douban_finished.is_set()
assert stored["confirmed_contract"]["retrieval"] == frozen_retrieval
assert stored["confirmed_contract"]["identity"]["external_ids"] == frozen_ids
```

Also assert a series without authoritative inventory waits for scope evidence, while a movie or already-complete series does not invoke scope enrichment.

- [x] **Step 3: Add a failing non-blocking poster test**

Use a poster lookup that waits forever. Run `_prepare_plan()` and assert the candidate report already contains title/year/type/buttons before the poster task is released.

- [x] **Step 4: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_feature_service.py -k 'anchor or authoritative or douban or poster'
```

Expected failures: supplement precedes hydration, slow optional work blocks Prowlarr/candidates, or optional values can mutate the frozen contract.

- [x] **Step 5: Implement authority/presentation policy**

Add pure guards in `enrichment_policy.py`. In `_select_candidate()`:

1. hydrate existing frozen links first;
2. invoke pre-existing supplement logic in authoritative-only mode only when scope evidence is missing;
3. rehydrate the expanded source set;
4. continue to strict scope selection.

Refactor `_supplement_selected_candidate()` to accept an explicit purpose and exclude Douban/AniList/presentation-only fields from `authoritative_scope` mode.

- [x] **Step 6: Start optional work only after Prowlarr work exists**

In `_confirm_and_search()`, first persist `confirmed_contract` and
`active_prowlarr_queries`. In `_confirm_and_search_indexers()`, create the first
wave tasks and store them in `stored["indexer_tasks"]` before calling
`_start_deferred_presentation_enrichment()`. In the aggregate fallback, create
the query tasks before the same call. The enrichment task may set
`stored["deferred_contract"]` through `apply_deferred_presentation`, but it
cannot replace `confirmed_contract` or `active_prowlarr_queries`.

In `_submit_release()`, use `deferred_contract` only if already complete; never await it and never alter selected release identity.

- [x] **Step 7: Move poster enrichment off the candidate gate**

Store candidates and report/select them immediately. Start one tracked enrichment task that updates posters and submits a coalescible candidate report only while the operation remains in `candidate_selection`. Cancel/ignore it when the plan closes or advances.

- [x] **Step 8: Run the Task 2 gate**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_feature_service.py tests/test_regression_pressure.py tests/test_candidate_hydration.py
```

Expected: exit 0; hard hydration and scope tests remain strict.

### Task 3: Persist handoff and effect receipts in Core

**Files:**
- Modify: `app/115bot.py`
- Modify: `app/runtime/interaction_coordinator.py`
- Modify: `app/runtime/runtime_broker.py`
- Modify: `app/runtime/event_dispatcher.py`
- Modify: `app/runtime/event_journal.py`
- Modify: `tests/test_interaction_coordinator.py`
- Modify: `tests/test_runtime_broker.py`
- Modify: `tests/test_event_dispatcher.py`
- Modify: `tests/test_event_journal.py`
- Modify: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Produces: `HandoffReceipt` query mappings from `InteractionCoordinator.get_handoffs(operation_id)`.
- Produces: `InteractionCoordinator.record_handoff_event(operation_id, event_id, target_plugin_id)`.
- Produces: `InteractionCoordinator.fail_handoff_delivery(event_id, target_plugin_id, error_code)`.
- Produces: `InteractionCoordinator.get_effect_receipts(operation_id)`.
- Consumes: optional `details.effect_receipt = {effect_key, state, receipt}` in operation reports.

- [x] **Step 1: Add failing schema/transition tests**

Exercise:

```text
search running
  -> search handed_off(next=download): prepared
  -> download running: accepted
  -> download handed_off(next=rename): prepared
  -> event.publish: submitted with event_id
  -> rename running: accepted
  -> rename completed: terminal effect completed
```

Assert duplicate reports/event IDs are idempotent and another operation cannot reuse an effect key.

- [x] **Step 2: Add a failing dead-letter operation test**

Publish `download.completed`, exhaust one poison delivery, and assert the journal dead-letter plus:

```python
record = coordinator.get(operation_id)
assert record.state == "failed"
assert record.details["manual_check_required"] is True
assert record.details["handoff_event_id"] == event_id
```

Transient transport/internal errors must leave the operation handed off and the event pending.

- [x] **Step 3: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_interaction_coordinator.py tests/test_runtime_broker.py tests/test_event_dispatcher.py
```

Expected failure: receipt tables/methods and dead-letter projection are absent.

- [x] **Step 4: Add additive SQLite migrations**

Create `operation_handoffs` and `operation_effect_receipts` with the exact states in the spec. Derive handoff key as `<operation_id>:<source_revision>:<target_plugin_id>`. Keep existing operations and milestones readable without data rewrite.

- [x] **Step 5: Integrate receipts into one coordinator transaction**

When `report()` stores `handed_off`, upsert prepared. When a different declared owner reports, mark the latest matching prepared/submitted receipt accepted. When a terminal/cancel state wins, mark unfinished handoffs cancelled. Validate and persist optional effect receipts without logging secrets or magnet URLs.

- [x] **Step 6: Record event submission and terminal delivery failure**

Give `RuntimeBroker` coordinator access through an explicit constructor field
and wire that field from `build_plugin_manager()` in `app/115bot.py`. After
`journal.publish()` returns, record the event against a matching active
handoff. When `record_failure()` reports exhaustion, `EventDispatcher` calls
`fail_handoff_delivery()` before continuing to later events. Add a startup
construction test proving the production broker and dispatcher share the same
coordinator instance.

Capture the exact source handoff before journal publication so target
acceptance cannot race away the association. Persist an unprojected
dead-letter marker and reconcile it on later dispatcher passes/startup; a
projection failure must not terminate the dispatcher or prevent later events.
Persist that captured handoff identity atomically with the journal event, and
require the dispatcher to apply the binding before business delivery. This
closes the process-restart window between event commit and coordinator bind.

- [x] **Step 7: Run the Task 3 gate**

Rerun the Step 3 command. Expected: exit 0; transient delivery retry tests unchanged.

### Task 4: Make Telegram milestones a durable projection and coalesce reports

**Files:**
- Modify: `app/runtime/interaction_coordinator.py`
- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/115bot.py`
- Modify: `app/runtime/plugin_manager.py`
- Modify: `tests/test_interaction_coordinator.py`
- Modify: `tests/test_interaction_handler.py`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_plugin_manager.py`

**Interfaces:**
- `OperationMilestoneSink.__call__(plugin_id, payload) -> {accepted: True, queued: True, duplicate: bool}` after SQLite enqueue only.
- Produces: `OperationMilestoneSink.drain()` for tests/shutdown and recovery scheduling on `attach()`.
- `OperationReportSink` maintains one render worker and one latest pending record per operation.

- [x] **Step 1: Replace the synchronous handoff-render test with RED assertions**

Block the renderer forever, await a handed-off report with a short timeout, and assert it is accepted. Submit 50 later revisions while one render is blocked and assert the listener receives only the first in-flight record and latest remaining record.

- [x] **Step 2: Add failing durable milestone tests**

Assert RPC return precedes a blocked delivery, persisted state is `pending/delivering`, known rejection is recorded/retried within the bound, and an uncertain exception becomes `unknown` without blind resend.

Add a cursor race test: a search milestone begins with message 41, download/rename establishes message 42, and late search completion must not clear 42.

- [x] **Step 3: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_interaction_handler.py tests/test_interaction_coordinator.py tests/test_bot_runtime_startup.py
```

Expected failure: handoff still awaits rendering and milestone payload/delivery state is not durably queued.

- [x] **Step 4: Extend milestone persistence**

Add additive columns for mode, text, photo URL, delivery state, attempts, last error, and updated time. Existing delivered rows migrate to `delivered`. Store payload before returning accepted.

Persist the enqueue-time expected message ID/kind (SQL `NULL` is an exact
expected cursor, never a wildcard). A legacy row with no durable payload may
only migrate to `delivered` when already complete, or complete without a send
when it has a recorded target; every other legacy incomplete row becomes
`unknown`, including an unstarted row, because it cannot be replayed safely.

- [x] **Step 5: Implement bounded background milestone delivery**

Schedule one worker per `(operation_id, milestone_id)`. Known `accepted=False` may retry up to three attempts. An exception after delivery starts is stored as `unknown` and not automatically resent. On attach/startup, resume only safe `pending/failed` intents; resolve `delivering` without a target as unknown.

Telegram `BadRequest`-style explicit rejection may use the existing safe
fallback; transport/time-out exceptions must propagate to the worker as
uncertain instead of being converted into `accepted=False` and retried.

`attach()` only registers delivery dependencies because production calls it
before `asyncio.run`. Start/recovery must be invoked idempotently from
`start_host_runtime()` on the running loop. Shutdown stops Feature/Broker
producers first, drains/cancels and awaits both projection sinks while the
coordinator is open, and closes SQLite only afterward.

- [x] **Step 6: Make cursor clearing compare-and-set**

Persist the enqueue-time expected cursor (including the `NULL` case). Only
clear the active cursor when plugin owner and that expected cursor still match;
the newly delivered message ID is audit evidence, not the compare value. This
keeps a late search milestone from clearing a download/rename cursor created
after enqueue. Completion without a known target never clears a later owner's
cursor.

- [x] **Step 7: Coalesce operation rendering**

Persist every report immediately. If a renderer is active, overwrite that operation's pending projection with the latest revision. The worker loops until no pending record remains. Remove the special synchronous `handed_off` path.

The real `render_operation()` path must render the supplied persisted snapshot;
it must not replace the first in-flight record by reloading the latest row.
Rejected stale/owner-mismatch reports cannot overwrite the pending projection.

- [x] **Step 8: Run the Task 4 gate**

Rerun the Step 3 command and the focused Telegram diagnostics tests. Expected: exit 0, with no unobserved task exception warnings.

### Task 5: Migrate Feature handoffs to receipt semantics

**Files:**
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/download/tests/test_feature_runtime.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `tests/test_operation_pipeline_e2e.py`

**Interfaces:**
- Download acceptance report emits `download.submit:<job_id>` completed receipt.
- Download handoff/event is represented only by Task 3's Core handoff/event
  ledger (semantic key `rename.enqueue:<job_id>`); do not create a parallel
  effect receipt.
- Rename terminal report emits `rename.organize:<job_id>` with organized, cleanup, partial, and final path values.

- [x] **Step 1: Add failing Feature failure-semantics tests**

For search and download, make milestone delivery block/fail after durable enqueue and assert capability/event submission occurs exactly once. For rename, assert final receipt exists and no `media.organized`/sync call occurs.

- [x] **Step 2: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_feature_service.py -k 'stage or handoff or receipt'
```

Run the equivalent focused download and rename tests. Expected failure: effect receipts are missing or tests still encode Telegram delivery as a business prerequisite.

- [x] **Step 3: Emit stable receipts without new public SDK methods**

Add `details.effect_receipt` to the target's first accepted operation report and rename's terminal report. Keep existing operation/milestone/event RPC signatures. Persist event IDs in download job result so restart replay uses the same event idempotency key.

Persist the Download completion event idempotency key before publication and
the returned event ID before marking the job completed; replay must converge to
the same ID. Persist Rename's exact terminal operation report before sending
it, and on an ambiguous/lost response replay that same revision/details rather
than first emitting a newer running report.

- [x] **Step 4: Relax only presentation failure gates**

Treat milestone response `accepted=True` as queued intent. A Host RPC transport error before durable acceptance remains retryable, but a later Telegram delivery failure never fails Search, Download, or Rename. Do not relax target-Feature availability or Host ownership rejection.

- [x] **Step 5: Run Feature and E2E gates**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_operation_pipeline_e2e.py tests/test_event_dispatcher.py
```

Run complete Search, Download, and Rename suites. Expected: exit 0 and the blocked-Telegram E2E reaches rename terminal.

### Task 6: Reuse one rename file-transaction snapshot

**Files:**
- Modify: `features/rename/src/telepiplex_rename/file_executor.py`
- Modify: `features/rename/src/telepiplex_rename/processor.py`
- Modify: `features/rename/tests/test_file_executor.py`
- Modify: `features/rename/tests/test_file_first_processor.py`
- Modify: `features/rename/tests/test_regression_pressure.py`

**Interfaces:**
- Produces immutable `FileTransactionSnapshot(file_info, source_parent_ids)`.
- `execute_file_resolutions(..., preflight: FileTransactionSnapshot | None = None)` consumes only pre-mutation facts.
- `_prepare_native_move(..., source_parent_id: str)` does not re-read a known parent.

- [x] **Step 1: Add failing same-parent and multi-parent call-count tests**

For 16 resolved files under one source parent, assert one parent-ID lookup during preparation; for two source parents, assert exactly two. Assert native move outcomes and all post-move listings remain unchanged.

- [x] **Step 2: Add failing snapshot safety tests**

Cover target appearing after preflight, source ID changing before mutation, rename target ID mismatch, submitted-but-not-applied move, source still present, and cleanup deletion still present. Every case must fail closed and keep the relevant source.

- [x] **Step 3: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_file_executor.py tests/test_file_first_processor.py
```

Expected failure: source parent is read repeatedly and no transaction snapshot interface exists.

- [x] **Step 4: Build the snapshot before mutation**

In `process_file_first_media()`, combine target preflight with source/parent preparation and pass the immutable snapshot to the executor. Deduplicate by normalized path. Never persist the snapshot in a job record.

- [x] **Step 5: Reuse only safe facts**

Use source-parent IDs for grouping and preflight source/target facts for initial conflict/replay decisions. Keep fresh reads performed by rename journal verification, native move reconciliation, and directory cleanup. Do not use a batch snapshot for a postcondition.

- [x] **Step 6: Add an instrumented 16-file budget assertion**

Use the fake storage recorder to assert the equivalent successful transaction performs at most 60 capability calls and still performs fresh source/target listing plus cleanup checks.

- [x] **Step 7: Run the Task 6 gate**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_file_executor.py tests/test_file_first_processor.py tests/test_regression_pressure.py tests/test_operations.py
```

Expected: exit 0, with zero weakened safety assertion.

### Task 7: Add Search single-flight and deterministic Prowlarr waves

**Files:**
- Create: `features/search/src/telepiplex_search/source_schedule.py`
- Create: `features/search/src/telepiplex_search/prowlarr_waves.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/config.default.yaml`
- Modify: `features/search/config.schema.json`
- Create: `features/search/tests/test_source_schedule.py`
- Create: `features/search/tests/test_prowlarr_waves.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/tests/test_config_schema_contract.py`

**Interfaces:**
- `SourceRequestKey(provider, purpose, media_type, identity, scope, season_number, episode_number)`.
- `await SourceScheduler.run(key, fetch)` returns a deep copy and caches successful values for a bounded TTL.
- `plan_prowlarr_waves(indexers, *, explicit_ids, indexer_scores) -> tuple[first_wave, remaining_wave]`.

- [x] **Step 1: Add RED single-flight tests**

Assert two concurrent identical keys call fetch once; values are independent copies; different IDs/types/scopes/purposes do not merge; one waiter cancellation does not cancel another; a transient failure is retried by the next call.

- [x] **Step 2: Add RED wave-planner tests**

Assert explicit IDs win, otherwise positive `indexer_scores` names form the first wave, no match yields one compatibility wave, order is stable, and no indexer is duplicated or lost.

- [x] **Step 3: Add RED service timing tests**

With a fake clock/task barrier, assert first-wave tasks start before remaining tasks, remaining tasks start no later than 1.5 seconds, first-wave eligible results are reported through the existing incremental gate, and user selection cancels unstarted/running remainder work.

- [x] **Step 4: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_source_schedule.py tests/test_prowlarr_waves.py tests/test_feature_service.py -k 'indexer or source_schedule or incremental'
```

Expected failure: modules/interfaces are absent and all indexer tasks start together.

- [x] **Step 5: Implement bounded SourceScheduler**

Use an asyncio lock for the flight map, `asyncio.shield()` for each waiter, monotonic TTL, a maximum of 256 success entries, deep-copy on store/return, and no failure caching. Integrate it at duplicate poster/localization/confirmed-source lookup sites without moving identity selection logic into the scheduler.

- [x] **Step 6: Implement wave planning and service launch**

Add `first_wave_indexer_ids: []` and `wave_delay: 1.5` to config. Refactor `_confirm_and_search_indexers()` so wave start changes but its existing `search_variant`, state aggregation, gate/rank update, stable callbacks, global timeout, and final summary remain shared.

- [x] **Step 7: Run the Task 7 gate**

Run all Search tests. Expected: exit 0, existing first-eligible and stable-selection tests unchanged.

### Task 8: Add endpoint-aware 115 pacing and adaptive download polling

**Files:**
- Create: `features/download/src/telepiplex_download/pacing.py`
- Modify: `features/download/src/telepiplex_download/client.py`
- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/download/config.default.yaml`
- Modify: `features/download/config.schema.json`
- Create: `features/download/tests/test_client_pacing.py`
- Modify: `features/download/tests/test_client_move_safety.py`
- Modify: `features/download/tests/test_feature_runtime.py`

**Interfaces:**
- `EndpointPacer.acquire(endpoint_class) -> wait_seconds` and `observe_throttle(endpoint_class, retry_after)`.
- `_request()` classifies paths without changing public storage capability methods.
- `wait_for_download(..., poll_initial_interval, poll_max_interval, poll_backoff_factor, ...)`.

- [x] **Step 1: Add RED pacer tests**

Use an injected monotonic clock/sleeper. Assert storage reads use their own minimum interval, mutations remain at least the legacy safety interval, HTTP 429 `Retry-After` cools only the affected class, and token refresh/retry cannot bypass pacing.

- [x] **Step 2: Add RED bounded batch tests**

Assert `get_file_info_batch()` deduplicates at most 32 paths, uses configured bounded read workers, returns every normalized key, and turns individual failures into missing values rather than a fabricated successful identity.

- [x] **Step 3: Add RED adaptive polling tests**

Assert unchanged state yields approximately `2.0, 3.4, 5.78, ... <= 30`, progress/status change resets to 2 seconds, completion returns immediately on the current poll, and cancellation interrupts a 30-second wait.

Simulate 30 minutes with no change and assert fewer than 90 list calls versus 180 fixed-10-second calls.

- [x] **Step 4: Verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_client_pacing.py tests/test_client_move_safety.py tests/test_feature_runtime.py -k 'pacing or batch or poll or download'
```

Expected failure: pacer module/new polling arguments are absent and delays remain fixed.

- [x] **Step 5: Implement EndpointPacer and request classification**

Default intervals:

```yaml
endpoint_intervals:
  offline_poll: 1.0
  offline_mutation: 1.0
  storage_read: 0.25
  storage_mutation: 1.0
  token_refresh: 1.0
storage_read_workers: 4
```

Use class-specific locks/timestamps, a bounded cooldown, and `Retry-After` parsing. Keep public method return shapes and token-redaction behavior unchanged.

- [x] **Step 6: Implement bounded file-info reads**

Use at most `storage_read_workers` for cache misses. Synchronize cache access. Do not parallelize mutations, move reconciliation, or cleanup.

- [x] **Step 7: Implement adaptive polling**

Add defaults `poll_initial_interval: 2`, `poll_max_interval: 30`, and `poll_backoff_factor: 1.7`. Compare a stable tuple of info hash, resource name, provider status, and progress. Reset only on a real change. Keep `cancel_event.wait(delay)`.

- [x] **Step 8: Run the Task 8 gate**

Run all Download tests and the Rename storage tests because both use the same provider. Expected: exit 0.

### Task 9: Version, documentation, full verification, and performance evidence

**Files:**
- Modify: `app/115bot.py`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_technical_identity_migration.py`
- Modify: `tests/test_unraid_publish_script.py`
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/search/src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `features/search/tests/test_config_schema_contract.py`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/download/manifest.yaml`
- Modify: `features/download/pyproject.toml`
- Modify: `features/download/README.md`
- Modify: `features/download/src/telepiplex_download.egg-info/PKG-INFO`
- Modify: `features/download/tests/test_feature_runtime.py`
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/pyproject.toml`
- Modify: `features/rename/README.md`
- Modify: `features/rename/src/telepiplex_rename.egg-info/PKG-INFO`
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `docs/superpowers/plans/2026-08-23-media-pipeline-business-performance.md`

**Interfaces:**
- Produces aligned local release identities: Host `v3.5.5-host`, Search `1.11.5`, Download `1.0.18`, Rename `1.5.8`.
- SDK and Host API protocol versions remain unchanged because no public RPC signature changes.

- [x] **Step 1: Update each changed package version exactly once**

Align manifests, `pyproject.toml`, checked-in metadata/version tests, release script expectations, and maintained README references. Do not change sync or caption versions.

- [x] **Step 2: Run complete package suites**

```bash
cd /Users/young/Documents/telepiplex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
```

```bash
cd /Users/young/Documents/telepiplex/features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
```

Run the same complete command in `features/download`, `features/rename`,
`features/sync`, and `features/caption`. Every command must exit 0.

- [x] **Step 3: Run static and packaging checks**

Compile touched Python modules with `python -m compileall -q`. Build changed Feature packages only under `/tmp`, inspect their manifests, and remove temporary archives after recording results.

- [x] **Step 4: Run deterministic performance scenarios**

Record before/after evidence for:

- candidate availability with a blocked poster;
- Prowlarr first-wave and first-eligible timestamps;
- 30-minute adaptive polling call count;
- 16-file rename storage call count and required fresh postcondition calls;
- 50-report render burst actual delivery count.

The acceptance thresholds are those in the spec; correctness gates take precedence over latency thresholds.

- [x] **Step 5: Perform final whole-change review**

Review every spec requirement against a test or measured scenario, inspect all changed files without Git, run a focused re-test for any review fix, and record any ruling in the execution ledger.

- [x] **Step 6: Verify workspace boundary**

```bash
test ! -e /Users/young/Documents/telepiplex/.git
test ! -e /Users/young/Documents/telepiplex/.worktrees
test -d /Users/young/Documents/telepiplex/.stfolder
```

Expected: all commands exit 0. Delivery stops locally; wait for Syncthing `Up to Date / 最新` before Unraid-side publication.

## Execution ledger format

For each task, append a short entry to the plan-specific local ledger containing:

```text
Task N RED: command, expected failure
Task N GREEN: command, passed/failed counts
Task N changed files: exact paths
Task N review: spec verdict, quality verdict, open findings
Ruling: decision — reason — cost if wrong
Task N complete
```

The ledger is stored outside Git-oriented tooling under
`.superpowers/sdd/2026-08-23-media-pipeline-business-performance/progress.md`.

## Self-review iteration 1

The first complete draft was checked against every spec section, scanned for
placeholder language, and compared across shared interfaces/files. This review
made three concrete corrections before implementation:

1. The deferred localization start point was too ambiguous: “query snapshot
   exists” could still run before a Prowlarr task. Task 2 now requires actual
   indexer/aggregate tasks to exist before optional enrichment starts.
2. Task 9 used vague “maintained assertions/docs” file entries. They are now
   replaced by exact Host, Search, Download, Rename, root-test, README, and
   checked-in package metadata paths.
3. P0/P1 coverage was implicit in task names. The requirement coverage matrix
   now gives every included item a task and an objective completion artifact,
   and explicitly records that P1-4 remains out of scope.

No placeholder markers or inconsistent public interface names remain after
these corrections. Shared-file sequencing is intentional: Task 3 adds Core
business receipts, Task 4 adds presentation queuing/coalescing, and Task 5 only
migrates Feature-produced receipt data onto those completed Core contracts.
