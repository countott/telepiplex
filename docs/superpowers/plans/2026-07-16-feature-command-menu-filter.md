# Feature Command Menu Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter Telepiplex `/start` and Telegram command menus down to independent Feature tasks while retaining aliases and callable hidden routes.

**Architecture:** Extend `CommandDeclaration` with an optional `menu_visible` flag, then centralize visibility decisions in `command_catalog.py`. Explicit manifest values win; legacy manifests use the approved name-based compatibility filter so already-published Feature 1.1.0 artifacts work with the new Telepiplex immediately.

**Tech Stack:** Python 3.12, dataclasses, PyYAML manifests, python-telegram-bot, pytest/unittest, GitHub Actions.

## Global Constraints

- Modify and publish only `main`.
- Keep `/search`, `/s`, `/magnet`, and `/m` visible.
- Hide `*_config`, `/auth`, and `/q` when legacy manifests omit `menu_visible`.
- Hidden commands remain registered and callable.
- `/start` and Telegram command menus must share exactly one filter.
- Publish the next unused Telepiplex patch tag `telepiplex-v1.1.1` only after fresh verification.

---

### Task 1: Add manifest command visibility metadata

**Files:**
- Modify: `tests/test_command_catalog.py`
- Modify: `app/runtime/plugin_manifest.py`

**Interfaces:**
- Consumes: existing `PluginManifest.from_mapping(value)` command parsing.
- Produces: `CommandDeclaration.menu_visible: bool | None`.

- [ ] **Step 1: Write failing manifest tests**

Add command mappings containing `menu_visible: true`, `menu_visible: false`, and an invalid string. Assert valid values survive parsing and the invalid type raises `ContractError` with `invalid_manifest`.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_command_catalog.py`

Expected: failure because `commands[]` rejects `menu_visible` as an unknown key.

- [ ] **Step 3: Implement the minimal parser contract**

Extend the dataclass:

```python
@dataclass(frozen=True)
class CommandDeclaration:
    name: str
    description: str
    menu_visible: bool | None = None
```

Allow `menu_visible` in command mappings, reject non-boolean values when present, and pass the parsed value into `CommandDeclaration`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_command_catalog.py`

Expected: all command catalog tests pass.

### Task 2: Share the independent-task menu filter

**Files:**
- Modify: `tests/test_command_catalog.py`
- Modify: `app/runtime/command_catalog.py`

**Interfaces:**
- Consumes: `CommandDeclaration.menu_visible`.
- Produces: `advertised_feature_commands(route) -> tuple[CommandDeclaration, ...]` used by both menu builders.

- [ ] **Step 1: Write failing behavior tests**

Activate legacy manifests exposing `/search`, `/s`, `/search_config`, `/magnet`, `/m`, `/auth`, `/q`, and `/rename_config`. Assert both rendered surfaces contain only `/search`, `/s`, `/magnet`, and `/m`, and omit the empty rename group. Add explicit override cases proving `menu_visible: true` can show a legacy-hidden name and `false` can hide an otherwise-visible task.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_command_catalog.py`

Expected: hidden legacy commands are still present before the filter exists.

- [ ] **Step 3: Implement one shared filter**

Add:

```python
LEGACY_HIDDEN_COMMANDS = frozenset({"auth", "q"})

def command_is_advertised(declaration):
    if declaration.menu_visible is not None:
        return declaration.menu_visible
    return (
        declaration.name not in LEGACY_HIDDEN_COMMANDS
        and not declaration.name.endswith("_config")
    )

def advertised_feature_commands(route):
    return tuple(
        declaration
        for declaration in route.manifest.commands
        if command_is_advertised(declaration)
    )
```

Make both `build_bot_commands()` and `build_start_help()` consume the helper.

- [ ] **Step 4: Run focused and adjacent tests**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_command_catalog.py tests/test_plugin_manifest.py tests/test_telepiplex_surface.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit implementation**

```bash
git add app/runtime/plugin_manifest.py app/runtime/command_catalog.py tests/test_command_catalog.py
git commit -m "feat(runtime): filter advertised Feature commands"
```

### Task 3: Verify, push, and publish Telepiplex 1.1.1

**Files:**
- Verify only.

**Interfaces:**
- Consumes: committed Telepiplex command filtering.
- Produces: synchronized `origin/main`, GHCR `1.1.1`/`latest`, and public `telepiplex-v1.1.1` Release.

- [ ] **Step 1: Run full local verification**

Run:

```bash
PYTHONPATH=.:sdk/src python -m pytest -q
PYTHONPATH=.:sdk/src python -m compileall -q app sdk tools tests
git diff --check
python -m pip check
```

Expected: no failures, compile errors, whitespace errors, or broken requirements.

- [ ] **Step 2: Push the Telepiplex branch and verify equality**

Run: `git push origin main`, fetch, then require `origin/main...HEAD` to report `0 0`.

- [ ] **Step 3: Create and push the release tag**

Create annotated tag `telepiplex-v1.1.1` at the verified Telepiplex commit and push it to `origin`.

- [ ] **Step 4: Monitor and verify publication**

Require every GitHub Actions job to conclude `success`, the public Latest Release to be `telepiplex-v1.1.1`, and GHCR `1.1.1` and `latest` to resolve to the same OCI digest containing `linux/amd64`.
