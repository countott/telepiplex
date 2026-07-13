# open115 Custom Visual Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Telegram `/config → open115 → Access/Refresh Token` with sequential token entry and immediate runtime activation while retaining independent scan authorization.

**Architecture:** Core recognizes a generic `x-telepiplex-config-command` JSON Schema annotation, validates it against the active Feature manifest, dispatches it through the current route, and reuses the existing Feature action/session gateway. The open115 Feature owns the two-step secret input state machine, atomic private-config persistence, in-memory client activation, scan flow, and secret expiry.

**Tech Stack:** Python 3.12, python-telegram-bot 22.3, JSON Schema 2020-12 annotations, asyncio, PyYAML, unittest/pytest.

## Global Constraints

- Feature configuration remains at `/config/plugins/open115/config.yaml` with mode `0600`.
- Core must not contain open115 token field names or open115-specific conditionals.
- Access/Refresh token values must not appear in Telegram output, logs, callback data, exception text, or durable Core state.
- The open115 Access token may exist only in process memory while waiting for Refresh token and must expire after 30 minutes.
- Scan authorization and automatic token refresh remain independent and keep `auth_mode: scan`.
- `feature/telepiplex-core` and `feature/115` remain independent branches; do not modify other Feature branches or push remote refs.

---

### Task 1: Add the generic Core custom-config handoff

**Files:**
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/handlers/config_handler.py`
- Modify: `tests/test_config_handler.py`
- Test: `tests/test_plugin_handler.py`

**Interfaces:**
- Consumes: active `PluginRoute` objects from `router.plugin_route(plugin_id)` and the schema annotation `x-telepiplex-config-command`.
- Produces: `custom_config_command(schema, route) -> str | None` and public `handle_feature_result(update, context, route, result) -> None`.

- [ ] **Step 1: Write failing Core tests**

Add a custom-only Feature view and route fixture, then assert `/config` lists it and selecting it dispatches `command.dispatch`, renders its keyboard, and creates the normal Feature session:

```python
async def test_custom_config_feature_is_listed_and_handed_to_feature_session(self):
    manager = FakeManager()
    manager.views["open115"] = {
        "plugin_id": "open115",
        "version": "1.0.0",
        "schema": {
            "type": "object",
            "x-telepiplex-config-command": "config",
            "properties": {"access_token": {"type": "string"}},
        },
        "config": {"access_token": "secret"},
    }
    manager.doctor = Mock(return_value=[
        {"plugin_id": "media-search", "state": "healthy"},
        {"plugin_id": "open115", "state": "healthy"},
    ])
    client = AsyncMock()
    client.request.return_value = {
        "actions": [{
            "kind": "send_message",
            "text": "请选择授权方式",
            "data": {"keyboard": [[{
                "text": "Access / Refresh Token",
                "callback_data": "open115:auth:direct",
            }]]},
        }],
        "session": {"state": "open"},
    }
    manifest = SimpleNamespace(
        commands=(SimpleNamespace(name="config"),),
        callbacks=("open115",),
    )
    route = SimpleNamespace(plugin_id="open115", client=client, manifest=manifest)
    router = Mock()
    router.plugin_route.side_effect = lambda plugin_id: route if plugin_id == "open115" else None

    update, context, _ = self.request(text="/config")
    context.application.bot_data["telepiplex_plugin_router"] = router
    with patch("app.handlers.config_handler.init.check_user", return_value=True):
        await config_command(update, context)
    self.assertIn("open115", update.effective_message.reply_text.await_args.args[0])

    index = context.user_data["core_config_plugins"].index("open115")
    update.callback_query.data = f"core-config-plugin:{index}"
    with patch("app.handlers.config_handler.init.check_user", return_value=True):
        state = await select_config_plugin(update, context)

    self.assertEqual(state, ConversationHandler.END)
    request = client.request.await_args.args
    self.assertEqual(request[0], "command.dispatch")
    self.assertEqual(request[1]["command"], "config")
    self.assertEqual(
        context.application.bot_data["telepiplex_plugin_sessions"][(10, 1)]["plugin_id"],
        "open115",
    )
```

Add a second test where the schema names `config` but the active manifest does not declare it. Assert the Feature is omitted when it has no generic nested sections, no RPC request is issued, and no secret value is rendered. Add a third test where the RPC raises `RuntimeError("token=secret-value")`; assert the reply contains `custom_config_failed` and excludes `secret-value`.

- [ ] **Step 2: Run the Core red tests**

Run:

```bash
python3 -m unittest tests.test_config_handler tests.test_plugin_handler -v
```

Expected: FAIL because custom root annotations are ignored and `_handle_feature_result` is private to `plugin_handler.py`.

- [ ] **Step 3: Expose the existing result handler without changing behavior**

Rename `_handle_feature_result` to `handle_feature_result` in `app/handlers/plugin_handler.py` and update the command, callback, and message gateways:

```python
async def handle_feature_result(update, context, route, result: dict):
    if not await _render_actions(update, context, route, result):
        return
    session = result.get("session") if isinstance(result, dict) else None
    if session is None:
        return
    if not isinstance(session, dict) or session.get("state") not in {"open", "close"}:
        await update.effective_message.reply_text("❌ Feature 返回了无效会话状态。")
        return
    key = _session_key(update)
    if session["state"] == "open":
        sessions = context.application.bot_data.setdefault(SESSION_KEY, {})
        sessions[key] = {
            "plugin_id": route.plugin_id,
            "expires_at": time.time() + SESSION_TTL_SECONDS,
        }
    else:
        _drop_session(context.application.bot_data, key)
```

- [ ] **Step 4: Implement generic custom-config discovery and dispatch**

In `app/handlers/config_handler.py`, import `ROUTER_KEY` and `handle_feature_result`. Add:

```python
_CUSTOM_CONFIG_COMMAND = "x-telepiplex-config-command"


def custom_config_command(schema: dict, route) -> str | None:
    command = str((schema or {}).get(_CUSTOM_CONFIG_COMMAND) or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", command):
        return None
    declared = {
        str(item.name)
        for item in getattr(getattr(route, "manifest", None), "commands", ())
    }
    return command if command in declared else None
```

When building the Feature list, obtain the current route and include a Feature when either `discover_config_sections(...)` is non-empty or `custom_config_command(...)` returns a command. In `select_config_plugin`, check the custom command before the generic sections. Dispatch with:

```python
result = await route.client.request(
    "command.dispatch",
    {
        "command": command,
        "args": [],
        "text": "/config",
        "user_id": update.effective_user.id,
        "chat_id": update.effective_chat.id,
        "update_id": getattr(update, "update_id", None),
    },
    deadline=30,
    idempotency_key=f"telegram:{getattr(update, 'update_id', '')}:config",
)
await handle_feature_result(update, context, route, result)
_clear_session(context.user_data)
return ConversationHandler.END
```

Return a sanitized `custom_config_failed` reply and `ConversationHandler.END` if route lookup or RPC dispatch fails. Do not expose exception details.

- [ ] **Step 5: Run Core tests and commit**

Run:

```bash
python3 -m unittest tests.test_config_handler tests.test_plugin_handler -v
python3 -m py_compile app/handlers/config_handler.py app/handlers/plugin_handler.py
```

Expected: all tests PASS and compilation exits 0.

Commit:

```bash
git add app/handlers/config_handler.py app/handlers/plugin_handler.py tests/test_config_handler.py tests/test_plugin_handler.py
git commit -m "feat(core): delegate custom Feature configuration"
```

---

### Task 2: Restore the open115 two-step token wizard

**Files:**
- Modify: `config.schema.json`
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `src/telepiplex_open115/service.py`
- Modify: `tests/test_feature_runtime.py`

**Interfaces:**
- Consumes: `FeatureConfigStore.write_tokens(access_token, refresh_token, auth_mode="direct")`, `client.set_tokens(...)`, Feature callback namespace `open115`, and message routing keyed by `(chat_id, user_id)`.
- Produces: `config` and `auth` commands that open the same authorization menu, sequential message states `access_token` and `refresh_token`, and `x-telepiplex-config-command: config`.

- [ ] **Step 1: Replace the old direct-token test with failing wizard tests**

In `tests/test_feature_runtime.py`, assert both commands open the same menu and the direct route collects secrets in order:

```python
async def test_config_and_auth_offer_token_entry_and_scan_routes(self):
    for command in ("config", "auth"):
        response = await self.feature.command({
            "command": command,
            "user_id": 1,
            "chat_id": 10,
        })
        self.assertEqual(response["session"]["state"], "open")
        callbacks = [
            button["callback_data"]
            for row in response["actions"][0]["data"]["keyboard"]
            for button in row
        ]
        self.assertEqual(callbacks, [
            "open115:auth:direct",
            "open115:auth:scan",
        ])

async def test_direct_token_wizard_writes_only_after_refresh_and_activates_client(self):
    await self.feature.command({"command": "config", "user_id": 1, "chat_id": 10})
    direct = await self.feature.callback({
        "payload": "auth:direct", "user_id": 1, "chat_id": 10,
    })
    self.assertIn("Access token", direct["actions"][0]["text"])

    access = await self.feature.message({
        "text": "access-new", "user_id": 1, "chat_id": 10,
    })
    self.assertIn("Refresh token", access["actions"][0]["text"])
    self.assertEqual(self.feature.config_store.writes, [])

    completed = await self.feature.message({
        "text": "refresh-new", "user_id": 1, "chat_id": 10,
    })
    self.assertEqual(completed["session"]["state"], "close")
    self.assertEqual(self.feature.config_store.writes, [
        ("access-new", "refresh-new", "direct"),
    ])
    self.assertEqual(self.client.tokens, ("access-new", "refresh-new"))
    self.assertNotIn("access-new", str(completed))
    self.assertNotIn("refresh-new", str(completed))
```

Add tests for empty/placeholder/multiline input staying at the current stage, `/q` clearing the pending Access token without writing, write failure preserving the previous client tokens, and expiry clearing the pending Access token. Patch `SESSION_TTL_SECONDS` to `0`, yield once with `await asyncio.sleep(0)`, and assert the session is gone and no write occurred.

Add a source contract test:

```python
schema = yaml.safe_load((ROOT / "config.schema.json").read_text())
self.assertEqual(schema["x-telepiplex-config-command"], "config")
manifest = yaml.safe_load((ROOT / "manifest.yaml").read_text())
self.assertIn("config", [item["name"] for item in manifest["commands"]])
```

Also parse `pyproject.toml` with `tomllib` and assert both its project version and the manifest version are `1.0.1`; immutable `.tpx` artifacts require a new semantic version for this fix to be installable.

- [ ] **Step 2: Run the open115 red tests**

Run:

```bash
python3 -m unittest tests.test_feature_runtime.Open115FeatureTest tests.test_feature_runtime.FeatureSourceContractTest -v
```

Expected: FAIL because `config` only returns a path, direct auth reads preconfigured tokens, and messages always report an expired session.

- [ ] **Step 3: Declare the custom config command**

Add the annotation beside the root schema type:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "x-telepiplex-config-command": "config"
}
```

Update the manifest description to `配置 115 授权` while retaining the existing `config` command declaration.
Set `manifest.yaml` and `pyproject.toml` to version `1.0.1`, and update the README build output example to `dist/open115-1.0.1.tpx`.

- [ ] **Step 4: Implement the Feature-owned session state machine**

In `service.py`, add:

```python
SESSION_TTL_SECONDS = 30 * 60


def _single_secret(value: str) -> str:
    value = str(value or "").strip().strip("`").strip('"').strip("'")
    if not value or "\n" in value or value.lower().startswith("your_"):
        raise ValueError("invalid secret")
    return value
```

Add `self.session_expiry_handles = {}`. Implement `_start_auth_session(request)` to clear any old session for the same key, store `{"stage": "choose_mode"}`, and return Token/scan buttons with `session: open`. Make both `config` and `auth` call it.

Change `auth:direct` to set `{"stage": "access_token"}` and ask for Access token. In `message(...)`:

```python
if stage == "access_token":
    access_token = _single_secret(request.get("text"))
    self.sessions[key] = {"stage": "refresh_token", "access_token": access_token}
    self._schedule_sensitive_expiry(key)
    return self._message_with_session("已收到 Access token。\n请发送 Refresh token。", "open")

if stage == "refresh_token":
    refresh_token = _single_secret(request.get("text"))
    access_token = session["access_token"]
    updated = self.config_store.write_tokens(
        access_token, refresh_token, auth_mode="direct"
    )
    self.config.update(updated)
    self.client.set_tokens(access_token, refresh_token)
    self._clear_auth_session(key)
    return self._message_with_session(
        "✅ 115 Token 已写入并立即生效。", "close"
    )
```

Use `asyncio.get_running_loop().call_later(...)` rather than `runtime.spawn(...)` for expiry so a pending input timer does not count as active Feature work and block drain/update. The callback must remove only the matching pending session. `_clear_auth_session` cancels and removes the timer handle and deletes the session. `/q`, scan selection, completion, and replacement sessions call it.

Catch input validation errors without echoing values and keep the current stage. Catch persistence errors with a generic failure message, keep the prior client tokens, and leave the user at the Refresh stage until retry or expiry.

- [ ] **Step 5: Preserve scan behavior and run open115 tests**

Keep `_start_scan_auth` and `_complete_scan_auth` behavior, but clear the menu session before starting scan. Run:

```bash
python3 -m unittest tests.test_feature_runtime -v
python3 -m py_compile src/telepiplex_open115/service.py src/telepiplex_open115/runtime.py src/telepiplex_open115/config_store.py
```

Expected: all tests PASS and compilation exits 0.

Commit:

```bash
git add config.schema.json manifest.yaml pyproject.toml README.md src/telepiplex_open115/service.py tests/test_feature_runtime.py
git commit -m "fix(open115): restore visual token configuration"
```

---

### Task 3: Cross-branch verification

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Consumes: the Core custom-config schema contract and the open115 schema declaration.
- Produces: evidence that both independent branches are clean, testable, and compatible.

- [ ] **Step 1: Run complete Core verification**

From `feature/telepiplex-core`:

```bash
python3 -m unittest discover -s tests -t .
python3 -m py_compile $(git ls-files '*.py')
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check origin/feature/telepiplex-core...HEAD
git status --short --branch
```

Expected: all tests PASS, compilation and whitespace checks exit 0, and only local commits are ahead of the remote branch.

- [ ] **Step 2: Run complete open115 verification**

From `feature/115`:

```bash
python3 -m unittest discover -s tests -t .
python3 -m py_compile $(git ls-files '*.py')
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check origin/feature/115...HEAD
git status --short --branch
```

Expected: all tests PASS, compilation and whitespace checks exit 0, and only the local implementation commit is ahead of the remote branch.

- [ ] **Step 3: Confirm publication boundary**

Do not push. Report both commit hashes, test counts, and the fact that remote branches remain unchanged.
