# Cancel and Exit Interaction Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove redundant terminal controls and make `退出`, `取消任务`, and `取消并回滚` mean the same thing everywhere in telepiplex.

**Architecture:** Keep every existing callback payload and operation action stable. Correct built-in Feature keyboards and completion copy at their source, then add a Core rendering guard that removes duplicate terminal controls sharing one callback while preserving intentional duplicate navigation destinations.

**Tech Stack:** Python 3.12, `unittest`/`pytest`, python-telegram-bot inline keyboards, telepiplex Feature runtime contracts.

## Global Constraints

- Product-facing text must use lowercase `telepiplex`.
- `退出` closes input, configuration, browsing, or pre-execution selection.
- `取消任务` stops a running operation or durable pending job.
- `取消并回滚` stops work and restores contractually reversible effects.
- Technical callback payloads and `cancel`/`exit`/`rollback` operation actions do not change.
- Search evidence, candidate, metadata, Prowlarr, and download business behavior is out of scope.
- Do not run Git or create `.git`/`.worktrees` on the Mac.
- Do not modify generated `build/` copies; local tests import from `src/`.

---

### Task 1: Core terminal-control rendering and Feature configuration exit

**Files:**
- Modify: `tests/test_interaction_handler.py`
- Modify: `tests/test_plugin_handler.py`
- Modify: `tests/test_config_handler.py`
- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/handlers/config_handler.py`

**Interfaces:**
- Produces: `deduplicate_terminal_controls(rows)`, which accepts rows of `InlineKeyboardButton` objects and returns rows with duplicate terminal controls removed.
- Preserves: non-terminal buttons even when two navigation labels intentionally share one callback.

- [ ] **Step 1: Write failing Core tests**

Add tests that express the approved contract:

```python
def test_terminal_control_dedup_keeps_navigation_duplicates(self):
    rows = [[
        InlineKeyboardButton("上一项", callback_data="search:browse:p1:1"),
        InlineKeyboardButton("下一项", callback_data="search:browse:p1:1"),
    ], [
        InlineKeyboardButton("取消", callback_data="search:cancel:p1"),
        InlineKeyboardButton("退出", callback_data="search:cancel:p1"),
    ]]
    result = deduplicate_terminal_controls(rows)
    assert [button.text for button in result[0]] == ["上一项", "下一项"]
    assert [button.text for button in result[1]] == ["取消"]
```

Add a direct Feature action markup test in `tests/test_plugin_handler.py` using
`_keyboard_markup(...)` and the same terminal-control pair. Assert that only
one terminal callback is rendered.

Add an async test in `tests/test_config_handler.py` that:

```python
await config_command(update, context)
button = update.effective_message.reply_text.await_args.kwargs[
    "reply_markup"
].inline_keyboard[-1][0]
self.assertEqual(button.text, "退出")
await quit_config_conversation(update, context)
self.assertEqual(
    update.callback_query.edit_message_text.await_args.args[0],
    "已退出 Feature 配置。",
)
```

- [ ] **Step 2: Run the Core tests and verify RED**

Run:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_interaction_handler.py \
  tests/test_plugin_handler.py \
  tests/test_config_handler.py
```

Expected: failures show duplicate terminal controls remain and the Core
configuration chooser still renders/reports `取消`.

- [ ] **Step 3: Implement the Core guard and exit copy**

In `app/handlers/interaction_handler.py`, define:

```python
TERMINAL_CONTROL_LABELS = frozenset({
    "退出", "取消", "取消任务", "取消并回滚",
})

def deduplicate_terminal_controls(rows):
    seen = set()
    result = []
    for row in rows:
        kept = []
        for button in row:
            callback_data = str(button.callback_data or "")
            if button.text in TERMINAL_CONTROL_LABELS:
                if callback_data in seen:
                    continue
                seen.add(callback_data)
            kept.append(button)
        if kept:
            result.append(kept)
    return result
```

Apply it in `operation_markup(...)` after Feature status rows are built. Import
and apply it in `app/handlers/plugin_handler.py::_keyboard_markup(...)` before
constructing `InlineKeyboardMarkup`.

In `app/handlers/config_handler.py`, change the chooser button to `退出` and
both completion paths to `已退出 Feature 配置。`. Keep
`host-config-cancel` unchanged.

- [ ] **Step 4: Run the Core tests and verify GREEN**

Run the Step 2 command again.

Expected: all selected Core tests pass with no warnings or errors.

---

### Task 2: download interaction exit copy

**Files:**
- Modify: `features/download/tests/test_feature_runtime.py`
- Modify: `features/download/src/telepiplex_download/service.py`

**Interfaces:**
- Preserves: `/q`, `download:exit`, operation terminal state, and all callback payloads.
- Produces: consistent `已退出当前交互。` user copy for explicit interaction exit.

- [ ] **Step 1: Write failing download tests**

Extend the existing `/q` and explicit-exit tests:

```python
response = await self.feature.command({
    "command": "q", "user_id": 1, "chat_id": 10,
})
self.assertEqual(
    response["actions"][0]["text"],
    "已退出当前交互。",
)
```

In the token-write failure test, assert the retry message contains
`使用 /q 退出` and does not contain `使用 /q 取消`.

- [ ] **Step 2: Run the download tests and verify RED**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/download
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_feature_runtime.py
```

Expected: failures show `/q` returns `已取消。` and the write-failure prompts
still say `/q 取消`.

- [ ] **Step 3: Implement download copy changes**

In `features/download/src/telepiplex_download/service.py`:

- change `/q` action text from `已取消。` to `已退出当前交互。`;
- change both retry prompts from `使用 /q 取消` to `使用 /q 退出`;
- leave QR authorization, token write rollback, download submission, and other
  active-task cancellation copy unchanged.

- [ ] **Step 4: Run the download tests and verify GREEN**

Run the Step 2 command again.

Expected: the download Feature test file passes.

---

### Task 3: search duplicate controls and configuration exit copy

**Files:**
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/tests/test_config_wizard.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/config_wizard.py`

**Interfaces:**
- Preserves: `search:cancel:<plan_id>` and `search:config:cancel`.
- Produces: one `退出` control for recoverable planning and Prowlarr recovery.
- Does not change: source orchestration, candidate construction, metadata gates, or release search.

- [ ] **Step 1: Write failing search tests**

Replace the recoverable-planning assertions with:

```python
self.assertEqual(
    keyboard,
    [[{
        "text": "重试",
        "callback_data": f"search:retry:{plan_id}",
    }], [{
        "text": "退出",
        "callback_data": f"search:cancel:{plan_id}",
    }]],
)
```

Extend `test_prowlarr_failure_keeps_plan_and_offers_retry_exit` to assert its
keyboard has exactly two buttons, with one terminal control:

```python
buttons = [button for row in keyboard for button in row]
self.assertEqual(
    [button["text"] for button in buttons],
    ["重试 Prowlarr", "退出"],
)
self.assertEqual(
    len({button["callback_data"] for button in buttons}),
    2,
)
```

Extend `test_cancel_at_confirmation_does_not_submit_patch` in
`test_config_wizard.py`:

```python
self.assertEqual(
    cancelled["actions"][0]["text"],
    "已退出 search 配置。",
)
```

- [ ] **Step 2: Run the search tests and verify RED**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/search
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_feature_service.py tests/test_config_wizard.py
```

Expected: failures show both duplicate search terminal buttons and the old
configuration cancellation copy.

- [ ] **Step 3: Implement search UI changes**

In the two failure keyboards in
`features/search/src/telepiplex_search/service.py`, replace:

```python
[{"text": "取消", ...}, {"text": "退出", ...}]
```

with one:

```python
[{"text": "退出", "callback_data": f"search:cancel:{plan_id}"}]
```

In `features/search/src/telepiplex_search/config_wizard.py`, change
`已取消 search 配置。` to `已退出 search 配置。`.

- [ ] **Step 4: Run the search tests and verify GREEN**

Run the Step 2 command again.

Expected: both selected search test files pass.

---

### Task 4: rename and sync configuration exit copy

**Files:**
- Modify: `features/rename/tests/test_config_wizard.py`
- Modify: `features/rename/src/telepiplex_rename/config_wizard.py`
- Modify: `features/sync/tests/test_config_wizard.py`
- Modify: `features/sync/src/telepiplex_sync/config_wizard.py`

**Interfaces:**
- Preserves: `rename:config:cancel` and `plex:config:cancel`.
- Produces: `已退出 rename 配置。` and `已退出 sync 配置。`.

- [ ] **Step 1: Write failing configuration-wizard tests**

For both Feature test files, open the wizard and send `config:cancel`, then
assert:

```python
self.assertEqual(result["session"]["state"], "close")
self.assertEqual(
    result["actions"][0]["text"],
    "已退出 rename 配置。",  # use sync in the sync test
)
self.assertNotIn("config_patch", result)
```

- [ ] **Step 2: Run the Feature tests and verify RED**

Run:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
(
  cd /Users/young/Documents/telepiplex/features/rename
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
    "$PY" -m pytest -q -p no:cacheprovider tests/test_config_wizard.py
)
(
  cd /Users/young/Documents/telepiplex/features/sync
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
    "$PY" -m pytest -q -p no:cacheprovider tests/test_config_wizard.py
)
```

Expected: each test reports the previous `已取消 ... 配置。` copy.

- [ ] **Step 3: Implement configuration exit copy**

Change only the cancellation-result message in:

- `features/rename/src/telepiplex_rename/config_wizard.py`;
- `features/sync/src/telepiplex_sync/config_wizard.py`.

Keep callback payloads and config patch behavior unchanged.

- [ ] **Step 4: Run the Feature tests and verify GREEN**

Run the Step 2 command again.

Expected: both configuration-wizard test files pass.

---

### Task 5: sync pre-execution exit and durable-job cancellation labels

**Files:**
- Modify: `features/sync/tests/test_feature_runtime.py`
- Modify: `features/sync/src/telepiplex_sync/feature.py`

**Interfaces:**
- Preserves: `plex:scan:cancel`, `plex:cancel`, job state transitions, and non-rollback disclosures.
- Produces: `退出` for manual scan library selection and `取消任务` for an existing durable Plex job.

- [ ] **Step 1: Write failing sync interaction tests**

Update manual scan menu assertions to:

```python
self.assertIn(
    {"text": "退出", "callback_data": "plex:scan:cancel"},
    keyboard[-1],
)
self.assertEqual(
    cancelled["actions"][0]["text"],
    "已退出 Plex 扫描选择。",
)
```

Update all existing durable-job selection assertions to:

```python
self.assertIn(
    {"text": "取消任务", "callback_data": "plex:cancel"},
    action["data"]["keyboard"][-1],
)
```

Keep the existing assertions that `plex:cancel` persists the job as
`cancelled`, stops later steps, and discloses that accepted Plex effects are not
rolled back.

- [ ] **Step 2: Run the sync runtime tests and verify RED**

Run:

```bash
cd /Users/young/Documents/telepiplex/features/sync
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_feature_runtime.py
```

Expected: label and exit-copy assertions fail while job-state assertions
remain green.

- [ ] **Step 3: Implement sync labels**

In `features/sync/src/telepiplex_sync/feature.py`:

- change manual scan selector `取消` to `退出`;
- change `已取消 Plex 扫描选择。` to `已退出 Plex 扫描选择。`;
- change both existing-job `plex:cancel` button labels from `取消` to
  `取消任务`;
- leave callback payloads and cancellation state handling unchanged.

- [ ] **Step 4: Run the sync runtime tests and verify GREEN**

Run the Step 2 command again.

Expected: the sync runtime test file passes.

---

### Task 6: Cross-project verification and local handoff

**Files:**
- Verify: all files changed in Tasks 1-5
- Verify: `docs/superpowers/specs/2026-07-28-cancel-exit-interaction-semantics-design.md`
- Verify: `docs/superpowers/plans/2026-07-28-cancel-exit-interaction-semantics.md`

**Interfaces:**
- Confirms: source and test behavior matches the approved semantics.
- Confirms: no search business-pipeline code was changed.

- [ ] **Step 1: Scan production source for unresolved ambiguous controls**

Run:

```bash
cd /Users/young/Documents/telepiplex
rg -n -C 2 '"text": "取消"|InlineKeyboardButton\\("取消"|已取消 .*配置|/q 取消' \
  app features -g '*.py' -g '!**/build/**' -g '!**/tests/**'
```

Expected: only true active-task cancellation or rollback messages remain; no
duplicate search terminal pair, config-exit message, or pre-scan `取消` remains.

- [ ] **Step 2: Run the full local test contract**

Run:

```bash
cd /Users/young/Documents/telepiplex
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
```

Expected: all repository and five Feature suites pass.

- [ ] **Step 3: Verify Mac workspace invariants**

Run:

```bash
cd /Users/young/Documents/telepiplex
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

Expected: exit code 0.

- [ ] **Step 4: Prepare the local handoff**

List every added and modified file, its purpose, every command actually run,
and the observed result. Remind the user to wait for Syncthing to show
`Up to Date / 最新`. Do not publish or run Git.
