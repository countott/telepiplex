# Feature Visual Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a schema-driven Telegram configuration UI for installed Features, including AI and TVDB, without breaking Feature isolation or exposing secrets.

**Architecture:** `PluginStore` owns validated atomic configuration reads and writes. `PluginManager` owns transactional runtime reload. A Core `ConversationHandler` discovers editable scalar sections from each active Feature schema and applies typed partial patches. Feature schemas provide titles and `writeOnly` metadata.

**Tech Stack:** Python 3.12, python-telegram-bot, JSON Schema 2020-12, PyYAML, unittest/pytest.

## Global Constraints

- Feature configuration remains at `/config/plugins/<plugin_id>/config.yaml`.
- Core must not hardcode Feature-specific AI or TVDB paths.
- Secrets must never be echoed or logged.
- Configuration writes are validated, atomic, and mode `0600`.
- Running Features reload transactionally; a failed reload restores the prior configuration and route.

---

### Task 1: Persistent configuration API

**Files:**
- Modify: `app/core/plugin_store.py`
- Test: `tests/test_plugin_store.py`

**Interfaces:**
- Produces: `PluginStore.config_schema(release) -> dict`
- Produces: `PluginStore.read_config(release) -> dict`
- Produces: `PluginStore.write_config(release, value) -> dict`

- [ ] **Step 1: Write failing tests** proving schema reads return copies, invalid writes preserve the original file, valid writes are atomic, and the resulting mode is `0600`.
- [ ] **Step 2: Run** `python -m pytest -q tests/test_plugin_store.py` and confirm failures are caused by missing methods.
- [ ] **Step 3: Implement** validated YAML read/write methods using a same-directory temporary file, `fsync`, `chmod(0o600)`, and `os.replace`.
- [ ] **Step 4: Re-run** `python -m pytest -q tests/test_plugin_store.py` and confirm all tests pass.

### Task 2: Transactional Feature reload

**Files:**
- Modify: `app/core/plugin_manager.py`
- Test: `tests/test_plugin_manager.py`

**Interfaces:**
- Produces: `PluginManager.config(plugin_id) -> dict`
- Produces: `await PluginManager.configure(plugin_id, value) -> PluginOperationResult`

- [ ] **Step 1: Write failing tests** for disabled Feature writes, healthy shadow reload, drain refusal, and failed-shadow rollback.
- [ ] **Step 2: Run** `python -m pytest -q tests/test_plugin_manager.py` and verify the new tests fail for missing behavior.
- [ ] **Step 3: Implement** full-schema validation, drain, atomic write, shadow start, route commit/promote, stabilization, old-process stop, and rollback/resume on failure.
- [ ] **Step 4: Re-run** `python -m pytest -q tests/test_plugin_manager.py` and confirm all tests pass.

### Task 3: Schema-driven Telegram form

**Files:**
- Create: `app/handlers/config_handler.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `app/115bot.py`
- Create: `tests/test_config_handler.py`
- Modify: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Produces: `discover_config_sections(schema, current) -> list[ConfigSection]`
- Produces: `parse_config_patch(text, section) -> dict`
- Produces: `register_feature_config_handlers(application)`

- [ ] **Step 1: Write failing pure-function and handler tests** for nested section discovery, local `$ref`, typed values, unknown fields, secret masking, authorization, callback indexing, save success, and sanitized failure.
- [ ] **Step 2: Run** `python -m pytest -q tests/test_config_handler.py tests/test_bot_runtime_startup.py` and verify expected failures.
- [ ] **Step 3: Implement** the conversation, add `/config` to Core commands, register it before generic Feature callback/message gateways, and add a “配置 Feature” button to `/plugin`.
- [ ] **Step 4: Re-run** the targeted tests and confirm all pass.

### Task 4: Feature schema metadata

**Files:**
- Modify: `../media-search/config.schema.json`
- Modify: `../renaming/config.schema.json`
- Modify: `../plex-management/config.schema.json`
- Test: each Feature's schema/config contract tests

**Interfaces:**
- Consumes: standard JSON Schema `title`, `description`, `writeOnly`, `properties`, and local `$ref`.
- Produces: editable `metadata.tvdb` and `ai` sections without Core-specific field tables.

- [ ] **Step 1: Write failing schema contract tests** asserting AI/TVDB properties are declared and secret keys are `writeOnly`.
- [ ] **Step 2: Run each targeted Feature test** and confirm the metadata assertions fail.
- [ ] **Step 3: Expand known schema properties** while retaining compatibility through permissive nested `additionalProperties` where old user configuration may contain supported extra keys.
- [ ] **Step 4: Re-run all affected Feature tests** and validate defaults with `Draft202012Validator`.

### Task 5: Release verification and publication

**Files:**
- Modify: `docs/todos/2026-07-12-business-module-decisions.md` only if the visual-config status needs an explicit completion note.

- [ ] **Step 1: Run full Core and four Feature test suites**, compile tracked Python, and run `git diff --check` in every worktree.
- [ ] **Step 2: Build and verify all four `.tpx` artifacts** using Core's release tooling.
- [ ] **Step 3: Commit each changed module independently** with module-scoped messages.
- [ ] **Step 4: Push** `feature/115`, `feature/media-search`, `feature/renaming`, `feature/plex-management`, and `feature/telepiplex-core`.
- [ ] **Step 5: Create and push** immutable tag `platform-v1.0.0` from the verified Core commit.
- [ ] **Step 6: Monitor the GitHub Actions run** through completion and verify the GitHub Release, catalog, artifacts, and Core image metadata.
