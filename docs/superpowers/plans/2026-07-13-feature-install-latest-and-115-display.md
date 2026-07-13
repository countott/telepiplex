# Feature Latest Install and 115 Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/plugin` recover from the missing legacy default catalog, install the newest compatible Feature by button or bare name, and display `open115` as `115` without changing its internal identity.

**Architecture:** Resolve the default catalog source once while constructing the Core manager, preserving explicit custom local catalogs. Keep internal plugin IDs at all manager, catalog, callback, and persistence boundaries; a small identity helper normalizes the `115` alias and formats user-facing labels.

**Tech Stack:** Python 3.12, python-telegram-bot 22.3, unittest/pytest, YAML-backed Feature catalog.

## Global Constraints

- Keep internal `plugin_id=open115`, `.tpx` manifests, catalog keys, capability dependencies, and persisted installation paths unchanged.
- A missing `<plugins.root>/catalog.yaml` legacy default falls back to `https://github.com/countott/telepiplex/releases/latest/download/catalog.yaml`; an existing file or a different explicit local path remains local.
- The install button remains the explicit authorization point and must contain an exact digest-pinned `open115@<version>` reference.
- Bare-name install uses the same latest stable, Core-compatible catalog candidate as `/plugin`.
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

Run:

```bash
PYTHONPATH=sdk/src "$PY" -m unittest \
  tests.test_bot_runtime_startup.BotPluginRuntimeStartupTest.test_missing_legacy_catalog_uses_official_release_catalog \
  tests.test_bot_runtime_startup.BotPluginRuntimeStartupTest.test_existing_or_custom_local_catalog_is_preserved
```

Expected: both tests fail because `resolve_plugin_catalog_source` does not exist.

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

Construct `PluginCatalog` with this function's result.

- [ ] **Step 4: Run the source-selection tests and verify GREEN**

Run the Step 2 command. Expected: `OK`.

---

### Task 2: Stable identity alias and user-facing 115 name

**Files:**
- Create: `app/core/plugin_identity.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/handlers/config_handler.py`
- Modify: `app/115bot.py`
- Modify: `tests/test_plugin_handler.py`
- Modify: `tests/test_config_handler.py`
- Modify: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Produces: `internal_plugin_id(value: str) -> str`, `display_plugin_id(value: str) -> str`, and `internal_plugin_reference(value: str) -> str`.
- Preserves: all callback data, manager calls, catalog references, and config session values use `open115`.

- [ ] **Step 1: Write failing identity and UI tests**

Add tests with these assertions:

```python
self.assertEqual(display_plugin_id("open115"), "115")
self.assertEqual(internal_plugin_id("115"), "open115")
self.assertEqual(internal_plugin_reference("115@1.0.0"), "open115@1.0.0")
self.assertIn("安装 115 1.0.0", buttons[0][0].text)
self.assertNotIn("open115", message)
self.assertEqual(buttons[0][0].callback_data,
                 "core-plugin-install:confirm:open115@1.0.0")
```

Verify `/config` buttons and prompts display `115` while `manager.config` and `manager.configure` receive `open115`.

- [ ] **Step 2: Run the identity/UI tests and verify RED**

Run:

```bash
PYTHONPATH=sdk/src "$PY" -m unittest \
  tests.test_plugin_handler \
  tests.test_config_handler \
  tests.test_bot_runtime_startup
```

Expected: new assertions fail because the UI still prints `open115` and no alias helper exists.

- [ ] **Step 3: Implement the identity helper**

```python
_ALIASES = {"115": "open115"}
_DISPLAY_NAMES = {"open115": "115"}


def internal_plugin_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return _ALIASES.get(normalized, normalized)


def display_plugin_id(value: str) -> str:
    internal = internal_plugin_id(value)
    return _DISPLAY_NAMES.get(internal, internal)


def internal_plugin_reference(value: str) -> str:
    raw = str(value or "").strip()
    plugin_id, separator, version = raw.partition("@")
    if separator:
        return f"{internal_plugin_id(plugin_id)}@{version}"
    return internal_plugin_id(raw)
```

- [ ] **Step 4: Apply display names only at presentation boundaries**

Use `display_plugin_id` for Telegram text and button labels. Use
`internal_plugin_id`/`internal_plugin_reference` before lifecycle/status manager calls. Do not transform callback data created from catalog candidate references.

- [ ] **Step 5: Run the identity/UI tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass.

---

### Task 3: Bare-name latest install and actionable catalog errors

**Files:**
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/core/plugin_update_monitor.py`
- Modify: `tests/test_plugin_handler.py`
- Modify: `tests/test_plugin_update_monitor.py`

**Interfaces:**
- Produces: `_resolve_install_reference(manager, raw: str) -> str`.
- Consumes: `manager.available_plugins()` candidates whose exact `reference` is already selected by catalog compatibility/version rules.

- [ ] **Step 1: Write failing bare-install tests**

Cover both aliases, a generic Feature, and a blocked dependency:

```python
manager.candidates = [candidate(
    plugin_id="open115", reference="open115@1.0.0", ready=True,
)]
await plugin_command(update_for("install", "115"), context)
self.assertIn(("install", "open115@1.0.0"), manager.calls)
```

Also assert `open115` resolves the same reference, `media-search` selects its candidate, and a blocked candidate produces `missing_dependency` without calling `manager.install`.

- [ ] **Step 2: Run plugin handler tests and verify RED**

Run:

```bash
PYTHONPATH=sdk/src "$PY" -m unittest tests.test_plugin_handler
```

Expected: manager receives the raw bare name or returns `invalid_reference` instead of the candidate reference.

- [ ] **Step 3: Implement a single candidate-based resolver**

```python
async def _resolve_install_reference(manager, raw: str) -> str:
    value = internal_plugin_reference(raw)
    if "@" in value or Path(value).expanduser().is_file():
        return value
    candidates = await manager.available_plugins()
    candidate = next((item for item in candidates if item.plugin_id == value), None)
    if candidate is None:
        raise PluginOperationError("release_not_found", ...)
    if not candidate.ready:
        raise PluginOperationError("missing_dependency", ...)
    return candidate.reference
```

Call this only for `install`; retain exact-reference behavior for update. Convert catalog exceptions to their stable safe code at the handler boundary.

- [ ] **Step 4: Improve update monitor diagnostics safely**

Log `getattr(exc, "code", type(exc).__name__)` so the warning identifies
`catalog_unavailable` or `catalog_download_failed` without logging exception text.

- [ ] **Step 5: Run handler and monitor tests and verify GREEN**

Run:

```bash
PYTHONPATH=sdk/src "$PY" -m unittest \
  tests.test_plugin_handler tests.test_plugin_update_monitor
```

Expected: all selected tests pass.

---

### Task 4: Operator documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/todos/2026-07-12-business-module-decisions.md`
- Test: `tests/test_deployment_contract.py`

**Interfaces:**
- Documents: `115` as the display name and `open115` as the stable internal identifier.
- Documents: `/plugin install 115` selects the latest compatible release and exact `name@version` remains the pinned/offline path.

- [ ] **Step 1: Update documentation contract assertions, then verify RED**

Require the Chinese and English README to state the bare-name latest install behavior, 115 display/internal-ID distinction, and legacy missing-catalog fallback.

Run:

```bash
PYTHONPATH=sdk/src "$PY" -m unittest tests.test_deployment_contract
```

Expected: the new documentation assertions fail.

- [ ] **Step 2: Update README and decision status**

Show `/plugin install 115` as the latest-compatible shortcut and keep
`/plugin install open115@1.0.0` as a pinned internal-reference example. Record the runtime migration behavior in the first-install TODO.

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

Inspect the staged diff for scope, secret leakage, accidental internal-ID changes, and callback-length regressions. Commit with:

```bash
git add app tests README.md README_EN.md docs
git commit -m "feat(core): install latest features from Telegram"
```
