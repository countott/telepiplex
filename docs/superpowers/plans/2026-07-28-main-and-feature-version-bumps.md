# Main and Feature Version Bumps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increment the telepiplex Host and every Feature except the freshly released `search` Feature by one patch version so changed artifact bytes receive new immutable release identities.

**Architecture:** Keep the Host display version in `app/115bot.py` synchronized with its focused test. For each affected Feature, keep `manifest.yaml`, `pyproject.toml`, checked-in package metadata, current-version README references, and version-contract tests synchronized; leave `search` at `1.1.0`.

**Tech Stack:** Python 3.12, YAML Feature manifests, TOML package metadata, `unittest`/`pytest`.

## Global Constraints

- Product-facing text must use lowercase `telepiplex`.
- Mac-local work must not invoke Git, create Git metadata, or publish.
- Increment only patch versions: Host `v3.4.7-host` to `v3.4.8-host`, `download` `1.0.3` to `1.0.4`, `rename` `1.0.2` to `1.0.3`, `sync` `1.0.0` to `1.0.1`, and `caption` `0.1.0` to `0.1.1`.
- Keep `search` unchanged at `1.1.0`.
- Historical test fixtures and historical release descriptions remain unchanged unless they assert the current source identity.

---

### Task 1: Lock the expected Host and Feature identities

**Files:**
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_technical_identity_migration.py`
- Modify: `features/download/tests/test_feature_runtime.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `features/sync/tests/test_feature_runtime.py`

**Interfaces:**
- Consumes: Existing Host `get_version()` and each Feature's manifest, project metadata, and README build example.
- Produces: Focused assertions for the five new identities while preserving `search@1.1.0`.

- [ ] **Step 1: Update current-version assertions to the requested patch versions**

Change only assertions and fixtures that model the current checked-in Host or Feature identity. Do not rewrite generic catalog, publish-script, or historical-version fixtures.

- [ ] **Step 2: Run the focused tests and confirm they fail against old source metadata**

Run:

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_bot_runtime_startup.py::BotPluginRuntimeStartupTest::test_core_runtime_version_is_v3_4_8_host \
  tests/test_technical_identity_migration.py::test_features_use_only_the_new_technical_identities \
  features/download/tests/test_feature_runtime.py \
  features/rename/tests/test_feature_processor.py \
  features/sync/tests/test_feature_runtime.py
```

Expected: failures identify the still-old Host/Feature version strings.

### Task 2: Apply the new immutable release identities

**Files:**
- Modify: `app/115bot.py`
- Modify: `features/download/manifest.yaml`
- Modify: `features/download/pyproject.toml`
- Modify: `features/download/src/telepiplex_download.egg-info/PKG-INFO`
- Modify: `features/download/README.md`
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/pyproject.toml`
- Modify: `features/rename/src/telepiplex_rename.egg-info/PKG-INFO`
- Modify: `features/rename/README.md`
- Modify: `features/sync/manifest.yaml`
- Modify: `features/sync/pyproject.toml`
- Modify: `features/sync/README.md`
- Modify: `features/caption/manifest.yaml`
- Modify: `features/caption/pyproject.toml`
- Modify: `features/caption/README.md`

**Interfaces:**
- Consumes: New version expectations from Task 1.
- Produces: Synchronized Host and Feature source identities suitable for later user-controlled publication from Unraid.

- [ ] **Step 1: Update Host and Feature source metadata**

Apply the exact patch-version mapping from Global Constraints to the Host version literal and affected Feature manifests/projects. Preserve `search@1.1.0`.

- [ ] **Step 2: Synchronize current-version generated metadata and README references**

Update checked-in `PKG-INFO` version headers and current build commands. Preserve historical migration text where `1.0.0` describes the release that introduced the migration.

- [ ] **Step 3: Run the focused tests and confirm they pass**

Run the focused command from Task 1 and expect all selected tests to pass.

### Task 3: Verify the complete local contract

**Files:**
- Verify: All files listed in Tasks 1 and 2.

**Interfaces:**
- Consumes: Synchronized version sources and tests.
- Produces: Evidence that only the requested Host/Feature identities changed and the local workspace still satisfies the no-Git/Syncthing boundary.

- [ ] **Step 1: Audit exact current version references**

Use focused `rg` checks to confirm new versions are present in authoritative/current files, `search` remains `1.1.0`, and old versions remain only in intentional historical fixtures or prose.

- [ ] **Step 2: Run the full Host and five-Feature test suites**

Run the AGENTS.md full local suite with bytecode and pytest caches disabled.

- [ ] **Step 3: Verify workspace boundary markers**

Run:

```bash
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

Expected: all three checks pass.

- [ ] **Step 4: Hand off through Syncthing**

List every modified/created file and actual validation result, then stop and ask the user to wait for Syncthing `Up to Date / 最新` before any Unraid-side publication.
