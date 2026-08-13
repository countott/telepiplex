# Feature Install and Config Migration Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce an installable `download 1.0.8` artifact and make configuration migration failures identify safe field paths without exposing values.

**Architecture:** `tools/build_feature.py` validates every active `Requires-Dist` against the packaged wheelhouse before building the `.tpx`. `PluginStore` derives sanitized JSON Schema failure paths, carries them through structured error details, and Telegram lifecycle handlers render only those paths.

**Tech Stack:** Python 3.12, `packaging`, `jsonschema` Draft 2020-12, pytest/unittest, setuptools wheels, telepiplex `.tpx` artifacts.

## Global Constraints

- Product-facing prose uses lowercase `telepiplex`.
- Mac-local work must not run Git, create worktrees, connect this checkout to GitHub, or publish.
- Configuration migration remains fail closed and preserves all operator values.
- Diagnostic output may include field paths but must never include configuration values.
- `download` release identity is `1.0.8`; bundled SDK identity is exactly `1.2.1`.

---

### Task 1: Enforce packaged dependency satisfiability

**Files:**
- Modify: `tests/test_feature_builder.py`
- Modify: `tools/build_feature.py`

**Interfaces:**
- Consumes: wheel `METADATA` parsed by `_wheel_metadata(path: Path)`.
- Produces: `validate_plugin_dependencies(plugin_wheel: Path, wheelhouse: Path) -> None`.

- [ ] **Step 1: Write failing tests**

Add one test where `plugin.whl` requires `telepiplex-plugin-sdk==1.1.0` and wheelhouse contains only SDK `1.2.1`; assert `FeatureBuildError`. Add a matching `1.2.1` case that returns normally.

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_feature_builder.py -k plugin_dependencies
```

Expected: failure because `validate_plugin_dependencies` does not exist.

- [ ] **Step 3: Implement minimal validation**

Read plugin requirements, evaluate active markers, index wheelhouse names and versions, and reject a dependency when no packaged version satisfies its specifier. Call the validator after the wheelhouse is complete and before `build_tpx`.

- [ ] **Step 4: Verify GREEN**

Run the same focused test and the complete `tests/test_feature_builder.py` file; expect all tests to pass.

### Task 2: Add safe schema failure paths

**Files:**
- Modify: `tests/test_plugin_store.py`
- Modify: `tests/test_plugin_manager.py`
- Modify: `app/runtime/plugin_store.py`
- Modify: `app/runtime/plugin_manager.py`

**Interfaces:**
- Produces: `StoreError.details: dict` with `config_error_paths: list[str]`.
- Produces: `PluginOperationError.details` preserving those safe paths.

- [ ] **Step 1: Write failing Store and Manager tests**

Cover a nested type error, a missing required key, and an additional property. Assert literal paths such as `service.timeout`, assert no secret value appears, and assert update failure keeps the old active release/config.

- [ ] **Step 2: Verify RED**

Run the named Store and Manager tests. Expected: missing `details` and missing `config_error_paths` assertions fail.

- [ ] **Step 3: Implement minimal path derivation and propagation**

Use `Draft202012Validator.iter_errors`. Derive paths from `absolute_path`; derive missing required names from `validator_value` minus instance keys; derive additional keys from instance keys minus schema properties. Limit results to 20 paths and 100 characters per path. Never copy `ValidationError.message` or `ValidationError.instance` into errors.

- [ ] **Step 4: Verify GREEN**

Run `tests/test_plugin_store.py` and the focused migration tests in `tests/test_plugin_manager.py`; expect all tests to pass.

### Task 3: Render safe migration diagnostics

**Files:**
- Modify: `tests/test_plugin_handler.py`
- Modify: `app/handlers/plugin_handler.py`

**Interfaces:**
- Consumes: `PluginOperationError.details["config_error_paths"]`.
- Produces: lifecycle error suffix `请检查配置项：path1、path2`.

- [ ] **Step 1: Write failing callback test**

Make `manager.update` raise `PluginOperationError("config_migration_required", ..., {"config_error_paths": ["legacy", "service.timeout"]})`; assert the callback contains both paths and excludes a supplied secret value.

- [ ] **Step 2: Verify RED**

Run the focused callback test. Expected: paths are absent.

- [ ] **Step 3: Implement minimal rendering**

Add one helper that accepts error details, limits and normalizes path strings, and appends the suffix in both command and callback lifecycle error branches.

- [ ] **Step 4: Verify GREEN**

Run `tests/test_plugin_handler.py`; expect all tests to pass.

### Task 4: Release `download 1.0.8` with SDK 1.2.1

**Files:**
- Modify: `features/download/manifest.yaml`
- Modify: `features/download/pyproject.toml`
- Modify: `features/download/src/telepiplex_download.egg-info/PKG-INFO`
- Modify: `features/download/src/telepiplex_download.egg-info/requires.txt`
- Modify: `features/download/README.md`
- Modify: `features/download/tests/test_feature_runtime.py`
- Modify: `tests/test_technical_identity_migration.py`
- Modify: `tests/test_unraid_publish_script.py`
- Modify: `app/115bot.py`
- Modify: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Produces: `download@1.0.8` requiring `telepiplex-plugin-sdk==1.2.1`.
- Produces: Host `v3.4.22-host` carrying the diagnostics implementation.

- [ ] **Step 1: Update release identities and checked-in metadata**

Keep manifest, project metadata, egg-info, README examples and contract tests synchronized. Update publisher fixtures so only genuinely unpublished versions are tagged.

- [ ] **Step 2: Run release contract tests**

Run download source contract, technical identity, publisher, and bot startup tests; expect all tests to pass.

- [ ] **Step 3: Build and inspect the real artifact**

Build `/tmp/download-1.0.8.tpx` with the bundled Python. Verify manifest version `1.0.8`, plugin `Requires-Dist` SDK `1.2.1`, one SDK `1.2.1` wheel, and no SDK 1.1.0 reference.

- [ ] **Step 4: Prove offline installation**

Extract the artifact into a temporary directory, create a temporary venv, and run the same `pip install --no-index --find-links wheelhouse plugin-wheel` shape as Host. Import `telepiplex_download` and `telepiplex_plugin_sdk`; expect exit code 0.

### Task 5: Full local verification and handoff

**Files:**
- Verify all files above.

**Interfaces:**
- Produces: test and artifact evidence for the Syncthing handoff.

- [ ] **Step 1: Run focused Host suites**

Run feature builder, store, manager, handler, technical identity, startup and publisher tests.

- [ ] **Step 2: Run complete download suite**

Run `features/download/tests` from `features/download` with `PYTHONPATH=src:../../sdk/src`.

- [ ] **Step 3: Run complete Host suite**

Run `tests` from the project root with `PYTHONPATH=.:sdk/src`.

- [ ] **Step 4: Check local workspace boundary**

Verify `.git` and `.worktrees` are absent and `.stfolder` exists, without invoking Git.

- [ ] **Step 5: Hand off**

List every changed file and actual verification result. Ask the user to wait for Syncthing `Up to Date / 最新` to `/mnt/user/archives/life hacker/telepiplex`. Stop before Git, tagging or publication.
