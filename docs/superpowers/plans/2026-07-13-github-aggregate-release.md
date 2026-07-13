# GitHub Aggregate Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Publish a linux/amd64 Core image, four Linux Feature artifacts, and a digest-pinned remote catalog from one immutable GitHub Release.

**Architecture:** A tested Python generator verifies tpx manifests and emits deterministic catalog metadata. A tag-only GitHub Actions workflow validates Core, builds each Feature branch on Ubuntu, pushes Core to GHCR, generates the catalog, and creates a non-overwriting GitHub Release.

**Tech Stack:** Python 3.12, PyYAML, Telepiplex artifact verifier, GitHub Actions, Docker Buildx, GHCR, GitHub CLI.

## Global Constraints

- Work only in feature/telepiplex-core.
- Keep all Feature branches independent.
- Build Core only for linux/amd64.
- Do not publish from pull requests or ordinary branch pushes.
- Do not overwrite an existing Release or reuse a plugin name@version for changed bytes.
- Catalog URLs use HTTPS and every entry pins a lowercase SHA-256.
- Do not push, create tags, or trigger a real release during local implementation.

---

### Task 1: Deterministic release catalog generator

**Files:**
- Create: tools/generate_release_catalog.py
- Create: tests/test_release_catalog_generator.py

**Interfaces:**
- build_catalog(repository, tag, artifact_paths) returns a validated mapping.
- write_catalog(repository, tag, artifact_paths, output) writes stable YAML and a sibling sha256 file.
- CLI accepts --repository, --tag, --output, and tpx paths.

- [ ] Step 1: Write failing tests for four verified tpx artifacts, deterministic bytes, manifest-derived URL/version/core_api/source, duplicate version, missing required plugin, invalid repository/tag, and corrupt artifact.
- [ ] Step 2: Run the focused test and confirm RED because the generator module is absent.
- [ ] Step 3: Implement strict validation using app.core.plugin_artifact.verify_tpx. Never trust the filename for identity or version. Require exactly open115, media-search, renaming, and plex-management.
- [ ] Step 4: Run focused tests and confirm GREEN.
- [ ] Step 5: Commit as feat(core): generate pinned release catalogs.

### Task 2: Replace unsafe Docker workflow with aggregate release workflow

**Files:**
- Delete: .github/workflows/docker-build.yml
- Create: .github/workflows/release.yml
- Create: tests/test_release_workflow.py

**Interfaces:**
- Trigger: platform-v* tag or workflow_dispatch release_tag.
- Jobs: validate-core, build-features matrix, build-core-image, publish-release.
- Registry: ghcr.io/<owner>/telepiplex-core.
- Required Feature refs: feature/115, feature/media-search, feature/renaming, feature/plex-management.

- [ ] Step 1: Write failing static workflow tests. Parse YAML with BaseLoader and assert only tag/dispatch triggers, permissions, concurrency, linux/amd64, GHCR, four branch refs, no PR publish path, no Docker Hub secrets, and publish-release needs validation, image, and Feature jobs.
- [ ] Step 2: Run the focused test and confirm RED against the old unsafe workflow.
- [ ] Step 3: Implement release.yml. Validate tag and non-existing Release, run Core tests, build Feature matrix on Ubuntu with tools/build_feature.py, upload artifacts, build/push GHCR image, generate catalog, verify digests, and create the Release once.
- [ ] Step 4: Run workflow tests, YAML parsing, and deployment contract tests; confirm GREEN.
- [ ] Step 5: Commit as ci(core): publish aggregate platform releases.

### Task 3: Documentation and full verification

**Files:**
- Modify: README.md
- Modify: README_EN.md
- Modify: docs/todos/2026-07-12-business-module-decisions.md
- Modify: tests/test_deployment_contract.py

- [ ] Step 1: Write a failing documentation contract test requiring GHCR image naming, platform-v tag release command, four tpx assets, remote catalog, immutable version rule, and no silent updates.
- [ ] Step 2: Run the focused test and confirm RED.
- [ ] Step 3: Document release operation, artifact URLs, catalog use, version-bump gate, and the separation between 01A publishing and 01B update notifications. Mark OPS-TODO-01A implemented while leaving 01B active.
- [ ] Step 4: Run the focused tests, full 138-test baseline successor, compileall, YAML parse, and diff checks.
- [ ] Step 5: Commit as docs(core): document aggregate release workflow.
