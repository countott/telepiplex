# Stale Session Close Race Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a newer `awaiting_input` operation active when an older response for the same operation closes only the Feature text session.

**Architecture:** Preserve the coordinator as the operation-state authority. Add one Host-side predicate that recognizes a stale operation snapshot by matching `operation_id` and comparing its revision with the active record, then prevent that stale result from rendering or triggering implicit session-close cancellation. Render the newest coordinator record instead.

**Tech Stack:** Python 3.12, `unittest`/`pytest`, telepiplex Core Host interaction coordinator.

## Global Constraints

- Product-facing text must use lowercase `telepiplex`.
- `session.close` must continue to drop the Feature free-text session.
- Explicit close results without an operation must continue to release waiting operations.
- Feature protocol, callback payloads, database schema, and Search 1.5.1 remain unchanged.
- Do not run Git or create `.git`/`.worktrees` on the Mac.
- Do not publish from the Mac.

---

### Task 1: Protect newer waiting operations from stale close responses

**Files:**
- Modify: `tests/test_plugin_handler.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `app/115bot.py`

**Interfaces:**
- Consumes: a validated Feature operation mapping and the active `OperationRecord`.
- Produces: `_is_stale_operation_snapshot(operation, active) -> bool`.
- Preserves: `handle_feature_result(update, context, route, result)` and all external protocol shapes.

- [ ] **Step 1: Write the failing regression test**

Add this asynchronous case beside
`test_closing_session_releases_awaiting_operation`:

```python
async def test_stale_closing_result_keeps_newer_awaiting_operation(self):
    coordinator.report("search", {
        "operation_id": "op-race",
        "chat_id": 10,
        "user_id": 1,
        "state": "awaiting_input",
        "stage": "candidate_recovery",
        "status_text": "请选择重试或退出",
        "control": "exit",
        "revision": 2,
    })

    await handle_feature_result(update, context, route, {
        "actions": [{"kind": "send_message", "text": "正在规划媒体证据"}],
        "session": {"state": "close"},
        "operation": {
            "operation_id": "op-race",
            "chat_id": 10,
            "user_id": 1,
            "state": "running",
            "stage": "planning",
            "status_text": "正在规划媒体证据",
            "control": "cancel",
            "revision": 1,
        },
    })

    active = coordinator.active(10, 1)
    self.assertIsNotNone(active)
    self.assertEqual(active.state, "awaiting_input")
    self.assertEqual(active.revision, 2)
    update.effective_message.reply_text.assert_not_awaited()
```

- [ ] **Step 2: Verify the regression test is RED**

Run:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_plugin_handler.py \
  -k 'stale_closing_result_keeps_newer_awaiting_operation'
```

Expected: the active record is absent because the existing close branch
changes it to `cancelled`.

- [ ] **Step 3: Implement the minimal stale-snapshot guard**

Add:

```python
def _is_stale_operation_snapshot(operation, active) -> bool:
    if not isinstance(operation, dict) or active is None:
        return False
    if str(operation.get("operation_id") or "") != active.operation_id:
        return False
    try:
        revision = int(operation.get("revision"))
    except (TypeError, ValueError):
        return False
    return revision < active.revision
```

Require this predicate to be false before the `session.close` branch reports
the active operation as cancelled. When the predicate is true immediately
after `coordinator.report(...)`, skip `_render_actions(...)` and call
`render_operation(...)` for the newest stored record instead.

- [ ] **Step 4: Verify GREEN and preserve explicit close**

Run:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_plugin_handler.py \
  -k 'stale_closing_result_keeps_newer_awaiting_operation or closing_session_releases_awaiting_operation'
```

Expected: `2 passed`.

- [ ] **Step 5: Run complete local verification**

First update
`test_core_runtime_version_is_v3_4_11_host` to
`test_core_runtime_version_is_v3_4_12_host`, make it expect
`v3.4.12-host`, and run it to verify RED:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_bot_runtime_startup.py \
  -k core_runtime_version_is_v3_4_12_host
```

Then change the literal returned by `get_version()` in `app/115bot.py` from
`v3.4.11-host` to `v3.4.12-host` and rerun the same test. Expected: `1 passed`.

After that, run complete local verification.

Run the complete Core suite and each Feature suite:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

Expected: every suite exits successfully and all three workspace-boundary
checks pass.

- [ ] **Step 6: Record the local handoff**

List the two production/test files and the design/plan records, report actual
test counts, and ask the user to wait for Syncthing to show
`Up to Date / 最新`. Do not commit, tag, push, or publish.
