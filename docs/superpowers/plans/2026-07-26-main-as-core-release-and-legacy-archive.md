# Main-as-Core Release and Legacy Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce `main` as the only Core/Host release source, keep the default `latest` artifacts on successful main-derived releases, and provide a recoverable retirement path for the obsolete Core branch.

**Architecture:** The Core release workflow gains an executable ancestry gate in its validation job. Existing versioned image, container `latest`, and GitHub Latest publication behavior stays unchanged. Current docs name `main` as Core/Host, while a pinned Unraid runbook preserves the divergent legacy branch with an annotated archive tag before deletion.

**Tech Stack:** GitHub Actions YAML, Bash/Git in CI, Python 3.12 `unittest`/`pytest`, Markdown.

## Global Constraints

- Mac `/Users/young/Documents/telepiplex` must not execute Git or create `.git`/`.worktrees`.
- `main` is the only active Core/Host source branch.
- Feature sources remain under `main/features/<plugin_id>` with independent version tags.
- Ordinary `main` pushes do not publish `latest`; only successful `telepiplex-v<semver>` releases do.
- Legacy branch archival Git operations are executed by the user on Unraid only.

---

### Task 1: Enforce main-derived Core releases

**Files:**
- Modify: `tests/test_release_workflow.py`
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: GitHub tag-event `GITHUB_SHA` and remote `refs/heads/main`.
- Produces: validation step `Verify telepiplex release commit belongs to main`.

- [x] **Step 1: Write the failing workflow behavior test**

Add a test that extracts the new validation step, runs it with a fake `git`
command, and requires success only when `git merge-base --is-ancestor`
returns success. Also require the gate to appear before the test-suite step.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_release_workflow.py::ReleaseWorkflowTest::test_telepiplex_release_commit_must_belong_to_main
```

Expected: FAIL because the validation step is absent.

- [x] **Step 3: Add the minimal workflow gate**

Fetch remote `main`, resolve both refs to commits, and fail unless:

```bash
git merge-base --is-ancestor "$RELEASE_COMMIT" "$MAIN_COMMIT"
```

- [x] **Step 4: Run focused and release-contract tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_release_workflow.py tests/test_technical_identity_migration.py
```

Expected: PASS.

### Task 2: State the active main-as-Core contract

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the release workflow contract from Task 1.
- Produces: current operator and agent instructions with no active legacy Core branch.

- [x] **Step 1: Update current architecture wording**

State that `main` is the only Core/Host source branch, Feature sources are
independent directories inside `main`, and `feature/telepiplex-core` is
retired.

- [x] **Step 2: Document default release behavior**

State that a successful main-derived `telepiplex-v<semver>` release updates
both the versioned image and `latest`, while ordinary `main` pushes do not.

### Task 3: Add the recoverable legacy-branch archive runbook

**Files:**
- Create: `docs/archive/2026-07-26-feature-telepiplex-core.md`

**Interfaces:**
- Consumes: expected legacy tip `4393bebac52ff75a1b46cf1ef9d634a4b4299f9d`.
- Produces: user-operated Unraid commands that preserve and verify the archive before branch deletion.

- [x] **Step 1: Write the pinned archive procedure**

Document exact fetch, SHA comparison, annotated tag creation, tag push,
peeled-tag verification, remote-branch recheck, deletion, and absence
verification commands.

- [x] **Step 2: Confirm the archive tag cannot trigger release workflows**

Check the tag against `.github/workflows/release.yml` and
`.github/workflows/release-feature.yml` trigger patterns.

### Task 4: Verify the complete workspace

**Files:**
- Verify only.

**Interfaces:**
- Consumes: all preceding changes.
- Produces: local evidence for handoff.

- [x] **Step 1: Run all root tests**

Run the root pytest command from `AGENTS.md`.

- [x] **Step 2: Run all five Feature test suites**

Run the Feature loop from `AGENTS.md`.

- [x] **Step 3: Check active terminology and workspace boundaries**

Confirm current runtime/release/docs contain no positive
`feature/telepiplex-core` source reference except the explicit retired/archive
record. Confirm `.git` and `.worktrees` are absent and `.stfolder` exists.

- [x] **Step 4: Hand off archival and publication**

List changed files and actual results. Remind the user to wait for Syncthing
`Up to Date / 最新`, then run the pinned archive procedure and publish the
next `telepiplex-v<semver>` tag from Unraid.

### Task 5: Make the formal GHCR image the default deployment

**Files:**
- Modify: `docker-compose.yaml`
- Modify: `tests/test_deployment_contract.py`
- Modify: `README.md`
- Modify: `README_EN.md`

**Interfaces:**
- Consumes: formal image `ghcr.io/countott/telepiplex:latest`.
- Produces: default Compose deployment plus explicit local-image overrides.

- [x] **Step 1: Prove the default Compose contract is currently missing**

Change the deployment contract test to require
`${TELEPIPLEX_IMAGE:-ghcr.io/countott/telepiplex:latest}` and
`${TELEPIPLEX_PULL_POLICY:-always}`, then run that test and observe failure.

- [x] **Step 2: Implement and verify the default Compose contract**

Add the image and pull-policy defaults to `docker-compose.yaml`, then rerun the
focused test and require success.

- [x] **Step 3: Preserve local and Unraid extra builds**

Keep `build.sh` unchanged so its existing `telepiplex:latest` output remains
available after 1:1 Syncthing delivery. Document the explicit
`TELEPIPLEX_IMAGE=telepiplex:latest TELEPIPLEX_PULL_POLICY=never` Compose
override in both README files. Do not change Feature release validation.

- [x] **Step 4: Run focused and complete verification**

Run the deployment contract, all root tests, all five Feature suites, and the
workspace boundary checks from `AGENTS.md`.

### Task 6: Advance the Core runtime patch version

**Files:**
- Modify: `app/115bot.py`
- Modify: `tests/test_bot_runtime_startup.py`

**Interfaces:**
- Consumes: current user-visible Core version `v3.4.5-host`.
- Produces: user-visible Core version `v3.4.6-host`.

- [x] **Step 1: Add and run the failing Core version contract**

Load the real bot entry module and require `get_version()` to return
`v3.4.6-host`. Run the focused test and observe the old version failure.

- [x] **Step 2: Apply the minimal patch bump**

Change only the `get_version()` literal from `v3.4.5-host` to
`v3.4.6-host`. Do not change Feature manifests or SDK package versions.

- [x] **Step 3: Run focused and complete verification**

Run the version test, all root tests, all five Feature suites, and the
workspace boundary checks from `AGENTS.md`.
