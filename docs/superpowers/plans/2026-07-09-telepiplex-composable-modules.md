# Telepiplex Composable Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `feature/telepiplex-core`, `feature/115`, `feature/media-search`, and `feature/renaming` as lightweight composable module branches.

**Architecture:** `feature/telepiplex-core` provides a small in-repo module registry, stable download contracts, Telegram lifecycle, and post-download pipeline. Business feature branches are additive branches from core and contribute module files, handlers, providers, and focused tests without rewriting core startup.

**Tech Stack:** Python 3, python-telegram-bot, unittest, Git feature branches, existing Telepiplex 115/Prowlarr/TVDB/AI utilities.

## Global Constraints

- `main` remains unchanged during this extraction round.
- `feature/115`, `feature/media-search`, and `feature/renaming` are based on `feature/telepiplex-core`, not on `main`.
- Core must not contain 115 delivery, Prowlarr search, or renaming business logic.
- Media search submits `DownloadRequest` through the core dispatcher and must not import `app.handlers.download_handler`.
- 115 delivery emits `DownloadCompletedEvent` and runs the core post-download pipeline.
- Renaming is a post-download processor extracted from current `main` behavior.
- Public Plex scan routing uses `category_folder[].plex_library_id`, not `media.plex.library_id`.
- Verification for every branch includes targeted `unittest`, `python3 -m py_compile $(git ls-files '*.py')`, and `git diff --check`.

---

### Task 1: Core Runtime And Module Contracts

**Files:**
- Modify: `app/115bot.py`
- Modify: `app/init.py`
- Modify: `app/utils/directory_config.py`
- Create: `app/core/module_registry.py`
- Create: `app/core/module_loader.py`
- Create: `tests/test_composable_core.py`
- Create: `config/modules/core.yaml.example`

**Interfaces:**
- Produces: `DownloadRequest`, `DownloadCompletedEvent`, `PostDownloadResult`, `ModuleRegistry`, `load_enabled_modules(registry, module_names)`.
- Consumes: existing `init.bot_config`, Telegram `Application`, existing message queue runtime.

- [ ] **Step 1: Write failing core tests**

```python
def test_registry_orders_commands_and_processors():
    registry = ModuleRegistry()
    registry.add_commands([("search", "搜索片源")])
    registry.add_post_download_processor(lambda event: PostDownloadResult(False), priority=200, name="late")
    registry.add_post_download_processor(lambda event: PostDownloadResult(False), priority=100, name="early")
    assert [command.command for command in registry.bot_commands()] == ["search"]
    assert [item.name for item in registry.post_download_processors] == ["early", "late"]
```

Run: `python3 -m unittest tests/test_composable_core.py`
Expected: FAIL because `app.core.module_registry` does not exist.

- [ ] **Step 2: Implement core contract types and registry**

Create frozen or simple dataclasses for:

```python
DownloadRequest(link, selected_path, user_id, naming_metadata=None, metadata=None, source="")
DownloadCompletedEvent(link, selected_path, user_id, final_path, resource_name, naming_metadata=None, metadata=None, provider="115", storage=None)
PostDownloadResult(handled, final_path=None, message=None, should_stop=False, metadata=None)
```

Implement `ModuleRegistry` with `add_commands`, `add_handlers`, `add_startup_hook`, `add_config_sections`, `set_download_provider`, `set_storage_provider`, `add_post_download_processor`, `dispatch_download`, and `run_post_download_pipeline`.

- [ ] **Step 3: Refactor `app/115bot.py` to load modules through registry**

Keep `/start` and `/reload` in core. Aggregate bot menu commands from core plus `registry.bot_commands()`. Store registry on `application.bot_data["telepiplex_registry"]`.

- [ ] **Step 4: Verify core**

Run:

```bash
python3 -m unittest tests/test_telepiplex_core_surface.py tests/test_composable_core.py
python3 -m py_compile $(git ls-files '*.py')
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit core branch**

```bash
git add app/115bot.py app/init.py app/utils/directory_config.py app/core/module_registry.py app/core/module_loader.py tests/test_composable_core.py config/modules/core.yaml.example docs/superpowers
git commit -m "Create composable Telepiplex core"
```

---

### Task 2: 115 Module Branch

**Files:**
- Create: `app/modules/open115.py`
- Create/modify: `app/core/open_115.py`
- Create/modify: `app/handlers/auth_handler.py`
- Create/modify: `app/handlers/config_handler.py`
- Create/modify: `app/handlers/download_handler.py`
- Create/modify: `app/utils/sqlitelib.py`
- Create: `config/modules/115.yaml.example`
- Create: `tests/test_composable_115.py`

**Interfaces:**
- Consumes: `ModuleRegistry`, `DownloadRequest`, `DownloadCompletedEvent`, `PostDownloadResult`.
- Produces: download provider, storage provider, `/auth`, `/config`, `/magnet`, `/m` handlers.

- [ ] **Step 1: Write failing 115 module tests**

```python
def test_open115_module_registers_provider_and_commands():
    registry = ModuleRegistry()
    register_module(registry)
    commands = [command.command for command in registry.bot_commands()]
    assert commands == ["auth", "config", "magnet", "m", "q"]
    assert registry.download_provider is not None
    assert registry.storage_provider is not None
```

Run: `python3 -m unittest tests/test_composable_115.py`
Expected: FAIL because `app.modules.open115` does not exist.

- [ ] **Step 2: Add 115 files from current working behavior**

Extract 115 auth, config, OpenAPI, direct magnet, retry, and offline delivery from current `feature/115` and current `main`.

- [ ] **Step 3: Change delivery to consume `DownloadRequest`**

`download_task` must accept either `DownloadRequest` or explicit parameters only as a compatibility shim. The provider path creates `DownloadCompletedEvent` after raw download completion and calls `registry.run_post_download_pipeline(event)`.

- [ ] **Step 4: Keep fallback unorganized behavior when no processor handles event**

If `run_post_download_pipeline` returns no terminal result, move the raw download result to `media.unorganized_path` and send a user-facing notification.

- [ ] **Step 5: Verify 115**

Run:

```bash
python3 -m unittest tests/test_feature_115_surface.py tests/test_open_115_startup.py tests/test_directory_config.py tests/test_composable_115.py
python3 -m py_compile $(git ls-files '*.py')
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit 115 branch**

```bash
git add app config tests docs/superpowers
git commit -m "Create composable 115 module"
```

---

### Task 3: Media Search Module Branch

**Files:**
- Create: `app/modules/media_search.py`
- Create/modify: `app/handlers/search_handler.py`
- Create/modify: `app/adapters/prowlarr.py`
- Create/modify: `app/adapters/tvdb.py`
- Create/modify: `app/utils/ai.py`
- Create/modify: `app/utils/media_metadata.py`
- Create/modify: `app/utils/release_score.py`
- Create/modify: `app/utils/search_query.py`
- Create/modify: `app/utils/search_resolution.py`
- Create: `config/modules/media-search.yaml.example`
- Create/modify: `tests/test_media_search_surface.py`
- Create/modify: `tests/test_media_search_utils.py`

**Interfaces:**
- Consumes: `ModuleRegistry`, `DownloadRequest`, core download dispatcher.
- Produces: `/search`, `/s`, search candidate confirmation, metadata-preserving download requests.

- [ ] **Step 1: Write failing search handoff test**

```python
def test_search_handler_uses_core_download_request_contract():
    source = Path("app/handlers/search_handler.py").read_text()
    assert "from app.core.module_registry import DownloadRequest" in source
    assert "app.handlers.download_handler" not in source
```

Run: `python3 -m unittest tests/test_media_search_surface.py`
Expected: FAIL until search handoff stops importing `download_handler`.

- [ ] **Step 2: Extract search files**

Copy the working search adapter, scoring, metadata, and resolution utilities from current `feature/media-search`.

- [ ] **Step 3: Replace concrete download handoff**

Search confirmation builds `DownloadRequest(link, selected_path, user_id, naming_metadata=naming_metadata, metadata=metadata, source="media-search")` and submits it through the registry stored in `context.application.bot_data`.

- [ ] **Step 4: Register media search module**

`app/modules/media_search.py::register_module` adds `/search`, `/s`, config section names, and `register_search_handlers`.

- [ ] **Step 5: Verify media search**

Run:

```bash
python3 -m unittest tests/test_media_search_surface.py tests/test_media_search_utils.py
python3 -m py_compile $(git ls-files '*.py')
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit media search branch**

```bash
git add app config tests docs/superpowers
git commit -m "Create composable media search module"
```

---

### Task 4: Renaming Module Branch

**Files:**
- Create: `app/modules/renaming.py`
- Create/modify: `app/adapters/tvdb.py`
- Create/modify: `app/utils/ai.py`
- Create/modify: `app/utils/media_naming.py`
- Create/modify: `app/utils/tvdb_rename.py`
- Create: `config/modules/renaming.yaml.example`
- Create/modify: `tests/test_media_auto_rename.py`
- Create/modify: `tests/test_tvdb_rename.py`
- Create: `tests/test_composable_renaming.py`

**Interfaces:**
- Consumes: `DownloadCompletedEvent`, storage provider operations, `naming_metadata`, `metadata`.
- Produces: post-download processors `renaming.tvdb_episode` and `renaming.generic_media`.

- [ ] **Step 1: Write failing renaming processor test**

```python
def test_renaming_module_registers_post_download_processors():
    registry = ModuleRegistry()
    register_module(registry)
    names = [item.name for item in registry.post_download_processors]
    assert names == ["renaming.tvdb_episode", "renaming.generic_media"]
```

Run: `python3 -m unittest tests/test_composable_renaming.py`
Expected: FAIL because `app.modules.renaming` does not exist.

- [ ] **Step 2: Extract naming planners**

Copy `media_naming.py` and `tvdb_rename.py` from current `main`.

- [ ] **Step 3: Extract post-download rename operations**

Move the current `download_handler.py` TVDB/AI episode rename and generic media auto-rename behavior into `app/modules/renaming.py`.

- [ ] **Step 4: Use storage provider instead of direct 115 ownership**

The renaming module reads `event.storage` first and falls back to `init.openapi_115` only for backward compatibility in targeted tests.

- [ ] **Step 5: Verify renaming**

Run:

```bash
python3 -m unittest tests/test_media_auto_rename.py tests/test_tvdb_rename.py tests/test_composable_renaming.py
python3 -m py_compile $(git ls-files '*.py')
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit renaming branch**

```bash
git add app config tests docs/superpowers
git commit -m "Create composable renaming module"
```

---

### Task 5: Integration Validation

**Files:**
- Create integration branch only: `integration/composable-modules-2026-07-09`
- Create or modify integration-only config example if needed.
- Create: `tests/test_composable_integration.py`

**Interfaces:**
- Consumes all modules.
- Produces validation that core plus three business modules can reconstruct the current main flow.

- [ ] **Step 1: Create integration branch from core**

```bash
git switch feature/telepiplex-core
git switch -c integration/composable-modules-2026-07-09
git merge --no-ff feature/115
git merge --no-ff feature/media-search
git merge --no-ff feature/renaming
```

Expected: no conflicts after module extraction.

- [ ] **Step 2: Write integration test**

```python
def test_all_modules_register_without_rewriting_core_entrypoint():
    registry = ModuleRegistry()
    for register in (register_open115, register_media_search, register_renaming):
        register(registry)
    assert registry.download_provider is not None
    assert registry.storage_provider is not None
    assert [item.name for item in registry.post_download_processors][:2] == [
        "renaming.tvdb_episode",
        "renaming.generic_media",
    ]
```

- [ ] **Step 3: Verify integration branch**

Run:

```bash
python3 -m unittest tests/test_composable_integration.py tests/test_composable_core.py tests/test_composable_115.py tests/test_media_search_surface.py tests/test_media_search_utils.py tests/test_media_auto_rename.py tests/test_tvdb_rename.py tests/test_composable_renaming.py
python3 -m py_compile $(git ls-files '*.py')
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 4: Check branch shape**

Run:

```bash
git merge-base feature/telepiplex-core feature/115
git merge-base feature/telepiplex-core feature/media-search
git merge-base feature/telepiplex-core feature/renaming
git merge-tree --write-tree --messages feature/115 feature/media-search
```

Expected: feature branch merge-bases are `feature/telepiplex-core`, and simulated merges do not produce business-file conflicts.

- [ ] **Step 5: Push when SSH remote is available**

Run:

```bash
git push origin feature/telepiplex-core feature/115 feature/media-search feature/renaming integration/composable-modules-2026-07-09
```

Expected: remote refs update. If SSH is unavailable, report the local commits and the push failure exactly.
