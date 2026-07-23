# Feature Config Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a versioned Feature update adopt new default configuration keys without overwriting existing operator values, while keeping config and template changes transactional.

**Architecture:** `PluginStore` recursively fills only missing keys from the new `config.default.yaml`, validates the merged mapping with the new schema, and reports incompatible migrations with a stable error code. `PluginManager` writes the migrated config before shadow startup and restores the previous config and example on activation failure. Telegram update feedback lists added key paths without exposing values.

**Tech Stack:** Python 3.12, asyncio, PyYAML, JSON Schema, unittest/pytest.

## Global Constraints

- Existing operator values always win over defaults, including credentials.
- Lists and scalar values are never merged or replaced when already present.
- Removed, renamed, or type-incompatible values fail closed with `config_migration_required`.
- Config and `config.yaml.example` return to the old release on activation failure.
- No remote branch, tag, Release, or catalog mutation is allowed.

---

### Task 1: Missing-default migration and validation

**Files:**
- Modify: `app/runtime/plugin_store.py`
- Modify: `tests/test_plugin_manager.py`

- [x] Write failing update tests for nested missing keys, existing value preservation, and incompatible schema changes.
- [x] Run the focused tests and confirm RED from the current strict new-schema validation.
- [x] Implement recursive missing-key fill and `config_migration_required` validation.
- [x] Run focused store/manager tests and confirm GREEN.

### Task 2: Transactional activation and operator feedback

**Files:**
- Modify: `app/runtime/plugin_manager.py`
- Modify: `app/handlers/plugin_handler.py`
- Modify: `tests/test_plugin_manager.py`
- Modify: `tests/test_plugin_handler.py`

- [x] Write failing tests proving migrated config is visible to the new shadow process, failed activation restores config/template, and feedback lists only added key paths.
- [x] Implement activation-time atomic config write and rollback.
- [x] Add `config_added_keys` to successful update details and Telegram output.
- [x] Run focused tests, then the full Telepiplex suite and compilation.
- [x] Commit locally without pushing.

### Task 3: Review hardening

**Files:**
- Modify: `app/runtime/plugin_store.py`
- Modify: `app/runtime/plugin_manager.py`
- Modify: `tests/test_plugin_manager.py`

- [x] Keep active missing, malformed, or unreadable config on the stable `config_migration_required` path.
- [x] Persist mode-0600 release rollback snapshots and restore them before rollback shadow startup.
- [x] Delay manager-driven update template switching until activation succeeds.
- [x] Report incomplete activation compensation as `activation_rollback_failed`.
- [x] Complete final read-only review with no remaining Critical or Important findings.
