# Telepiplex Runtime Modularity Self-Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the reproducible data-safety, module fallback, startup-hook, authorization, and deployment defects found during the full runtime/modularity audit, then publish synchronized main and affected module branches.

**Architecture:** Keep the existing in-repo module registry and restart-effective module configuration. Make renaming fail closed before destructive operations, let the 115 module own the terminal unorganized fallback, keep core startup hooks signature-aware, protect core config reload, and make the build script target the image consumed by Docker Compose.

**Tech Stack:** Python 3.12, unittest, python-telegram-bot 22.3, YAML, Docker CLI, Git worktrees.

## Global Constraints

- Preserve the independent ownership of `feature/telepiplex-core`, `feature/115`, `feature/media-search`, and `feature/renaming`.
- Do not merge `main` wholesale into module branches.
- Every production behavior change must have a test that is observed failing before implementation.
- A failed or incomplete rename must keep the source directory and enter the unorganized fallback instead of reporting success.
- Module changes remain restart-effective; `/reload` does not hot-load handlers.

---

### Task 1: Make Core Startup Hooks Signature-Safe

**Files:**
- Modify: `app/core/module_registry.py`
- Test: `tests/test_composable_core.py`

**Interfaces:**
- Consumes: `ModuleRegistry.add_startup_hook(hook)`
- Produces: `ModuleRegistry.run_startup_hooks(application=None)` with exactly-once invocation

- [ ] **Step 1: Write failing tests**

Add tests proving a no-argument hook and an application-argument hook each run once, while an internal `TypeError` from a hook is propagated without calling the hook a second time.

- [ ] **Step 2: Verify RED**

Run `python3 -m unittest tests.test_composable_core.ComposableCoreTest.test_startup_hook_internal_type_error_is_not_retried -v` and confirm the current hook is called twice.

- [ ] **Step 3: Implement the minimal fix**

Use `inspect.signature(hook).bind(application)` to decide whether the hook accepts an application argument before invocation. Do not use a caught runtime `TypeError` as signature detection.

- [ ] **Step 4: Verify GREEN**

Run `python3 -m unittest tests.test_composable_core -v` and confirm all core contract tests pass.

### Task 2: Make Renaming Fail Closed

**Files:**
- Modify: `app/utils/tvdb_rename.py`
- Modify: `app/modules/renaming.py`
- Test: `tests/test_tvdb_rename.py`
- Test: `tests/test_composable_renaming.py`

**Interfaces:**
- Consumes: `build_tvdb_rename_plan(...)`, `_attempt_media_auto_rename(event, naming_metadata)`
- Produces: complete one-to-one TVDB plans and checked generic storage operations

- [ ] **Step 1: Write failing TVDB plan tests**

Add tests proving a plan is rejected when it maps only part of the video tree or maps the same source video more than once.

- [ ] **Step 2: Verify TVDB RED**

Run the two new `tests.test_tvdb_rename` cases and confirm both currently return a plan.

- [ ] **Step 3: Implement complete one-to-one validation**

Collect the canonical video relative paths, reject duplicate source paths, and require the mapped source set to equal the source video set before returning a plan.

- [ ] **Step 4: Write failing generic rename tests**

Add tests proving multi-video folders are skipped and source deletion never occurs when target creation, rename, or move fails.

- [ ] **Step 5: Verify generic RED**

Run the new `tests.test_composable_renaming` cases and confirm current code reports success or deletes the source.

- [ ] **Step 6: Implement checked operations**

Require exactly one video for generic naming. Validate `create_dir_recursive`, `rename`, and `move_file`; raise an operation error before source cleanup on failure. Delete the original wrapper only after the move succeeds.

- [ ] **Step 7: Verify GREEN**

Run `python3 -m unittest tests.test_tvdb_rename tests.test_composable_renaming tests.test_media_auto_rename -v`.

### Task 3: Restore Unorganized Fallback as a 115 Module Processor

**Files:**
- Modify: `app/modules/open115.py`
- Test: `tests/test_composable_115.py`
- Test: `tests/test_composable_integration.py`

**Interfaces:**
- Consumes: unhandled `DownloadCompletedEvent`, `media.unorganized_path`, `event.storage`
- Produces: `PostDownloadResult` from processor `open115.unorganized_fallback` at priority `900`

- [ ] **Step 1: Write failing fallback tests**

Add tests proving the 115 module registers the priority-900 fallback, an unhandled download moves to `/未整理`, and a successful earlier terminal processor prevents fallback execution.

- [ ] **Step 2: Verify RED**

Run the new composable 115/integration cases and confirm the fallback processor is absent.

- [ ] **Step 3: Implement the fallback**

Read `media.unorganized_path`, create it through the event storage provider, move the raw final path into it, return the new full path and a success message, and raise on create/move failure so the pipeline preserves the raw path.

- [ ] **Step 4: Verify GREEN**

Run `python3 -m unittest tests.test_composable_115 tests.test_composable_integration tests.test_download_task_startup -v`.

### Task 4: Protect Core Reload

**Files:**
- Modify: `app/115bot.py`
- Test: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Consumes: Telegram `/reload` update and `init.check_user`
- Produces: authorized reload or an unauthorized warning without config mutation

- [ ] **Step 1: Write the failing authorization test**

Add an async test where `init.check_user` returns false and assert `init.load_yaml_config` is not called.

- [ ] **Step 2: Verify RED**

Run the new reload test and confirm the current handler reloads configuration for the unauthorized caller.

- [ ] **Step 3: Add the authorization guard**

Mirror the existing `/modules` denial response before calling `init.load_yaml_config`.

- [ ] **Step 4: Verify GREEN**

Run `python3 -m unittest tests.test_bot_runtime_startup -v`.

### Task 5: Repair the Local Docker Build Contract

**Files:**
- Modify: `build.sh`
- Test: `tests/test_deployment_contract.py`

**Interfaces:**
- Consumes: repository `Dockerfile` and `docker-compose.yaml`
- Produces: local image `telepiplex-core:latest`

- [ ] **Step 1: Write the failing deployment contract test**

Assert every Dockerfile referenced by `build.sh` exists and the built image tag matches the Compose image `telepiplex-core:latest`.

- [ ] **Step 2: Verify RED**

Run `python3 -m unittest tests.test_deployment_contract -v` and confirm it reports missing `Dockerfile.base` and tag mismatch.

- [ ] **Step 3: Simplify the build script**

Use `set -euo pipefail`, build the standalone `Dockerfile` once as `telepiplex-core:latest`, and display that exact image.

- [ ] **Step 4: Verify GREEN**

Run `python3 -m unittest tests.test_deployment_contract -v` and `bash -n build.sh`.

### Task 6: Full Verification and Branch-Scoped Publication

**Files:**
- Modify only affected branch-owned files and tests when syncing module branches.

**Interfaces:**
- Produces: synchronized `main`, `feature/telepiplex-core`, `feature/115`, `feature/media-search`, and `feature/renaming` local/remote refs

- [ ] **Step 1: Run main verification**

Run the complete unittest discovery, Python compilation, dependency check, Bash syntax check, configuration-template equality/YAML parsing checks, whitespace check, and all eight module combinations.

- [ ] **Step 2: Self-review the final diff**

Check destructive storage calls, exception paths, module ownership, command authorization, and deployment filenames against this plan and the existing composable-module design.

- [ ] **Step 3: Commit and fast-forward main**

Commit the verified audit fixes, fast-forward local `main`, rerun the complete main verification, and push `main`.

- [ ] **Step 4: Sync affected module branches independently**

Apply only core-owned changes to `feature/telepiplex-core`, only 115-owned changes to `feature/115`, metadata-search-owned changes to `feature/media-search`, and renaming-owned changes to `feature/renaming`; run each branch's scoped suite before pushing.

- [ ] **Step 5: Verify remote equality**

Fetch remote refs and require every local branch hash to equal its corresponding `origin/*` hash before reporting completion.
