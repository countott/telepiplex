# Recovery Version Bumps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assign new immutable Host and Feature release identities after the prior five-tag push reached GitHub without generating Actions events.

**Architecture:** Advance the Host and four unreleased Feature patch versions while keeping `search@1.1.0` unchanged. Synchronize every current source-of-truth, checked-in package metadata, build example, contract assertion, and Unraid publisher test fixture before handing the new versions to the corrected one-tag-per-push publisher.

**Tech Stack:** Python 3.12, YAML, TOML, Bash, `pytest`.

## Global Constraints

- Host advances from `v3.4.8-host` to `v3.4.9-host`.
- `download` advances from `1.0.4` to `1.0.5`.
- `rename` advances from `1.0.3` to `1.0.4`.
- `sync` advances from `1.0.1` to `1.0.2`.
- `caption` advances from `0.1.1` to `0.1.2`.
- `search` remains `1.1.0`.
- Existing remote tags are immutable and must not be deleted, moved, or reused.
- Do not run Git, create Git metadata, or publish from the Mac workspace.
- Historical release prose and generic test fixtures remain unchanged unless they assert the current checked-in identity.

---

### Task 1: Lock the recovery identities in tests

**Files:**
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_technical_identity_migration.py`
- Modify: `tests/test_unraid_publish_script.py`
- Modify: `features/download/tests/test_feature_runtime.py`
- Modify: `features/rename/tests/test_feature_processor.py`
- Modify: `features/sync/tests/test_feature_runtime.py`

**Interfaces:**
- Consumes: Current Host/Feature version literals and publisher remote-tag fixtures.
- Produces: Expected recovery identities plus a publisher scenario that emits `telepiplex-v3.4.9` and the four new Feature tags individually.

- [ ] **Step 1: Update current identity expectations**

Apply the Global Constraints mapping to the Host assertion, technical identity
table, Feature current-source contract tests, runtime context fixtures, README
build-path assertions, and publisher test tag lists. Keep every `search`
identity at `1.1.0`.

- [ ] **Step 2: Run isolated focused tests and confirm red failures**

Run the Host, identity table, Unraid publisher, download contract, rename
contract, and sync contract tests from their correct root/module working
directories. Expected failures must show the still-old Host or Feature source
versions and publisher tag refs.

### Task 2: Apply synchronized patch versions

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
- Consumes: Recovery identity expectations from Task 1.
- Produces: Internally consistent Host and Feature source trees for new immutable tags.

- [ ] **Step 1: Update authoritative Host and Feature metadata**

Change only the Host version literal and affected Feature manifest/project
versions. Leave SDK and search versions unchanged.

- [ ] **Step 2: Synchronize generated metadata and current README references**

Update the checked-in download/rename `PKG-INFO` version headers, current build
artifact paths, and caption's current reserved-version description. Preserve
sync's historical `1.0.0` migration explanation.

- [ ] **Step 3: Run focused tests and Bash syntax validation**

Re-run all Task 1 focused tests plus:

```bash
bash -n scripts/unraid/telepiplex-publish.sh
```

Expected: all focused tests pass and Bash syntax exits `0`.

### Task 3: Verify and hand off

**Files:**
- Verify: Every file listed in Tasks 1 and 2.

**Interfaces:**
- Consumes: Synchronized recovery versions and corrected publisher behavior.
- Produces: Fresh local verification evidence and a Syncthing handoff ready for `PUBLISH 3.4.9 release telepiplex 3.4.9`.

- [ ] **Step 1: Audit current and historical version references**

Confirm each affected current directory contains its new identity, old values
remain only in intentional historical prose/fixtures, and `search` remains
`1.1.0`.

- [ ] **Step 2: Run the full Host and five-Feature suites**

Run the AGENTS.md full test command with `set -e`,
`PYTHONDONTWRITEBYTECODE=1`, and pytest cache disabled.

- [ ] **Step 3: Verify workspace boundaries**

Run:

```bash
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

- [ ] **Step 4: Stop at Syncthing handoff**

List every changed file and actual validation result. Ask the user to wait for
Syncthing `Up to Date / 最新`, replace the actual Unraid User Scripts copy if
needed, and run `PUBLISH 3.4.9 release telepiplex 3.4.9` themselves.
