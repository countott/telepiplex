# Core GitHub Latest Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every successful `core-v<semver>` image publication create the same-tag GitHub Release and explicitly mark it Latest, without attaching catalog assets.

**Architecture:** Extend the tag-only Core workflow with a write-scoped Release job that runs after validation and GHCR publication. Keep the rolling Feature catalog on the `catalog` branch, and remove the Feature workflow's obsolete dependency on a Latest Platform Release while retaining catalog snapshots on each Feature Release.

**Tech Stack:** GitHub Actions YAML, GitHub CLI, Python 3.12 `unittest`, PyYAML, GHCR OCI registry.

## Global Constraints

- `core-v*` is the only Core release trigger.
- Core publishes `ghcr.io/<owner>/telepiplex-core:<semver>` and `:latest` before creating the GitHub Release.
- The GitHub Release tag and title equal `core-v<semver>` and use `--latest` explicitly.
- Core Releases contain no `catalog.yaml`, `.tpx`, or other assets.
- Feature Releases continue using `--latest=false`.
- The first publication under this contract is immutable `core-v1.0.7`; do not move `core-v1.0.6`.

---

### Task 1: Publish a same-tag Latest Core Release

**Files:**
- Modify: `tests/test_release_workflow.py`
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `RELEASE_TAG=${{ github.ref_name }}`, `CORE_IMAGE`, `GITHUB_SHA`, and the successful `build-core-image` job.
- Produces: a public GitHub Release whose tag/title equal `RELEASE_TAG`, with no assets and with Latest explicitly selected.

- [x] **Step 1: Replace the old image-only workflow test with the failing Release contract**

```python
def test_core_release_tests_pushes_image_and_publishes_latest_release(self):
    workflow = self._workflow(CORE_WORKFLOW)
    jobs = workflow["jobs"]
    source = CORE_WORKFLOW.read_text(encoding="utf-8")

    self.assertEqual(
        set(jobs), {"validate-core", "build-core-image", "publish-core-release"}
    )
    self.assertNotIn("build-features", jobs)
    self.assertEqual(workflow["permissions"], {"contents": "read"})

    build = jobs["build-core-image"]
    self.assertEqual(build["needs"], "validate-core")
    self.assertEqual(
        build["permissions"], {"contents": "read", "packages": "write"}
    )
    image = self._step(
        workflow, "build-core-image", "Build and push Core image"
    )["with"]
    self.assertEqual(
        set(image["tags"].splitlines()),
        {
            "${{ env.CORE_IMAGE }}:${{ steps.version.outputs.version }}",
            "${{ env.CORE_IMAGE }}:latest",
        },
    )

    release = jobs["publish-core-release"]
    self.assertEqual(
        release["needs"], ["validate-core", "build-core-image"]
    )
    self.assertEqual(release["permissions"], {"contents": "write"})
    self._step(workflow, "publish-core-release", "Refuse an existing Core Release")
    create = self._step(
        workflow, "publish-core-release", "Create GitHub Latest Release"
    )["run"]
    self.assertIn('gh release create "$RELEASE_TAG"', create)
    self.assertIn('--title "$RELEASE_TAG"', create)
    self.assertIn("--verify-tag", create)
    self.assertIn("--latest", create)
    self.assertNotIn("catalog.yaml", create)
    self.assertNotIn(".tpx", source)
```

- [x] **Step 2: Run the focused test and verify RED**

Run: `PYTHONPATH=.:sdk/src python -m unittest tests.test_release_workflow.ReleaseWorkflowTest.test_core_release_tests_pushes_image_and_publishes_latest_release`

Expected: FAIL because `publish-core-release` is absent and workflow permissions still include `packages: write` globally.

- [x] **Step 3: Add least-privilege job permissions and the Release job**

```yaml
permissions:
  contents: read

jobs:
  build-core-image:
    needs: validate-core
    permissions:
      contents: read
      packages: write

  publish-core-release:
    needs:
      - validate-core
      - build-core-image
    permissions:
      contents: write
    runs-on: ubuntu-latest
    steps:
      - name: Refuse an existing Core Release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          if gh release view "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
            echo "Core Release already exists: $RELEASE_TAG" >&2
            exit 1
          fi

      - name: Write Core release notes
        run: |
          VERSION="${RELEASE_TAG#core-v}"
          {
            echo "# Telepiplex Core $VERSION"
            echo
            echo "- Image: \`$CORE_IMAGE:$VERSION\`"
            echo "- Commit: \`$GITHUB_SHA\`"
            echo "- Platform: \`linux/amd64\`"
          } > release-notes.md

      - name: Create GitHub Latest Release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "$RELEASE_TAG" \
            --repo "$GITHUB_REPOSITORY" \
            --verify-tag \
            --title "$RELEASE_TAG" \
            --notes-file release-notes.md \
            --latest
```

- [x] **Step 4: Run the focused test and verify GREEN**

Run: `PYTHONPATH=.:sdk/src python -m unittest tests.test_release_workflow.ReleaseWorkflowTest.test_core_release_tests_pushes_image_and_publishes_latest_release`

Expected: PASS.

---

### Task 2: Remove the Latest Platform catalog dependency

**Files:**
- Modify: `tests/test_release_workflow.py`
- Modify: `.github/workflows/release-feature.yml`
- Modify: `tests/test_deployment_contract.py`
- Modify: `README.md`
- Modify: `README_EN.md`

**Interfaces:**
- Consumes: the confirmed `catalog` branch snapshot after optimistic publication.
- Produces: refreshed catalog assets on the current Feature Release only; Core Latest remains asset-free and Feature publication no longer assumes a `platform-v*` Latest Release.

- [x] **Step 1: Replace the Platform compatibility test with a failing Feature-only synchronization contract**

```python
def test_feature_release_catalog_assets_converge_after_catalog_push(self):
    workflow = self._workflow(FEATURE_WORKFLOW)
    sync = self._step(
        workflow, "publish-feature", "Synchronize Feature Release catalog assets"
    )["run"]

    self.assertIn('gh release upload "$RELEASE_TAG"', sync)
    self.assertIn("--clobber", sync)
    self.assertIn("catalog.yaml", sync)
    self.assertIn("catalog.yaml.sha256", sync)
    self.assertIn("for SYNC_ATTEMPT in 1 2 3 4 5", sync)
    self.assertIn("cmp", sync)
    self.assertNotIn("releases/latest", sync)
    self.assertNotIn("LATEST_TAG", sync)
    self.assertNotIn("platform-v", sync)
```

Update the deployment documentation contract to require `core-v1.0.7`, the raw `catalog` branch URL, and language stating that every Core Release is Latest. Remove the requirement for `releases/latest/download/catalog.yaml`.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=.:sdk/src python -m unittest tests.test_release_workflow.ReleaseWorkflowTest.test_feature_release_catalog_assets_converge_after_catalog_push tests.test_deployment_contract.DeploymentContractTest.test_documentation_describes_independent_release_contract`

Expected: FAIL because the Feature synchronization step still targets Latest Platform and the documentation still describes `core-v1.0.6`/Platform Latest.

- [x] **Step 3: Keep only current-Feature catalog synchronization**

Rename the workflow step to `Synchronize Feature Release catalog assets`. Preserve its five-attempt fetch/upload/refetch/compare loop and this upload:

```bash
gh release upload "$RELEASE_TAG" compatibility/before/catalog.yaml compatibility/before/catalog.yaml.sha256 \
  --repo "$GITHUB_REPOSITORY" --clobber
```

Delete the `releases/latest` API request, `platform-v*` validator, `LATEST_TAG`, and upload to the Latest Platform Release.

- [x] **Step 4: Update the operator documentation**

Document `core-v1.0.7`, state that Core publishes both GHCR tags and a same-tag GitHub Release explicitly marked Latest, and state that Core Releases carry no Feature/catalog assets. Explain that `https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml` is the rolling catalog endpoint and that Feature Releases remain `--latest=false`.

- [x] **Step 5: Run focused workflow and documentation tests and verify GREEN**

Run: `PYTHONPATH=.:sdk/src python -m unittest tests.test_release_workflow tests.test_deployment_contract`

Expected: PASS.

- [x] **Step 6: Commit the implementation**

```bash
git add .github/workflows/release.yml .github/workflows/release-feature.yml tests/test_release_workflow.py tests/test_deployment_contract.py README.md README_EN.md docs/superpowers/plans/2026-07-15-core-github-latest-release.md
git commit -m "feat(release): publish Core as GitHub Latest"
```

---

### Task 3: Verify and publish Core 1.0.7

**Files:**
- Verify only: the complete Core worktree and remote release surfaces.

**Interfaces:**
- Consumes: the verified `feature/telepiplex-core` head, including `7cbe5b6`.
- Produces: remote branch, immutable `core-v1.0.7`, successful Actions run, matching GHCR `1.0.7`/`latest`, and GitHub Latest Release `core-v1.0.7`.

- [x] **Step 1: Run the full local verification matrix**

Run: `PYTHONPATH=.:sdk/src python -m pytest -q`

Run: `PYTHONPATH=.:sdk/src python -m compileall -q app sdk tools tests`

Run: `git diff --check`

Expected: all commands exit 0 and pytest reports zero failures.

- [ ] **Step 2: Push the Core branch**

Run: `git push origin feature/telepiplex-core`

Expected: remote branch advances to the verified local head.

- [ ] **Step 3: Create and push the immutable release tag**

Run: `git tag -a core-v1.0.7 -m "core 1.0.7"`

Run: `git push origin refs/tags/core-v1.0.7`

Expected: GitHub starts `Publish Telepiplex Core image` for `core-v1.0.7`.

- [ ] **Step 4: Verify all remote outcomes**

Use the GitHub Actions API to require a completed successful `core-v1.0.7` run. Use the GHCR v2 API to require identical `Docker-Content-Digest` values for `1.0.7` and `latest`. Use the GitHub Releases API to require `releases/latest.tag_name == core-v1.0.7`, no release assets, and `target_commitish` resolving to the tagged Core commit.

Expected: every remote assertion passes.
