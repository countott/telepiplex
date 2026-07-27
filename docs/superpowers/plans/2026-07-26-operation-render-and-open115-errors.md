# Operation Rendering and Open115 Error Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:test-driven-development` inline for this task. Git and
> worktrees are prohibited by the workspace `AGENTS.md`.

**Goal:** Serialize all Telegram mutations for one operation, make successful
selection visibly read-only, and expose actionable sanitized 115 failures.

**Architecture:** Core provides one application-scoped lock per operation and
uses it around both callback actions and background status rendering. The
download Feature enriches `Open115Error`, classifies failures through a small
pure module, and reuses one structured failure object across logs, events,
operation status, job persistence, and notifications.

**Tech Stack:** Python 3.12, asyncio, python-telegram-bot 22.3, SQLite
InteractionCoordinator, telepiplex Feature SDK, pytest/unittest.

## Global constraints

- Do not run Git or create Git metadata in the Mac workspace.
- Do not change Search query, candidate, release-gate, or ranking behavior.
- Do not expose tokens, magnets, provider URLs, or authorization headers.
- Search remains `1.0.8`.
- Host becomes `v3.4.5-host`; download becomes `1.0.2`.

---

### Task 1: Serialize operation message rendering

**Files:**
- Modify: `tests/test_plugin_handler.py`
- Modify: `tests/test_interaction_handler.py`
- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/handlers/plugin_handler.py`

**Interfaces:**
- Produces: `operation_render_lock(application, operation_id) -> asyncio.Lock`
- Consumes: `InteractionCoordinator.set_message_id(...)`,
  `_render_actions(...)`, and `render_operation(...)`

- [ ] Add a concurrency regression that blocks callback `edit_text`, starts a
  background operation render, and proves the latter currently sends a
  duplicate before the callback persists its message.
- [ ] Add a log regression requiring sanitized BadRequest text plus
  `message_id` and `message_kind`.
- [ ] Run the focused Core tests and record the expected failures.
- [ ] Add an application-scoped per-operation lock.
- [ ] Hold the lock across callback action rendering and message identity
  persistence.
- [ ] Hold the same lock across background edit/send and message identity
  persistence.
- [ ] Log sanitized Telegram exception detail and message identity.
- [ ] Run the focused Core tests until green.

### Task 2: Preserve and classify 115 failures

**Files:**
- Create: `features/download/src/telepiplex_download/failure.py`
- Modify: `features/download/src/telepiplex_download/client.py`
- Modify: `features/download/src/telepiplex_download/service.py`
- Modify: `features/download/tests/test_feature_runtime.py`

**Interfaces:**
- Produces:
  `DownloadFailure(code, summary, detail, remedy, provider_code, operation)`
- Produces:
  `classify_download_failure(exc, stage) -> DownloadFailure`
- Extends:
  `Open115Error(message, *, code="", operation="")`

- [ ] Add pure failing tests for authorization, directory, submit-rejection,
  and request classifications.
- [ ] Add a failing download-job regression that requires structured log,
  event, operation details, job error, and Telegram remedy.
- [ ] Run the focused download tests and record the expected failures.
- [ ] Implement the immutable failure description and classifier.
- [ ] Preserve code and provider operation on directory and offline-add
  failures.
- [ ] Replace every class-name-only failure surface in `_download_job` with
  the structured failure.
- [ ] Run the focused download tests until green.

### Task 3: Update release identity and documentation

**Files:**
- Modify: `app/115bot.py`
- Modify: `features/download/manifest.yaml`
- Modify: `features/download/pyproject.toml`
- Modify: `features/download/src/telepiplex_download.egg-info/PKG-INFO`
- Modify: `features/download/README.md`
- Modify: `features/download/tests/test_feature_runtime.py`
- Modify: `tests/test_technical_identity_migration.py`

- [ ] Update Host display version to `v3.4.5-host`.
- [ ] Update download Feature and build example to `1.0.2`.
- [ ] Document actionable provider failures and single operation status.
- [ ] Assert Search remains `1.0.8`.
- [ ] Run the affected identity and contract tests.

### Task 4: Full verification and Syncthing handoff

- [ ] Run all root tests.
- [ ] Run all five Feature test suites.
- [ ] Build and inspect `/tmp/download-1.0.2.tpx`.
- [ ] Confirm `.git` and `.worktrees` are absent and `.stfolder` exists.
- [ ] List every changed file and remind the user to wait for Syncthing
  `Up to Date / 最新`; do not publish.

