# Default Composable Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployable runtime load stable Telepiplex modules by default and expose Telegram module status without requiring manual module toggles.

**Architecture:** `main` is the deployable composed runtime. Feature branches remain development boundaries, while the running bot loads stable modules by default when module configuration is omitted. `/modules` reports active/default module state and reminds operators that code/config changes take effect after restart.

**Tech Stack:** Python 3, python-telegram-bot handlers, existing in-repo module registry, YAML runtime config.

## Global Constraints

- Do not modify `main` until the integration branch is verified.
- Default stable modules are `app.modules.open115`, `app.modules.media_search`, and `app.modules.renaming`.
- `/modules` is a status command, not a runtime enable/disable switch.
- Runtime module changes are restart-effective, not hot-loaded by `/reload`.

---

### Task 1: Default Stable Module Loading

**Files:**
- Modify: `app/115bot.py`
- Test: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Consumes: `get_enabled_module_names(config=None) -> list[str]`
- Produces: default module resolution used by `build_module_registry()`

- [ ] **Step 1: Write the failing test**

```python
def test_default_enabled_modules_load_all_stable_modules(self):
    bot_module = load_bot_module()
    self.assertEqual(
        bot_module.get_enabled_module_names({}),
        ["app.modules.open115", "app.modules.media_search", "app.modules.renaming"],
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_bot_runtime_startup.BotRuntimeStartupTest.test_default_enabled_modules_load_all_stable_modules`
Expected: FAIL because omitted `modules.enabled` currently returns `[]`.

- [ ] **Step 3: Write minimal implementation**

Add `DEFAULT_ENABLED_MODULES` and update `get_enabled_module_names()` so omitted config, omitted `modules`, `enabled: all`, and empty `enabled` resolve to the stable module list. Keep `modules.disabled` as an optional exclusion list.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_bot_runtime_startup.BotRuntimeStartupTest.test_default_enabled_modules_load_all_stable_modules`
Expected: PASS.

### Task 2: Telegram Module Status Command

**Files:**
- Modify: `app/115bot.py`
- Test: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Consumes: `application.bot_data["telepiplex_registry"]`
- Produces: `/modules` command and `build_modules_status_text(config=None, registry=None) -> str`

- [ ] **Step 1: Write the failing test**

```python
def test_modules_status_text_reports_default_modules_and_restart_boundary(self):
    bot_module = load_bot_module()
    text = bot_module.build_modules_status_text({})
    self.assertIn("115 下载", text)
    self.assertIn("媒体搜索", text)
    self.assertIn("下载后重命名", text)
    self.assertIn("重启容器后生效", text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_bot_runtime_startup.BotRuntimeStartupTest.test_modules_status_text_reports_default_modules_and_restart_boundary`
Expected: FAIL because `build_modules_status_text` does not exist.

- [ ] **Step 3: Write minimal implementation**

Add `MODULE_CATALOG`, `build_modules_status_text`, async `/modules` handler, core menu entry, and command registration in `main()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_bot_runtime_startup.BotRuntimeStartupTest.test_modules_status_text_reports_default_modules_and_restart_boundary`
Expected: PASS.

### Task 3: Config Templates And Verification

**Files:**
- Modify: `config/config.yaml.example`
- Modify: `app/config.yaml.example`
- Test: existing bot runtime and composable integration tests

**Interfaces:**
- Consumes: `/config/config.yaml`
- Produces: documented `modules.enabled: all` default with optional `disabled: []`

- [ ] **Step 1: Update templates**

Add:

```yaml
modules:
  enabled: all
  disabled: []
```

- [ ] **Step 2: Run verification**

Run:

```bash
python3 -m unittest tests/test_bot_runtime_startup.py tests/test_composable_integration.py tests/test_composable_core.py
python3 -m py_compile $(git ls-files '*.py')
git diff --check
```

Expected: all pass.

### Task 4: Main Backup And Publish

**Files:**
- Git refs only.

**Interfaces:**
- Produces: `archive/2026-07-10/main-pre-composable`
- Produces: deployable `main` containing the composed runtime

- [ ] **Step 1: Request code review before merging to main**

Review the integration diff against the pre-change commit.

- [ ] **Step 2: Backup current main**

Run:

```bash
git fetch origin
git branch archive/2026-07-10/main-pre-composable origin/main
git push origin archive/2026-07-10/main-pre-composable
```

- [ ] **Step 3: Merge or fast-forward the composed runtime into main**

Use the verified integration branch as the source and push `main`.
