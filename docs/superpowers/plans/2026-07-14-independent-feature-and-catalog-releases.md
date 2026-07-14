# Independent Feature and Catalog Releases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish Core images, individual Feature artifacts, and the current Feature catalog through independent release channels.

**Architecture:** `core-v*` tags update only GHCR Core images. A Feature tag builds or reuses one immutable `.tpx` in a read-only job, creates one Feature Release in a write-scoped job, then uses an optimistic fetch/merge/non-fast-forward retry loop to update the `catalog` branch while preserving all unrelated catalog entries. Every Feature Release also carries the complete catalog snapshot so older `releases/latest` clients remain compatible.

**Tech Stack:** Python 3.12, PyYAML, Telepiplex `.tpx` verification, GitHub Actions, GitHub CLI on runners, Git/GHCR.

## Global Constraints

- Existing `platform-v1.0.5`, its GitHub Release, and its Core image remain immutable.
- A Feature version cannot change artifact digest, source branch, or source commit.
- `catalog` publishing uses optimistic non-fast-forward retry and never exposes an unpublished artifact URL.
- Existing Core installations using `releases/latest/download/catalog.yaml` continue working.
- The preferred new catalog URL is `https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml`.
- The migration publishes all four current Feature versions as `1.0.1` without producing Telegram update notifications.

---

### Task 1: Incremental catalog updater

**Files:**
- Create: `tools/update_feature_catalog.py`
- Create: `tests/test_feature_catalog_updater.py`

**Interfaces:**
- Consumes: `verify_tpx(path, expected_sha256="")` and an optional previous catalog mapping.
- Produces: `parse_feature_tag(tag) -> tuple[str, str]`, `merge_feature_release(previous_catalog, artifact_path, repository, tag) -> dict`, and `write_feature_catalog(...) -> Path`.

- [ ] **Step 1: Write failing tag and merge tests**

```python
def test_parses_supported_feature_tag():
    assert parse_feature_tag("media-search-v1.2.3") == ("media-search", "1.2.3")

def test_merge_preserves_other_plugins_and_versions():
    merged = merge_feature_release(previous, media_artifact, "countott/telepiplex", "media-search-v1.2.3")
    assert merged["plugins"]["open115"] == previous["plugins"]["open115"]
    assert "1.2.2" in merged["plugins"]["media-search"]["versions"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_feature_catalog_updater.py`

Expected: collection fails because `tools.update_feature_catalog` does not exist.

- [ ] **Step 3: Implement strict tag parsing and catalog merge**

```python
FEATURE_BRANCHES = {
    "open115": "feature/115",
    "media-search": "feature/media-search",
    "renaming": "feature/renaming",
    "plex-management": "feature/plex-management",
}

def parse_feature_tag(tag):
    match = FEATURE_TAG_RE.fullmatch(str(tag))
    if not match:
        raise CatalogUpdateError("invalid Feature release tag")
    return match.group("plugin"), match.group("version")
```

The merge validates plugin ID, manifest version, source branch, source commit,
digest immutability, HTTPS release URL, and preserves unrelated entries.

- [ ] **Step 4: Add deterministic YAML and checksum tests, then implement atomic output**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_feature_catalog_updater.py`

Expected: all updater tests pass.

- [ ] **Step 5: Commit**

```bash
git add tools/update_feature_catalog.py tests/test_feature_catalog_updater.py
git commit -m "feat(core): merge independent Feature releases into catalog"
```

### Task 2: Split Core and Feature workflows

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `.github/workflows/release-feature.yml`
- Modify: `tests/test_release_workflow.py`

**Interfaces:**
- Consumes: `core-v<semver>` and the four `<plugin-id>-v<semver>` tag families.
- Produces: a Core-only GHCR workflow and a split read-only-build/write-scoped one-Feature Release/catalog workflow with optimistic publication.

- [ ] **Step 1: Replace aggregate expectations with failing workflow-contract tests**

Tests require:

```python
assert core_triggers["push"]["tags"] == ["core-v*"]
assert "build-features" not in core_jobs
assert "concurrency" not in feature_workflow
assert build_job["permissions"]["contents"] == "read"
assert publish_job["permissions"]["contents"] == "write"
assert "catalog.yaml" in feature_release_step
assert "git push --porcelain origin HEAD:catalog" in catalog_publish_step
```

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_release_workflow.py`

Expected: old `platform-v*` aggregate workflow violates the new contracts.

- [ ] **Step 3: Convert `release.yml` to Core-only release**

The workflow validates `core-v<semver>`, runs Core tests/compile, refuses an
existing immutable image version, and pushes only `<version>` plus `latest`.

- [ ] **Step 4: Add split Feature release workflow with optimistic catalog retry**

The read-only job maps the tag to a fixed source branch, checks manifest
identity, and either builds one artifact or reuses the exact verified artifact
from an existing Release. The write-scoped job creates the Feature Release,
then repeatedly fetches the fresh catalog head, merges the Feature, and retries
only non-fast-forward push rejection. Operational probe and push failures stop
closed. After a successful push, catalog assets are synchronized back to the
current and latest Feature Releases for old-client compatibility.

- [ ] **Step 5: Run workflow and deployment contract tests**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_release_workflow.py tests/test_deployment_contract.py`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml .github/workflows/release-feature.yml tests/test_release_workflow.py
git commit -m "ci: split Core and Feature releases"
```

### Task 3: Catalog URL and operator documentation

**Files:**
- Modify: `app/115bot.py`
- Modify: `app/config.yaml.example`
- Modify: `config/config.yaml.example`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `tests/test_config_template_contract.py`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: `tests/test_deployment_contract.py`

**Interfaces:**
- Consumes: the `catalog` branch maintained by Task 2.
- Produces: new-install defaults using the raw catalog URL while retaining the old Release compatibility endpoint.

- [ ] **Step 1: Write failing URL and documentation contract tests**

```python
expected = "https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml"
assert parsed["plugins"]["catalog"] == expected
assert DEFAULT_PLUGIN_CATALOG_URL == expected
```

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_config_template_contract.py tests/test_bot_runtime_startup.py tests/test_deployment_contract.py`

Expected: defaults still point at `releases/latest` and aggregate-release documentation remains.

- [ ] **Step 3: Update both byte-identical templates, runtime fallback, and documentation**

Document Core tags, all Feature tag forms, the catalog branch, old-client
compatibility assets, Feature semver requirements, and Telegram confirmation.

- [ ] **Step 4: Run targeted tests and template equality check**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q tests/test_config_template_contract.py tests/test_bot_runtime_startup.py tests/test_deployment_contract.py && cmp -s app/config.yaml.example config/config.yaml.example`

Expected: all tests pass and templates are byte-identical.

- [ ] **Step 5: Commit**

```bash
git add app/115bot.py app/config.yaml.example config/config.yaml.example README.md README.en.md tests
git commit -m "docs(core): document independent release channels"
```

### Task 4: Full verification and infrastructure publication

**Files:**
- Verify only.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: a pushed `feature/telepiplex-core` release-infrastructure commit.

- [ ] **Step 1: Run full Core tests and compilation**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q && python -m compileall -q app sdk tools tests`

Expected: all tests pass.

- [ ] **Step 2: Run whitespace, template, workflow YAML, and status checks**

Run: `git diff --check && cmp -s app/config.yaml.example config/config.yaml.example`

Expected: clean output and clean worktree after commits.

- [ ] **Step 3: Push Core release infrastructure**

```bash
git push origin feature/telepiplex-core
```

- [ ] **Step 4: Verify remote head equality**

Expected: local and `origin/feature/telepiplex-core` hashes match.

### Task 5: Publish current Feature releases and catalog

**Files:**
- Remote tags, Releases, and `catalog` branch only.

**Interfaces:**
- Consumes: the pushed Feature workflow and the four synchronized Feature branches.
- Produces: four independent `1.0.1` Releases and a complete catalog branch.

- [ ] **Step 1: Verify tag absence and source branch versions**

Expected versions: `open115`, `media-search`, `renaming`, and
`plex-management` are all `1.0.1`.

- [ ] **Step 2: Push tags sequentially from the Core infrastructure commit**

```bash
CORE_RELEASE_COMMIT=$(git rev-parse HEAD)
git tag open115-v1.0.1 "$CORE_RELEASE_COMMIT"
git push origin open115-v1.0.1
git tag media-search-v1.0.1 "$CORE_RELEASE_COMMIT"
git push origin media-search-v1.0.1
git tag renaming-v1.0.1 "$CORE_RELEASE_COMMIT"
git push origin renaming-v1.0.1
git tag plex-management-v1.0.1 "$CORE_RELEASE_COMMIT"
git push origin plex-management-v1.0.1
```

- [ ] **Step 3: Monitor every workflow to success before pushing the next tag**

Expected: each Release contains one `.tpx`, `catalog.yaml`, and
`catalog.yaml.sha256`; unchanged `1.0.1` bytes match `platform-v1.0.5`.

- [ ] **Step 4: Verify final catalog branch**

Verify all four plugin IDs, `1.0.1` versions, per-Feature Release URLs,
SHA-256 values, source branches, source commits, and catalog checksum.

- [ ] **Step 5: Verify runtime update semantics**

Expected: no false Telegram update because installed and catalog versions are
equal; Core `latest` remains the image produced by `platform-v1.0.5`.
