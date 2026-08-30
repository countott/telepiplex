# telepiplex Single-Operation Telegram Critical Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove avoidable Telegram network waits from one media task while preserving durable segment, milestone, recovery, and terminal semantics.

**Architecture:** Track callback feedback independently from Feature dispatch and reconcile the exact durable segment after any late busy write. Extend the existing per-operation latest-record worker so report plus seal can collapse into one final Telegram edit, and add one Host-level lifecycle owner for operation and milestone projection drains.

**Tech Stack:** Python 3.12, asyncio, python-telegram-bot, SQLite-backed `InteractionCoordinator`, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-08-28-single-operation-telegram-critical-path-design.md`

## Global Constraints

- Product copy and metadata use lowercase `telepiplex`.
- One user runs one media task at a time; no PTB or EventDispatcher concurrency change.
- Preserve callback exactly-once claim, segment ordering, milestone durability, terminal projection, retries, and shutdown drain.
- Mac workspace must not run Git or create Git/worktree metadata.
- Host version becomes exactly `v3.6.6-host`; Feature and SDK versions remain unchanged.

---

### Task 1: Tracked callback feedback and exact-segment reconciliation

**Files:**
- Modify: `tests/test_interaction_handler.py`
- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/115bot.py`

**Interfaces:**
- Produces: `schedule_callback_feedback(update, application, record, segment) -> asyncio.Task`
- Produces: `drain_callback_feedback(application, timeout=None) -> bool`
- Produces: `reconcile_segment_projection(application, router, operation_id, segment_id, generation, message_id) -> int | None`

- [x] Write a test where busy Telegram delivery is blocked but `dynamic_callback_gateway` reaches the real route client before busy delivery is released.
- [x] Run that test and verify it fails because `operation_gate` still awaits busy render.
- [x] Implement the tracked feedback registry and schedule ACK/busy without awaiting them in `operation_gate`.
- [x] Run the focused test and verify it passes.
- [x] Write a late-apply test where terminal/seal wins durably, busy arrives later, and the exact old segment is restored to its durable projection.
- [x] Run it and verify it fails because current rerender only handles the active segment.
- [x] Implement exact-segment reconciliation and feedback-task finalization.
- [x] Add feedback drain to Host shutdown and run focused interaction/startup tests.

### Task 2: Latest projection plus seal Telegram-call coalescing

**Files:**
- Modify: `tests/test_interaction_handler.py`
- Modify: `app/handlers/interaction_handler.py`

**Interfaces:**
- Consumes: existing `OperationReportSink._pending`, `_pending_segment_ids`, and per-operation worker.
- Produces: deterministic report-plus-seal behavior that performs one content edit with `reply_markup=None` before durable seal.

- [x] Write a test that submits a newer segment report followed immediately by seal and asserts one Telegram edit and no separate reply-markup edit.
- [x] Run it and verify the current renderer performs two Telegram writes.
- [x] Add the bounded initial coalescing window and always enqueue the sealing target.
- [x] Complete a sealing segment immediately after a successful latest-content edit that already cleared markup.
- [x] Run focused report/segment tests and verify all seal recovery paths still pass.

### Task 3: One projection lifecycle owner

**Files:**
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_plugin_manager.py`
- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/115bot.py`
- Modify: `app/runtime/plugin_manager.py`

**Interfaces:**
- Produces: `OperationProjectionLifecycle` containing `operation_sink`, `milestone_sink`, `attach`, `start`, and `drain`.
- RuntimeBroker retains its existing `operation_sink` and `milestone_sink` call contracts through the lifecycle-owned instances.

- [x] Write startup and shutdown tests proving one lifecycle starts milestone recovery, attaches both deliveries, drains both sinks, and drains callback feedback.
- [x] Run them and verify current wiring has no lifecycle owner.
- [x] Implement the façade without changing either sink's durable semantics.
- [x] Update Host startup/configure/shutdown and PluginManager close to use it.
- [x] Run broker, manager, startup, milestone, and operation pipeline tests.

### Task 4: Single-task performance regression contract

**Files:**
- Modify: `tests/test_pressure_telegram_pipeline.py`
- Modify: `tools/pressure_telegram_pipeline.py`

**Interfaces:**
- Produces: `correctness.callback_feedback_drain_completed` and per-pipeline Telegram API call statistics.

- [x] Write a pressure test requiring callback feedback drain and a bounded per-task Telegram API call count.
- [x] Run it and verify the new fields are absent or the budget fails.
- [x] Add the metrics and drain integration without weakening existing correctness gates.
- [x] Run the pressure test and a 10-task sequential 50 ms Telegram scenario.

### Task 5: Host patch version and full local verification

**Files:**
- Modify: `app/115bot.py`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: version-sensitive publisher tests only where the checked-out Host identity is intentionally asserted.

**Interfaces:**
- Produces: Host identity `v3.6.6-host` and publish source version `3.6.6`.

- [x] Update the version expectation test first and verify it fails with `v3.6.5-host`.
- [x] Change the single Host version source to `v3.6.6-host` and update intentional checked-out-version fixtures.
- [x] Run version and publisher tests.
- [x] Run all Host and all five Feature suites with the bundled Python 3.12 runtime.
- [x] Run single-task pressure verification and compare against the recorded 1.418 s / 12-call baseline.
- [x] Record changed files and actual results; do not run Git. Wait for Syncthing `Up to Date / 最新` before user-controlled Unraid publication.
