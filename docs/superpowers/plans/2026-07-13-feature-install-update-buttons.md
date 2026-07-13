# Feature Install and Update Buttons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/plugin` recover from the missing legacy default catalog and expose newest-compatible install and update actions as Telegram buttons.

**Architecture:** Resolve the default catalog source once while constructing the Core manager, preserving explicit custom local catalogs. Build the `/plugin` view from `available_plugins()` and `available_updates()` and reuse the existing exact-reference callbacks and manager transactions.

**Tech Stack:** Python 3.12, python-telegram-bot 22.3, unittest/pytest, YAML-backed Feature catalog.

## Global Constraints

- Keep `open115` naming, internal plugin IDs, `.tpx` manifests, catalog keys, capability dependencies, and persisted installation paths unchanged.
- A missing `<plugins.root>/catalog.yaml` legacy default falls back to `https://github.com/countott/telepiplex/releases/latest/download/catalog.yaml`; an existing file or a different explicit local path remains local.
- Install and update buttons contain exact catalog-selected `plugin_id@version` references and remain explicit authorization points.
- Normal latest-version installation and updates require only `/plugin` plus a button click.
- Exact-reference commands remain only as advanced offline/pinned fallbacks.
- Secret values and raw exception details must not enter Telegram messages or logs.

---

### Task 1: Catalog source migration compatibility

**Files:**
- Modify: `app/115bot.py`
- Modify: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Produces: `DEFAULT_PLUGIN_CATALOG_URL: str` and `resolve_plugin_catalog_source(plugin_config: dict, root: Path) -> str`.
- Consumes: the existing `plugins.root` and optional `plugins.catalog` configuration.

- [ ] **Step 1: Write failing source-selection tests**

Add tests proving that an omitted catalog and a missing exact legacy `<root>/catalog.yaml` select the official URL, while an existing legacy file and a different explicit missing path remain local.

```python
source = bot_module.resolve_plugin_catalog_source({}, root / "plugins")
self.assertEqual(source, bot_module.DEFAULT_PLUGIN_CATALOG_URL)

legacy = root / "plugins/catalog.yaml"
source = bot_module.resolve_plugin_catalog_source({"catalog": str(legacy)}, root / "plugins")
self.assertEqual(source, bot_module.DEFAULT_PLUGIN_CATALOG_URL)
```

- [ ] **Step 2: Run the source-selection tests and verify RED**

```bash
PYTHONPATH=sdk/src "$PY" -m unittest \
  tests.test_bot_runtime_startup.BotPluginRuntimeStartupTest.test_missing_legacy_catalog_uses_official_release_catalog \
  tests.test_bot_runtime_startup.BotPluginRuntimeStartupTest.test_existing_or_custom_local_catalog_is_preserved
```

Expected: fail because `resolve_plugin_catalog_source` does not exist.

- [ ] **Step 3: Implement source resolution and use it in manager construction**

```python
DEFAULT_PLUGIN_CATALOG_URL = (
    "https://github.com/countott/telepiplex/releases/latest/download/catalog.yaml"
)


def resolve_plugin_catalog_source(plugin_config: dict, root: Path) -> str:
    configured = str((plugin_config or {}).get("catalog") or "").strip()
    legacy = Path(root) / "catalog.yaml"
    if not configured:
        return DEFAULT_PLUGIN_CATALOG_URL
    source = Path(configured).expanduser()
    if source.resolve(strict=False) == legacy.expanduser().resolve(strict=False):
        return configured if source.is_file() else DEFAULT_PLUGIN_CATALOG_URL
    return configured
```

- [ ] **Step 4: Run the source-selection tests and verify GREEN**

Run the Step 2 command. Expected: `OK`.

---

### Task 2: Install and update actions in `/plugin`

**Files:**
- Modify: `app/handlers/plugin_handler.py`
- Modify: `tests/test_plugin_handler.py`

**Interfaces:**
- Consumes: `manager.doctor()`, `await manager.available_plugins()`, and `await manager.available_updates()`.
- Produces: install callback `core-plugin-install:confirm:<reference>` and update callback `core-plugin-update:confirm:<reference>`.

- [ ] **Step 1: Write failing update-button and partial-failure tests**

Extend the fake manager with `available_updates()`. Add an installed
`open115@1.0.0` status and an update candidate targeting `1.1.0`, then assert:

```python
self.assertIn("可更新", message)
self.assertIn("open115 1.0.0 → 1.1.0", message)
self.assertEqual(
    update_button.callback_data,
    "core-plugin-update:confirm:open115@1.1.0",
)
```

Retain the install-button assertion for `open115@1.0.0`. Add one test where update discovery fails but install candidates still produce buttons, and one where install discovery fails but update candidates remain clickable.

- [ ] **Step 2: Run handler tests and verify RED**

```bash
PYTHONPATH=sdk/src "$PY" -m unittest tests.test_plugin_handler
```

Expected: update-button tests fail because `_show_feature_overview` does not query `available_updates()`.

- [ ] **Step 3: Implement isolated discovery and button rendering**

In `_show_feature_overview`, query updates only when `doctor()` returns installed statuses. Query install candidates independently. Keep successful results if the other query raises, collect only the safe error code, and render:

```python
callback_data = f"core-plugin-update:confirm:{item.reference}"
rows.append([InlineKeyboardButton(
    f"更新 {item.plugin_id} 到 {item.target_version}",
    callback_data=callback_data,
)])
```

Keep the existing callback byte-length check and install-button dependency rules.

- [ ] **Step 4: Run handler tests and verify GREEN**

Run the Step 2 command. Expected: all handler tests pass.

---

### Task 3: Safe catalog diagnostics

**Files:**
- Modify: `app/core/plugin_update_monitor.py`
- Modify: `tests/test_plugin_update_monitor.py`

**Interfaces:**
- Consumes: exceptions with optional stable `code` attribute.
- Produces: warning text containing the code or exception class name, never the raw exception message.

- [ ] **Step 1: Write a failing stable-code logging test**

Make `manager.available_updates()` raise `CatalogError("catalog_unavailable", "token=secret")`. Assert the captured warning includes `catalog_unavailable`, excludes `CatalogError`, and excludes `secret`.

- [ ] **Step 2: Run the monitor tests and verify RED**

```bash
PYTHONPATH=sdk/src "$PY" -m unittest tests.test_plugin_update_monitor
```

Expected: the warning contains `CatalogError` instead of `catalog_unavailable`.

- [ ] **Step 3: Implement safe code selection**

```python
error_code = str(getattr(exc, "code", type(exc).__name__))[:100]
self._warn(
    "Feature 更新目录检查失败；本轮已跳过，Core 将继续运行："
    f"{error_code}"
)
```

- [ ] **Step 4: Run monitor tests and verify GREEN**

Run the Step 2 command. Expected: all monitor tests pass.

---

### Task 4: Operator documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/todos/2026-07-12-business-module-decisions.md`
- Modify: `tests/test_deployment_contract.py`

**Interfaces:**
- Documents: `/plugin` as the normal click-only latest install/update flow.
- Documents: exact `name@version` and `.tpx` commands as advanced offline/pinned fallbacks.

- [ ] **Step 1: Update documentation contract assertions and verify RED**

Require both READMEs to describe install and update buttons, the legacy missing-catalog fallback, and the advanced exact-reference fallback.

```bash
PYTHONPATH=sdk/src "$PY" -m unittest tests.test_deployment_contract
```

Expected: new documentation assertions fail.

- [ ] **Step 2: Update README and decision status**

Document that `/plugin` lists install and update buttons bound to the newest compatible release. Move command examples under an advanced/offline heading and record the migration behavior in OPS-TODO-02.

- [ ] **Step 3: Run complete verification**

```bash
PYTHONPATH=sdk/src "$PY" -m unittest discover -s tests -t .
PYTHONPATH=sdk/src "$PY" -m pytest -q
PYTHONPATH=sdk/src "$PY" -m compileall -q app sdk tools tests
"$PY" -m pip check
git diff --check
```

Expected: all tests pass, compilation is silent, pip reports no broken requirements, and diff check is silent.

- [ ] **Step 4: Review and commit the implementation**

```bash
git add app tests README.md README_EN.md docs
git commit -m "feat(core): add Feature install and update buttons"
```
