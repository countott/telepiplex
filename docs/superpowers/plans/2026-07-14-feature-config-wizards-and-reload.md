# Feature Config Wizards and Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore reliable Feature configuration discovery, give every Feature an independent Telegram wizard, materialize current templates, and make update/reload results reflect the actual running release.

**Architecture:** Telepiplex owns lifecycle transactions, schema validation, private YAML writes, runtime consistency checks, and error reporting. Each Feature owns its wizard copy, allowed fields, input validation, and the opaque nested patch it submits to Telepiplex. download keeps its asynchronous token writer because QR completion occurs after the initiating RPC returns.

**Tech Stack:** Python 3.12, python-telegram-bot, asyncio, JSON Schema, PyYAML, unittest/pytest, Telepiplex Feature RPC SDK.

## Global Constraints

- Keep `main`, `feature/search`, `feature/sync`, `feature/rename`, and `feature/115` independent.
- Do not merge into `main`, push, tag, or publish unless the user separately requests publication.
- Prowlarr is always enabled and its wizard exposes only address and API Key.
- Telegram must not expose timeout, polling, category identifiers, internal thresholds, scoring, or MCP settings.
- Secrets are never echoed or logged; config files remain mode `0600`.
- A successful update/reload response requires store, supervisor, router, manifest, schema, and health state to agree.
- Existing `config.yaml` files are never overwritten by example-template refreshes.

---

## File Structure

### Telepiplex

- `app/handlers/config_handler.py`: Feature configuration discovery, status rendering, and delegation only; remove the generic scalar editor.
- `app/handlers/plugin_handler.py`: apply opaque Feature patches through `PluginManager.configure()` and invalidate stale sessions after update.
- `app/runtime/plugin_manager.py`: active-release consistency assertion and reload-one transaction.
- `app/runtime/plugin_store.py`: materialize `config.yaml.example` from the active release default.
- `app/init.py`: strict config reads that preserve the last good in-memory value on error.
- `app/115bot.py`: layered `/reload`, runtime-safe parameter rebinding, and per-Feature summary.
- `tests/test_config_handler.py`, `tests/test_plugin_handler.py`, `tests/test_plugin_manager.py`, `tests/test_plugin_store.py`, `tests/test_bot_runtime_startup.py`, `tests/test_config_template_contract.py`: regression coverage.

### Feature branches

- `src/<package>/config_wizard.py`: Feature-owned wizard/session/parser and allowed-field map.
- Existing `service.py` or `feature.py`: route unique internal config command, callback, and message input to the wizard.
- Existing `runtime.py`: register config command/callback/message surfaces.
- `config.schema.json`: declare `x-telepiplex-config-command` only; the Telepiplex no longer derives UI fields from arbitrary properties.
- `manifest.yaml`, `pyproject.toml`: register the unique command and bump immutable patch versions.
- Feature tests: exact buttons, allowed fields, opaque patch, cancellation, invalid input, secret masking, and version alignment.

---

### Task 1: Telepiplex discovery, opaque patch application, and update consistency

**Files:**
- Modify: `app/handlers/config_handler.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/runtime/plugin_manager.py`
- Modify: `tests/test_config_handler.py`
- Modify: `tests/test_plugin_handler.py`
- Modify: `tests/test_plugin_manager.py`

**Interfaces:**
- Produces: `PluginManager.config_state(plugin_id: str) -> dict`
- Produces: `PluginManager.assert_active_consistency(release) -> None`
- Consumes Feature result: `{"config_patch": dict, "session": {"state": "close"}, "actions": []}`
- Produces callbacks: `host-config-plugin:<index>` and `host-config-direct:<plugin_id>`

- [ ] **Step 1: Replace generic-form tests with discovery-state tests**

Add tests that exercise the desired public contract:

```python
def test_config_menu_keeps_invalid_feature_visible_with_stable_code():
    manager.states["search"] = {
        "plugin_id": "search", "state": "invalid_config",
        "error_code": "invalid_config", "configurable": False,
    }
    # /config text must contain "search" and "invalid_config".

def test_only_explicit_custom_command_is_configurable():
    state = manager.config_state("download")
    assert state["configurable"] is True
    assert state["command"] == "config"
```

Delete expectations for `discover_config_sections()`, scalar coercion, and Telepiplex-owned `key=value` editing.

- [ ] **Step 2: Add Telepiplex patch and post-update red tests**

```python
async def test_feature_patch_is_merged_and_configured_transactionally():
    result = {
        "actions": [],
        "session": {"state": "close"},
        "config_patch": {"ai": {"model": "new-model"}},
    }
    await handle_feature_result(update, context, route, result)
    manager.configure.assert_awaited_once_with(
        "search",
        {"ai": {"api_key": "kept", "model": "new-model"}},
    )

async def test_update_success_rechecks_store_process_route_and_schema():
    updated = await manager.update(artifact_v2)
    assert manager.store.active("echo").version == updated.version
    assert manager.supervisor.process("echo").release.version == updated.version
    assert manager.router.plugin_route("echo").manifest.version == updated.version
```

Also assert patch failures are sanitized, do not claim success, and close neither the old process nor unrelated sessions.

- [ ] **Step 3: Run the focused Telepiplex tests and verify RED**

Run:

```bash
PYTHONPATH=.:sdk/src python3.12 -m pytest -q \
  tests/test_config_handler.py \
  tests/test_plugin_handler.py \
  tests/test_plugin_manager.py
```

Expected: failures because `config_state`, `config_patch`, direct config callbacks, and consistency assertions do not exist, while generic-editor tests describe obsolete behavior.

- [ ] **Step 4: Implement minimal Telepiplex behavior**

Implement a structural deep merge that never interprets field names:

```python
def merge_nested_patch(current: dict, patch: dict) -> dict:
    result = deepcopy(current)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_nested_patch(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
```

`PluginManager.config_state()` must read schema independently from config so `invalid_config` can still be reported. `config_handler` must render every installed Feature with a stable state, but create buttons only for valid custom commands. `plugin_handler` must detect `config_patch`, merge it into the current config, await `manager.configure()`, clear sessions, and send Telepiplex-owned success/failure copy.

After `install()`, `update()`, `rollback()`, and `configure()`, assert that active record, active process, route manifest/client, readable schema, and version/source identity agree before returning success. Keep the assertion inside the existing rollback transaction.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Step 3 command. Expected: all selected tests pass with no warning output.

- [ ] **Step 6: Commit Telepiplex task**

```bash
git add app/handlers/config_handler.py app/handlers/plugin_handler.py \
  app/runtime/plugin_manager.py tests/test_config_handler.py \
  tests/test_plugin_handler.py tests/test_plugin_manager.py
git commit -m "fix(runtime): make Feature config hot loading observable"
```

### Task 2: Versioned Feature example templates

**Files:**
- Modify: `app/runtime/plugin_store.py`
- Modify: `app/config.yaml.example`
- Modify: `config/config.yaml.example`
- Modify: `tests/test_plugin_store.py`
- Modify: `tests/test_config_template_contract.py`

**Interfaces:**
- Produces runtime file: `/config/plugins/<plugin_id>/config.yaml.example`
- Preserves runtime file: `/config/plugins/<plugin_id>/config.yaml`

- [ ] **Step 1: Write template lifecycle red tests**

```python
def test_activation_materializes_current_example_without_overwriting_live_config():
    first = store.activate(store.stage(artifact("1.0.0", default={"prefix": "one"})))
    write_live_config({"prefix": "custom"})
    second = store.activate(store.stage(artifact("1.1.0", default={"prefix": "two"})))
    assert read("echo/config.yaml") == {"prefix": "custom"}
    assert read("echo/config.yaml.example") == {"prefix": "two"}
```

Assert both Telepiplex examples are byte-identical and explicitly mention `/config/plugins/<plugin_id>/config.yaml.example`.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH=.:sdk/src python3.12 -m pytest -q \
  tests/test_plugin_store.py tests/test_config_template_contract.py
```

Expected: missing Feature example file and missing Telepiplex template explanation.

- [ ] **Step 3: Implement example materialization**

During `PluginStore.commit()`, parse and validate the release default, then atomically write it to `config.yaml.example` on every release commit. Continue creating `config.yaml` only when absent. Use a public example mode without secrets populated and keep the live config at `0600`.

Update both Telepiplex example files with identical concise comments describing the Telepiplex/Feature split.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command, then `cmp -s app/config.yaml.example config/config.yaml.example` and YAML-parse both.

- [ ] **Step 5: Commit Telepiplex task**

```bash
git add app/runtime/plugin_store.py app/config.yaml.example \
  config/config.yaml.example tests/test_plugin_store.py \
  tests/test_config_template_contract.py
git commit -m "fix(runtime): refresh installed Feature config examples"
```

### Task 3: Layered `/reload` with truthful results

**Files:**
- Modify: `app/init.py`
- Modify: `app/115bot.py`
- Modify: `app/runtime/plugin_manager.py`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_plugin_manager.py`

**Interfaces:**
- Produces: `init.read_yaml_config(path=None) -> dict`, raising a stable config error without mutating globals.
- Produces: `PluginManager.reload_config(plugin_id: str) -> PluginOperationResult`
- Produces: `/reload` summary sections `Telepiplex 已应用`, `Feature 已重载`, `失败`, `需要重启容器`.

- [ ] **Step 1: Write strict-read and reload red tests**

```python
def test_invalid_host_yaml_preserves_last_good_config():
    init.bot_config = {"allowed_user": 1}
    config_path.write_text("plugins: [", encoding="utf-8")
    with pytest.raises(init.ConfigLoadError):
        init.read_yaml_config(config_path)
    assert init.bot_config == {"allowed_user": 1}

async def test_reload_restarts_each_enabled_feature_and_reports_partial_failure():
    manager.reload_config = AsyncMock(side_effect=[
        SimpleNamespace(
            plugin_id="download", version="1.0.1", state="active",
            details={"restarted": True},
        ),
        PluginOperationError("invalid_config", "invalid Feature config"),
    ])
    await reload(update, context)
    text = update.effective_message.reply_text.await_args.args[0]
    assert "download" in text and "search" in text
    assert "全部成功" not in text
```

Add a manager test proving `reload_config()` restarts from the current on-disk valid config and restores the old route/process when the shadow is unhealthy.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH=.:sdk/src python3.12 -m pytest -q \
  tests/test_bot_runtime_startup.py tests/test_plugin_manager.py
```

Expected: missing strict reader, manager reload API, and per-Feature summary.

- [ ] **Step 3: Implement layered reload**

`read_yaml_config()` returns a validated mapping without mutation. `/reload` compares old/new Telepiplex values, updates logger/authorization and safe numeric manager/supervisor/dispatcher fields, then calls `manager.reload_config()` for every enabled Feature. Treat `bot_token`, `plugins.root`, `plugins.catalog`, and `plugins.catalog_refresh_interval` changes as restart-required.

`reload_config()` reuses the configure transaction with the current validated on-disk mapping, so a valid manual edit starts a new shadow process and an invalid file yields `invalid_config` without touching the old process.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command and assert no old generic “配置已重新加载” claim remains without a result breakdown.

- [ ] **Step 5: Commit Telepiplex task**

```bash
git add app/init.py app/115bot.py app/runtime/plugin_manager.py \
  tests/test_bot_runtime_startup.py tests/test_plugin_manager.py
git commit -m "fix(runtime): reload Feature configs transactionally"
```

### Task 4: search independent configuration wizard

**Files:**
- Create: `src/telepiplex_search/config_wizard.py`
- Modify: `src/telepiplex_search/service.py`
- Modify: `src/telepiplex_search/runtime.py`
- Modify: `config.schema.json`
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `tests/test_feature_service.py`
- Modify: `tests/test_config_schema_contract.py`

**Interfaces:**
- Unique command: `search_config`
- Existing callback namespace: `search`
- Produces patches only under `search.prowlarr`, `metadata.tvdb`, or `ai`.

- [ ] **Step 1: Write search wizard red tests**

Cover exact menu fields, secret masking, invalid/partial input, cancellation, and the final patch:

```python
async def test_prowlarr_wizard_exposes_only_address_and_key():
    menu = await feature.command({"command": "search_config", **owner})
    assert button_texts(menu) == ["Prowlarr", "TVDB", "AI", "取消"]
    await feature.callback({"payload": "config:prowlarr", **owner})
    result = await feature.message({
        "text": "base_url=http://prowlarr:9696\napi_key=secret", **owner,
    })
    assert result["config_patch"] == {
        "search": {"prowlarr": {
            "base_url": "http://prowlarr:9696", "api_key": "secret",
        }}
    }
    assert "enable" not in repr(result["config_patch"])
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
PYTHONPATH=src:../../sdk/src python3.12 -m pytest -q \
  tests/test_feature_service.py tests/test_config_schema_contract.py
```

- [ ] **Step 3: Implement wizard and immutable version bump**

Implement a Feature-local session keyed by `(chat_id, user_id)`, exact allowed-field maps, boolean parsing for TVDB/AI, and `config_patch` output. Register `search_config` in schema/manifest/runtime. Route config callbacks/messages before search flow. Bump manifest and package from `1.0.0` to `1.0.1`.

- [ ] **Step 4: Run full search tests and verify GREEN**

```bash
PYTHONPATH=src:../../sdk/src python3.12 -m pytest -q
```

- [ ] **Step 5: Commit search task**

```bash
git add src config.schema.json manifest.yaml pyproject.toml tests
git commit -m "feat(config): add media search setup wizard"
```

### Task 5: sync independent configuration wizard

**Files:**
- Create: `src/telepiplex_sync/config_wizard.py`
- Modify: `src/telepiplex_sync/feature.py`
- Modify: `src/telepiplex_sync/runtime.py`
- Modify: `config.schema.json`
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Modify: `tests/test_feature_runtime.py`
- Modify: `tests/test_config_schema_contract.py`

**Interfaces:**
- Unique command: `sync_config`
- Existing callback namespace: `plex`
- Produces patches only under `plex`, `tmdb`, `fanart`, or `ai`.

- [ ] **Step 1: Write Plex wizard red tests**

Assert the menu is exactly `Plex`, `TMDB`, `Fanart.tv`, `AI`, `取消`; Plex accepts only `base_url/token`; provider sections accept only `api_key`; AI accepts only `enabled/api_url/api_key/model`. Assert timeout, scan, tool-round, and MCP keys are rejected as unknown.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
PYTHONPATH=src:../../sdk/src python3.12 -m pytest -q \
  tests/test_feature_runtime.py tests/test_config_schema_contract.py
```

- [ ] **Step 3: Implement wizard and immutable version bump**

Register `sync_config`, delegate callback/message input to the local wizard before job/AI routing, emit an opaque patch, and bump manifest/package from `1.0.0` to `1.0.1`. Do not mutate the current `SyncFeature.service`; Telepiplex restart applies the patch safely.

- [ ] **Step 4: Run full Plex tests and verify GREEN**

```bash
PYTHONPATH=src:../../sdk/src python3.12 -m pytest -q
```

- [ ] **Step 5: Commit Plex task**

```bash
git add src config.schema.json manifest.yaml pyproject.toml tests
git commit -m "feat(config): add Plex management setup wizard"
```

### Task 6: rename independent configuration wizard

**Files:**
- Create: `src/telepiplex_rename/config_wizard.py`
- Modify: `src/telepiplex_rename/service.py`
- Modify: `src/telepiplex_rename/runtime.py`
- Modify: `config.schema.json`
- Modify: `manifest.yaml`
- Modify: `pyproject.toml`
- Create: `tests/test_config_wizard.py`
- Modify: `tests/test_config_schema_contract.py`

**Interfaces:**
- Unique command: `rename_config`
- New callback namespace: `rename`
- Produces patches only under `metadata.tvdb` or `ai`.

- [ ] **Step 1: Write rename wizard red tests**

Assert the menu is exactly `TVDB`, `AI`, `取消`; TVDB accepts only `enable/api_key/subscriber_pin`; AI accepts only `enable/api_url/api_key/model`; storage timeouts, unorganized path, and selection thresholds are rejected.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
PYTHONPATH=src:../../sdk/src python3.12 -m pytest -q \
  tests/test_config_wizard.py tests/test_config_schema_contract.py
```

- [ ] **Step 3: Implement wizard and immutable version bump**

Add config command/callback/message surfaces without changing event handling. Return only opaque patches and bump manifest/package from `1.0.0` to `1.0.1`.

- [ ] **Step 4: Run full rename tests and verify GREEN**

```bash
PYTHONPATH=src:../../sdk/src python3.12 -m pytest -q
```

- [ ] **Step 5: Commit rename task**

```bash
git add src config.schema.json manifest.yaml pyproject.toml tests
git commit -m "feat(config): add rename setup wizard"
```

### Task 7: Cross-branch regression and release-contract verification

**Files:**
- Modify only if a failing cross-contract test identifies a real gap.
- Verify: all files changed by Tasks 1-6.

- [ ] **Step 1: Run Telepiplex full suite**

```bash
PYTHONPATH=.:sdk/src python3.12 -m pytest -q
python3.12 -m compileall -q app sdk tools tests
```

- [ ] **Step 2: Run every Feature full suite**

From each Feature worktree:

```bash
PYTHONPATH=src:../../sdk/src python3.12 -m pytest -q
python3.12 -m compileall -q src tests
```

- [ ] **Step 3: Verify schema/default/version contracts**

For all four Features, parse `config.schema.json`, parse `config.default.yaml`, validate defaults, and confirm manifest/package versions match. Confirm download remains `1.0.1` unless its source changed; confirm the other three are `1.0.1`.

- [ ] **Step 4: Verify diffs and branch scope**

Run in each worktree:

```bash
git status --short --branch
git -c host.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

Expected: only the intended branch-local files differ; no generated build artifacts are tracked; `main` remains untouched.

- [ ] **Step 5: Review the final behavior against the design**

Confirm every requirement in `docs/superpowers/specs/2026-07-14-feature-config-wizards-and-reload-design.md` maps to a passing test. If a gap exists, add a failing test first and repeat RED/GREEN before completion.
