# First-install Feature Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Expose a dependency-aware Telegram Feature catalog that installs the newest stable Core-compatible release with one explicit button click.

**Architecture:** The release generator publishes manifest capability metadata. PluginCatalog selects install candidates, PluginManager adds live capability state and cache fallback, and the Core Telegram handler renders and executes reserved install callbacks through the existing install transaction.

**Tech Stack:** Python 3.12, asyncio, PyYAML, packaging, python-telegram-bot, pytest/unittest.

## Global Constraints

- Never install silently or in bulk.
- Preserve exact-version and local `.tpx` command paths.
- Never bypass PluginManager verification or lifecycle transactions.
- Keep Core generic; derive dependency ordering from catalog capabilities.
- Keep catalog and UI failures isolated from Core and other Features.
- Work locally on `feature/telepiplex-core` without push.

---

### Task 1: Publish capability-aware catalog entries

**Files:**
- Modify `tools/generate_release_catalog.py`
- Modify `tests/test_release_catalog_generator.py`

- [ ] Write failing assertions for manifest-derived `provides` and `requires`.
- [ ] Run focused tests and verify RED.
- [ ] Add deterministic capability metadata to release entries.
- [ ] Run focused tests and verify GREEN.
- [ ] Commit `feat(core): publish Feature capability metadata`.

### Task 2: Select dependency-aware install candidates

**Files:**
- Modify `app/core/plugin_catalog.py`
- Modify `app/core/plugin_manager.py`
- Modify `tests/test_plugin_catalog.py`
- Modify `tests/test_plugin_manager.py`

- [ ] Write failing tests for newest compatible stable selection, installed filtering, missing capabilities, provider mapping, local catalogs, and remote cache fallback.
- [ ] Run focused tests and verify RED.
- [ ] Implement catalog candidates and manager capability enrichment.
- [ ] Run focused tests and verify GREEN.
- [ ] Commit `feat(core): list installable Feature releases`.

### Task 3: Telegram overview and install buttons

**Files:**
- Modify `app/handlers/plugin_handler.py`
- Modify `app/115bot.py`
- Modify `tests/test_plugin_handler.py`
- Modify `tests/test_bot_runtime_startup.py`

- [ ] Write failing tests for overview text, ready buttons, blocked prerequisites, authorized install, unauthorized rejection, sanitized errors, and handler reservation.
- [ ] Run focused tests and verify RED.
- [ ] Implement `/plugin` overview and `core-plugin-install:` callback.
- [ ] Run focused tests and verify GREEN.
- [ ] Commit `feat(core): add one-click Feature installation`.

### Task 4: Documentation and full verification

**Files:**
- Modify `README.md`
- Modify `README_EN.md`
- Modify `docs/todos/2026-07-12-business-module-decisions.md`
- Modify `tests/test_deployment_contract.py`

- [ ] Write failing documentation assertions for the first-install flow and completed OPS-TODO-02 status.
- [ ] Run focused tests and verify RED.
- [ ] Document `/plugin`, dependency display, explicit buttons, and manual fallback.
- [ ] Run the exact CI suite, compileall, YAML checks, template comparison, and `git diff --check`.
- [ ] Commit `docs(core): document first-install Feature flow`.
