# Core Feature Interaction Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Core dynamically advertise active Feature commands and coordinate one cancellable, status-reporting Telegram operation per user across every current Feature.

**Architecture:** Core API 1.1 adds a persistent `InteractionCoordinator`, an authenticated operation report/control protocol, a global Telegram input gate, and a shared dynamic command catalog. Each Feature keeps domain execution and compensation local, reports stage changes to Core, propagates one `operation_id` through capability/event handoffs, and exposes exactly one appropriate exit/cancel control at every non-terminal interaction step.

**Tech Stack:** Python 3.12, python-telegram-bot, asyncio Unix-socket RPC, SQLite, PyYAML, unittest/pytest, Telepiplex `.tpx` builder.

## Global Constraints

- Implement only in `feature/telepiplex-core`, `feature/open115`, `feature/media-search`, `feature/renaming`, and `feature/plex-management`; leave empty `main` untouched.
- Do not add `/mag` or `/scan`.
- Core 1.1 remains compatible with Core API 1.0 Features.
- Coordinated Feature releases declare `core_api: ">=1.1,<2.0"` and depend on `telepiplex-plugin-sdk==1.1.0`.
- Ordinary text and commands received during running/cancelling/rolling-back states are dropped, never queued.
- Never delete downloaded media as rollback.
- Use **退出** for pre-execution interactions, **取消任务** for non-reversible work, and **取消并回滚** only for fully journaled and verified inverse operations.
- A 115 cancellation attempts standard offline-task deletion once only when an unambiguous InfoHash exists; otherwise it keeps the task record and reports it.
- State reports and errors exclude secrets and raw magnet links.

---

### Task 1: Core API 1.1 operation protocol

**Files:**
- Modify: `sdk/pyproject.toml`
- Modify: `sdk/src/telepiplex_plugin_sdk/core_client.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/runtime.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/runner.py`
- Modify: `app/core/plugin_contract.py`
- Modify: `app/core/core_broker.py`
- Test: `tests/test_plugin_sdk_runtime.py`
- Test: `tests/test_core_broker.py`

**Interfaces:**
- Produces: `CORE_API_VERSION = "1.1"`.
- Produces: `CoreClient.report_operation(report: dict, *, deadline: float = 10) -> dict` via Core RPC `operation.report`.
- Produces: `FeatureRuntime(..., operation_control: Handler | None = None, operation_snapshot: Handler | None = None)`.
- Produces: Feature RPC methods `operation.control` and `operation.snapshot`.
- Consumes later: `operation_sink(plugin_id: str, report: dict) -> dict | Awaitable[dict]` in `CoreBroker`.

- [ ] **Step 1: Write failing SDK and broker tests**

Add these behaviors:

```python
async def test_operation_control_dispatches_to_registered_handler(self):
    seen = []
    async def control(request):
        seen.append(request)
        return {"operation_id": request["operation_id"], "state": "cancelling", "revision": 2}
    runtime, _ = await self._start(echo, operation_control=control)
    result = await RpcClient(self.socket_path, "token").request(
        "operation.control",
        {"operation_id": "op-1", "action": "cancel", "revision": 1},
        deadline=1,
    )
    self.assertEqual(result["state"], "cancelling")
    self.assertEqual(seen[0]["action"], "cancel")
```

```python
async def test_operation_report_uses_authenticated_feature_identity(self):
    sink = AsyncMock(return_value={"accepted": True, "revision": 1})
    broker = self._broker(operation_sink=sink)
    client = CoreClient(broker.socket_path, "echo-token")
    result = await client.report_operation({
        "operation_id": "op-1", "chat_id": 10, "user_id": 1,
        "state": "running", "stage": "planning", "status_text": "规划中",
        "control": "cancel", "revision": 1,
    })
    self.assertTrue(result["accepted"])
    self.assertEqual(sink.await_args.args[0], "echo")
```

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m unittest tests.test_plugin_sdk_runtime tests.test_core_broker -v
```

Expected: failures because the new constructor arguments and RPC methods do not exist.

- [ ] **Step 3: Implement the optional protocol**

Implement `CoreClient.report_operation()` as `_request("operation.report", report, ...)`. Extend `FeatureRuntime._dispatch()` with optional `operation.control` and `operation.snapshot` business calls. Extend `CoreBroker._dispatch()` to pass reports only through the authenticated identity to `operation_sink`.

Update SDK version to `1.1.0`, runner context core range to `>=1.1,<2.0`, and Core API version to `1.1`. Keep every existing API 1.0 method unchanged.

- [ ] **Step 4: Verify GREEN**

```bash
python3 -m unittest tests.test_plugin_sdk_runtime tests.test_core_broker tests.test_plugin_runtime_e2e -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add sdk app/core/plugin_contract.py app/core/core_broker.py tests/test_plugin_sdk_runtime.py tests/test_core_broker.py
git commit -m "feat(core): add Feature operation protocol"
```

---

### Task 2: Persistent Core interaction coordinator

**Files:**
- Create: `app/core/interaction_coordinator.py`
- Test: `tests/test_interaction_coordinator.py`

**Interfaces:**
- Produces: immutable `OperationRecord` containing IDs, owner, state, stage, status, control, revision, message ID, sanitized details, and timestamps.
- Produces: `InteractionCoordinator.report(plugin_id: str, report: dict) -> OperationRecord`.
- Produces: `active(chat_id: int, user_id: int) -> OperationRecord | None`.
- Produces: `set_message_id(operation_id: str, message_id: int) -> OperationRecord`.
- Produces: `interrupt_unowned(active_plugin_ids: set[str]) -> list[OperationRecord]`.

- [ ] **Step 1: Write failing transition tests**

Test one-active-operation enforcement, owner authentication, atomic handoff, revision monotonicity, terminal gate release, SQLite reload, and stale-operation interruption:

```python
def test_late_revision_cannot_overwrite_cancelled_state(self):
    current = self.coordinator.report("media-search", self.report(revision=3, state="cancelled"))
    stale = self.coordinator.report("media-search", self.report(revision=2, state="running"))
    self.assertEqual(stale, current)
    self.assertIsNone(self.coordinator.active(10, 1))
```

```python
def test_handoff_changes_owner_without_releasing_gate(self):
    self.coordinator.report("media-search", self.report(
        state="handed_off", next_plugin_id="open115", revision=2,
    ))
    record = self.coordinator.report("open115", self.report(
        state="running", stage="download", revision=3,
    ))
    self.assertEqual(record.plugin_id, "open115")
    self.assertEqual(self.coordinator.active(10, 1).operation_id, "op-1")
```

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m unittest tests.test_interaction_coordinator -v
```

Expected: import failure for the coordinator module.

- [ ] **Step 3: Implement SQLite coordination**

Create an `operations` table in `core.db`. Validate these exact sets:

```python
VALID_STATES = {
    "awaiting_input", "running", "handed_off", "cancelling", "rolling_back",
    "completed", "cancelled", "rolled_back", "partially_rolled_back",
    "failed", "interrupted",
}
VALID_CONTROLS = {"", "exit", "cancel", "rollback"}
TERMINAL_STATES = {
    "completed", "cancelled", "rolled_back", "partially_rolled_back",
    "failed", "interrupted",
}
```

Reject owner changes unless stored state is `handed_off` and `next_plugin_id` matches. Ignore reports at or below the stored revision. Cap status text at 4096 characters and serialize only JSON-compatible details.

- [ ] **Step 4: Verify GREEN**

```bash
python3 -m unittest tests.test_interaction_coordinator -v
git diff --check
```

Expected: tests pass and diff check is silent.

- [ ] **Step 5: Commit**

```bash
git add app/core/interaction_coordinator.py tests/test_interaction_coordinator.py
git commit -m "feat(core): persist coordinated interactions"
```

---

### Task 3: Telegram operation gate, status renderer, and controls

**Files:**
- Create: `app/handlers/interaction_handler.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/115bot.py`
- Test: `tests/test_interaction_handler.py`
- Test: `tests/test_plugin_handler.py`
- Test: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Produces: `operation_gate(update, context)` registered before normal handlers.
- Produces: callback namespace `core-operation:<action>:<operation_id>`.
- Produces: `render_operation(application, router, record)` with edit-then-send fallback.
- Consumes: `result["operation"]` from Feature responses.

- [ ] **Step 1: Write failing gate and renderer tests**

Prove unrelated commands are stopped with `ApplicationHandlerStop`, unrelated callbacks receive only `当前任务执行中`, the active Feature's awaiting-input callback remains routable, repeated controls are idempotent, and failed status edits send a replacement message whose ID is persisted.

```python
async def test_running_operation_drops_unrelated_command(self):
    with self.assertRaises(ApplicationHandlerStop):
        await operation_gate(update_with_text("/search test"), context)
    route.client.request.assert_not_awaited()
```

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m unittest tests.test_interaction_handler tests.test_plugin_handler tests.test_bot_runtime_startup -v
```

Expected: missing handler and missing operation-result support failures.

- [ ] **Step 3: Implement the global gate and control callback**

Register a high-priority handler before command/callback/text routing. In `awaiting_input`, allow only plain text and callback namespaces owned by the active Feature. In running states, stop every ordinary message and command; allow only the matching `core-operation` control.

The control callback validates ownership, dispatches `operation.control` to the current Feature route, and sends its result through `handle_feature_result()`. Extend result handling to persist `result["operation"]` after rendering actions and to store a returned message ID. Closing a session closes an awaiting-input operation owned by that Feature.

- [ ] **Step 4: Connect broker reports and startup recovery**

Build one coordinator from `core.db`, pass its authenticated sink to `CoreBroker`, attach a Telegram render listener after application creation, and query active Features through `operation.snapshot` after `manager.start()`. Mark rows with no confirmed owner `interrupted`.

- [ ] **Step 5: Verify GREEN**

```bash
python3 -m unittest tests.test_interaction_coordinator tests.test_interaction_handler tests.test_plugin_handler tests.test_bot_runtime_startup tests.test_core_broker -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/handlers app/115bot.py tests/test_interaction_handler.py tests/test_plugin_handler.py tests/test_bot_runtime_startup.py
git commit -m "feat(core): coordinate Telegram Feature tasks"
```

---

### Task 4: Dynamic `/start` and Telegram command menu

**Files:**
- Create: `app/core/command_catalog.py`
- Modify: `app/115bot.py`
- Modify: `app/handlers/plugin_handler.py`
- Test: `tests/test_command_catalog.py`
- Test: `tests/test_bot_runtime_startup.py`
- Test: `tests/test_plugin_handler.py`

**Interfaces:**
- Produces: `build_bot_commands(router) -> list[BotCommand]`.
- Produces: `build_start_help(router, core_version: str) -> str`.
- Produces: `sync_bot_commands(application, router) -> bool`.

- [ ] **Step 1: Write failing catalog tests**

Use parsed manifests to assert Core-first ordering, Feature sorting, manifest order, inactive/blocked exclusion, reserved-name suppression, and HTML-safe help:

```python
def test_combines_core_and_active_feature_commands(self):
    commands = build_bot_commands(router_with("open115", "media-search"))
    names = [item.command for item in commands]
    self.assertEqual(names[:4], ["start", "reload", "plugin", "config"])
    self.assertIn("magnet", names)
    self.assertIn("search", names)
    self.assertEqual(names.count("config"), 1)
```

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m unittest tests.test_command_catalog tests.test_bot_runtime_startup tests.test_plugin_handler -v
```

Expected: missing command catalog and static help/menu failures.

- [ ] **Step 3: Implement live command builders and sync hooks**

Build only from active router registrations. Escape Feature names and descriptions for HTML. Suppress `start`, `reload`, `plugin`, and `config` from Feature sections. Change `/start` to use the live router. Synchronize after Core startup and after successful install, update, enable, disable, rollback, and remove. Report menu-sync failure without reverting the completed lifecycle operation.

- [ ] **Step 4: Verify GREEN**

```bash
python3 -m unittest tests.test_command_catalog tests.test_bot_runtime_startup tests.test_plugin_handler tests.test_capability_router -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/core/command_catalog.py app/115bot.py app/handlers/plugin_handler.py tests/test_command_catalog.py tests/test_bot_runtime_startup.py tests/test_plugin_handler.py
git commit -m "feat(core): publish active Feature commands"
```

---

### Task 5: open115 exits, task status, and cancellation

**Files (worktree `.worktrees/open115`):**
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `src/telepiplex_open115/client.py`
- Modify: `src/telepiplex_open115/service.py`
- Modify: `src/telepiplex_open115/runtime.py`
- Modify: `src/telepiplex_open115/jobs.py`
- Test: `tests/test_feature_runtime.py`

**Interfaces:**
- Consumes: `CoreClient.report_operation()` and `FeatureRuntime.operation_control`.
- Propagates: `operation_id` through download capability payload/result and download events.
- Produces: `Open115Feature.operation_control(request: dict) -> dict`.
- Produces: `Open115Client.wait_for_download(..., cancel_event=None, progress_callback=None)`.

- [ ] **Step 1: Write failing interaction and cancellation tests**

Assert every open auth/path response has exactly one exit, invalid token input retains it, QR polling is cancellable, and download progress reports `preparing`, `submitted`, `downloading`, `reading_files`, and `handoff_renaming`. Test one offline-task deletion attempt when InfoHash is known and preservation when it is absent or deletion fails.

```python
async def test_cancelled_download_never_deletes_media(self):
    await feature.download_capability({
        "method": "submit",
        "payload": {"link": MAGNET, "selected_path": "/Downloads", "operation_id": "op-1"},
        "context": {"idempotency_key": "op-1"},
    })
    result = await feature.operation_control({
        "operation_id": "op-1", "action": "cancel", "revision": 3,
    })
    self.assertEqual(result["operation"]["state"], "cancelling")
    self.assertLessEqual(len(client.deleted_tasks), 1)
    self.assertEqual(client.deleted_files, [])
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m pytest -q tests/test_feature_runtime.py
```

Expected: failures for operation descriptors, progress reports, and control handling.

- [ ] **Step 3: Implement managed task state and controls**

Keep one session `operation_id` through authorization or path selection. Add an exit callback to prompts without an existing cancel path. Register `operation_control` and `operation_snapshot` in runtime.

Track each download with a thread-safe cancellation event, current InfoHash, chat/user IDs, and revision. Extend the polling client to check the cancellation event and call a progress callback. Check cancellation before each external stage. Never invoke `delete_single_file()` from cancellation. If an unambiguous InfoHash exists, attempt `del_offline_task(info_hash, 0)` once and report whether the record remains.

Update Feature/package version to `1.1.0`, SDK to `1.1.0`, Core range to `>=1.1,<2.0`, and remove the reserved `config` manifest command while retaining `/auth` and Core `/config`.

- [ ] **Step 4: Verify GREEN and the full worktree**

```bash
python3 -m pytest -q
python3 -m py_compile $(rg --files src -g '*.py')
git diff --check
```

Expected: all tests pass and checks exit zero.

- [ ] **Step 5: Commit**

```bash
git add manifest.yaml pyproject.toml src tests/test_feature_runtime.py
git commit -m "feat(open115): expose cancellable task interactions"
```

---

### Task 6: media-search asynchronous stages and complete exits

**Files (worktree `.worktrees/media-search`):**
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `src/telepiplex_media_search/config_wizard.py`
- Modify: `src/telepiplex_media_search/service.py`
- Modify: `src/telepiplex_media_search/runtime.py`
- Test: `tests/test_config_wizard.py`
- Test: `tests/test_feature_service.py`

**Interfaces:**
- Produces: `MediaSearchFeature.operation_control()` and `operation_snapshot()`.
- Hands the unchanged `operation_id` to `download.provider.submit`.

- [ ] **Step 1: Write failing wizard and stage tests**

Assert each open config/search prompt has one exit/cancel control. Assert query planning and Prowlarr work return a running descriptor promptly, later report selection keyboards, and cancellation clears allocator/plan state without download submission.

```python
async def test_submission_hands_operation_to_open115(self):
    await feature._submit_release("plan-1", stored, "0", operation_id="op-1")
    self.assertEqual(core.calls[0][2]["operation_id"], "op-1")
    self.assertEqual(core.reports[-1]["state"], "handed_off")
    self.assertEqual(core.reports[-1]["next_plugin_id"], "open115")
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m pytest -q tests/test_config_wizard.py tests/test_feature_service.py
```

Expected: failures for missing controls and synchronous task behavior.

- [ ] **Step 3: Implement background planning, search, and reporting**

Bind runtime to the service. Spawn stable tasks for planning, Prowlarr lookup, and release submission. Report `planning`, `evidence_lookup`, `prowlarr_search`, `resolving_release`, and `submitting_download`. Report `awaiting_input` with current namespaced keyboards for user selection.

Cancellation stops the managed coroutine, releases the temporary-special allocator, removes plan/session data, and reports `cancelled`. Configuration application remains Core-owned and uses its existing atomic restore path.

Update Feature/package version, SDK dependency, and Core range to 1.1.

- [ ] **Step 4: Verify GREEN and the full worktree**

```bash
python3 -m pytest -q
python3 -m py_compile $(rg --files src -g '*.py')
git diff --check
```

Expected: all tests pass and checks exit zero.

- [ ] **Step 5: Commit**

```bash
git add manifest.yaml pyproject.toml src tests/test_config_wizard.py tests/test_feature_service.py
git commit -m "feat(media-search): coordinate cancellable search tasks"
```

---

### Task 7: renaming stage reports and verified compensation

**Files (worktree `.worktrees/renaming`):**
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `src/telepiplex_renaming/config_wizard.py`
- Create: `src/telepiplex_renaming/operations.py`
- Modify: `src/telepiplex_renaming/runtime.py`
- Modify: `src/telepiplex_renaming/service.py`
- Test: `tests/test_config_wizard.py`
- Create: `tests/test_operations.py`
- Modify: `tests/test_feature_processor.py`

**Interfaces:**
- Produces: `CancellationToken` backed by `threading.Event`.
- Produces: `CompensationJournal.record_rename(source_path, renamed_path, file_id)`.
- Produces: `mark_irreversible(reason)` and `rollback(storage) -> dict`.
- Propagates: `operation_id` from `download.completed` into `media.organized`.

- [ ] **Step 1: Write failing compensation and stage tests**

Test reverse-order rename restoration with identity checks, refusal after identity changes, partial rollback details, downgrade before delete/copy-plus-delete, event propagation, and config exits.

```python
def test_rollback_refuses_changed_target_identity(self):
    journal.record_rename("/a/old.mkv", "/a/new.mkv", "file-1")
    storage.info["/a/new.mkv"] = {"file_id": "different"}
    result = journal.rollback(storage)
    self.assertEqual(result["state"], "partially_rolled_back")
    self.assertEqual(result["remaining"], ["/a/new.mkv"])
    self.assertEqual(storage.renamed, [])
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m pytest -q tests/test_operations.py tests/test_config_wizard.py tests/test_feature_processor.py
```

Expected: missing operation module and status/control failures.

- [ ] **Step 3: Implement background organization and safe compensation**

Bind runtime to `RenamingFeature`. Claim the durable job, report `metadata`, `planning`, `conflict_check`, `renaming`, `moving`, `cleanup`, and `handoff_plex`, and spawn processing rather than holding event delivery open.

Use an operation-aware storage wrapper that checks cancellation before mutations. Record rename inverses only after stable ID verification. Switch Core control to `cancel` before directory creation with unknown provenance, copy-plus-delete moves, cleanup, or `delete_single_file`. Run valid inverses in reverse order and report `rolled_back` or `partially_rolled_back`.

Update Feature/package version, SDK dependency, and Core range to 1.1.

- [ ] **Step 4: Verify GREEN and the full worktree**

```bash
python3 -m pytest -q
python3 -m py_compile $(rg --files src -g '*.py')
git diff --check
```

Expected: all tests pass and checks exit zero.

- [ ] **Step 5: Commit**

```bash
git add manifest.yaml pyproject.toml src tests
git commit -m "feat(renaming): add cancellable staged organization"
```

---

### Task 8: plex-management task stages and cancellation

**Files (worktree `.worktrees/plex-management`):**
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `src/telepiplex_plex/config_wizard.py`
- Modify: `src/telepiplex_plex/feature.py`
- Modify: `src/telepiplex_plex/management.py`
- Modify: `src/telepiplex_plex/runtime.py`
- Test: `tests/test_config_wizard.py`
- Test: `tests/test_feature_runtime.py`
- Test: `tests/test_plex_management.py`

**Interfaces:**
- Produces: `PlexFeature.operation_control()` and `operation_snapshot()`.
- Consumes: `operation_id` from `media.organized`.
- Extends: `PlexManagementService.run_job(job_id, *, should_cancel=None, on_stage=None)`.

- [ ] **Step 1: Write failing interaction and stage tests**

Assert each open config/AI confirmation/manual-match response has one control, no-argument `/plex` stays terminal, all management stages report, and cancellation stops before the next step without claiming to reverse completed Plex work.

```python
def test_run_job_stops_before_next_step_after_cancel(self):
    stages = []
    result = service.run_job(
        job_id,
        should_cancel=lambda: len(stages) == 1,
        on_stage=stages.append,
    )
    self.assertEqual(stages, ["scanning"])
    self.assertEqual(result["state"], "cancelled")
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m pytest -q tests/test_config_wizard.py tests/test_feature_runtime.py tests/test_plex_management.py
```

Expected: failures for operation controls and cancellable service hooks.

- [ ] **Step 3: Implement task reporting and safe checkpoints**

Bind each Telegram AI/write task and organized-event batch to an `operation_id`. Report `ai_planning`, `scan_preparing`, `scanning`, `locating`, `matching`, `localizing`, `artwork`, and `streams`. Automatic Plex jobs use `cancel` because the pipeline begins with an irreversible scan. Stop only between safe service steps and list effects already accepted by Plex.

Keep no-argument `/plex` as a terminal read. Update Feature/package version, SDK dependency, and Core range to 1.1. Do not add `/scan`.

- [ ] **Step 4: Verify GREEN and the full worktree**

```bash
python3 -m pytest -q
python3 -m py_compile $(rg --files src -g '*.py')
git diff --check
```

Expected: all tests pass and checks exit zero.

- [ ] **Step 5: Commit**

```bash
git add manifest.yaml pyproject.toml src tests
git commit -m "feat(plex): expose cancellable management stages"
```

---

### Task 9: Cross-Feature runtime and artifact verification

**Files (Core worktree):**
- Create: `tests/test_operation_pipeline_e2e.py`
- Modify: `tests/test_feature_builder.py`
- Modify: `README.md`
- Modify: `README_EN.md`

**Interfaces:**
- Consumes: four Feature 1.1.0 artifacts and Core API 1.1.
- Verifies: one operation ID, atomic ownership, persistent gate, current-owner cancellation, and dynamic menu sync.

- [ ] **Step 1: Write the failing cross-Feature test**

Exercise real local RPC and assert:

```python
self.assertEqual(ownership, [
    "media-search", "open115", "renaming", "plex-management",
])
self.assertEqual(coordinator.active(chat_id, user_id).plugin_id, "plex-management")
await cancel_current_operation()
self.assertEqual(plex_control_calls, [{"operation_id": operation_id, "action": "cancel"}])
self.assertEqual(media_search_control_calls, [])
```

- [ ] **Step 2: Run E2E and verify RED**

```bash
python3 -m unittest tests.test_operation_pipeline_e2e -v
```

Expected: failure until the coordinated artifacts are built and installed.

- [ ] **Step 3: Build SDK and Feature artifacts**

```bash
python3 -m build sdk
python3 tools/build_feature.py ../open115 ../open115/dist/open115-1.1.0.tpx
python3 tools/build_feature.py ../media-search ../media-search/dist/media-search-1.1.0.tpx
python3 tools/build_feature.py ../renaming ../renaming/dist/renaming-1.1.0.tpx
python3 tools/build_feature.py ../plex-management ../plex-management/dist/plex-management-1.1.0.tpx
```

Expected: four validated artifacts are created.

- [ ] **Step 4: Update Core documentation**

Document live command discovery, operation controls/status, cancellation semantics, and Core-first upgrade order. Do not document `/mag` or `/scan`.

- [ ] **Step 5: Run fresh full verification**

Core:

```bash
python3 -m unittest discover -s tests -t . -v
python3 -m pytest -q
python3 -m py_compile $(rg --files app sdk/src tools -g '*.py')
python3 -m pip check
git diff --check
```

Each Feature:

```bash
python3 -m pytest -q
python3 -m py_compile $(rg --files src -g '*.py')
python3 -m pip check
git diff --check
```

Expected: every command exits zero with no failures.

- [ ] **Step 6: Commit Core integration**

```bash
git add tests/test_operation_pipeline_e2e.py tests/test_feature_builder.py README.md README_EN.md
git commit -m "test(core): verify coordinated Feature pipeline"
```
