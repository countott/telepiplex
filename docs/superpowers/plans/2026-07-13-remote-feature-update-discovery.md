# Remote Feature Update Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Refresh a remote digest-pinned catalog, detect compatible Feature updates, notify Telegram once, and update only after one explicit callback.

**Architecture:** PluginCatalog owns HTTPS refresh, atomic cache, release parsing, and semver compatibility. PluginManager exposes active-release update candidates. The Telegram runtime owns a cancellable monitor task, deduplicated notifications, and a Telepiplex callback that invokes the existing update transaction.

**Tech Stack:** Python 3.12, asyncio, urllib, PyYAML, packaging, python-telegram-bot, pytest/unittest.

## Global Constraints

- Never silently update.
- Never let catalog or notification failure stop Telepiplex or another Feature.
- Preserve local catalog path support.
- Require HTTPS and pinned lowercase SHA-256.
- Reuse PluginManager.update for shadow, drain, switch, and rollback.
- Keep both config templates byte-identical.
- Work locally on main without push.

---

### Task 1: Remote catalog refresh and update comparison

**Files:**
- Modify app/runtime/plugin_catalog.py
- Modify tests/test_plugin_catalog.py

- [ ] Write failing tests for HTTPS refresh, cache fallback, downgrade redirect, size limit, invalid YAML, available update ordering, Host API compatibility, and invalid release metadata.
- [ ] Run focused tests and verify RED.
- [ ] Implement remote/local source detection, validated atomic cache, CatalogRelease/CatalogUpdate, and available_updates.
- [ ] Run focused tests and verify GREEN.
- [ ] Commit feat(runtime): discover remote Feature releases.

### Task 2: PluginManager update candidates

**Files:**
- Modify app/runtime/plugin_manager.py
- Modify tests/test_plugin_manager.py

- [ ] Write failing tests showing only active releases are compared and custom resolvers without update discovery return an empty list.
- [ ] Run focused tests and verify RED.
- [ ] Implement async available_updates using store active versions and host_api_version.
- [ ] Run focused tests and verify GREEN.
- [ ] Commit feat(runtime): expose compatible Feature updates.

### Task 3: Telegram monitor and one-click confirmation

**Files:**
- Create app/runtime/plugin_update_monitor.py
- Modify app/handlers/plugin_handler.py
- Modify app/115bot.py
- Modify tests/test_plugin_handler.py
- Modify tests/test_bot_runtime_startup.py
- Create tests/test_plugin_update_monitor.py

- [ ] Write failing tests for one notification, deduplication, no update before click, authorized confirm, unauthorized rejection, decline, sanitized error, startup tolerance, and shutdown cancellation.
- [ ] Run focused tests and verify RED.
- [ ] Implement monitor run_once/run loop, Telepiplex callback, handler ordering, task startup, and cancellation.
- [ ] Run focused tests and verify GREEN.
- [ ] Commit feat(runtime): notify and confirm Feature updates.

### Task 4: Configuration, docs, and verification

**Files:**
- Modify app/config.yaml.example
- Modify config/config.yaml.example
- Modify README.md
- Modify README_EN.md
- Modify docs/todos/2026-07-12-business-module-decisions.md
- Modify tests/test_config_template_contract.py
- Modify tests/test_deployment_contract.py

- [ ] Write failing tests for remote default URL, refresh interval, byte-identical templates, documentation, and completed OPS-TODO-01 status.
- [ ] Run focused tests and verify RED.
- [ ] Update both templates and documentation, preserving local catalog fallback and explicit no-silent-update wording.
- [ ] Run focused tests and full suite, compileall, YAML checks, and diff checks.
- [ ] Commit docs(runtime): document remote Feature updates.
