import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CORE_WORKFLOW = ROOT / ".github/workflows/release.yml"
FEATURE_WORKFLOW = ROOT / ".github/workflows/release-feature.yml"
OLD_WORKFLOW = ROOT / ".github/workflows/docker-build.yml"


class ReleaseWorkflowTest(unittest.TestCase):
    def _workflow(self, path):
        self.assertTrue(path.is_file(), f"workflow is missing: {path.name}")
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        # PyYAML's YAML 1.1 resolver treats the Actions key `on` as True.
        if True in workflow:
            workflow["on"] = workflow.pop(True)
        return workflow

    def _step(self, workflow, job, name):
        return next(
            step
            for step in workflow["jobs"][job]["steps"]
            if step.get("name") == name
        )

    def test_core_release_is_core_tag_or_manual_only(self):
        workflow = self._workflow(CORE_WORKFLOW)
        triggers = workflow["on"]

        self.assertEqual(set(triggers), {"push", "workflow_dispatch"})
        self.assertEqual(triggers["push"]["tags"], ["core-v*"])
        self.assertNotIn("branches", triggers["push"])
        self.assertNotIn("pull_request", triggers)
        self.assertIn("release_tag", triggers["workflow_dispatch"]["inputs"])
        self.assertIn("concurrency", workflow)

    def test_core_release_tests_and_pushes_only_version_and_latest(self):
        workflow = self._workflow(CORE_WORKFLOW)
        jobs = workflow["jobs"]
        source = CORE_WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(set(jobs), {"validate-core", "build-core-image"})
        self.assertNotIn("build-features", jobs)
        self.assertNotIn("publish-release", jobs)
        self.assertEqual(workflow["permissions"]["contents"], "read")
        self.assertEqual(workflow["permissions"]["packages"], "write")

        validate = self._step(
            workflow, "validate-core", "Validate immutable Core tag"
        )["run"]
        self.assertIn("^core-v", validate)
        self.assertNotIn("platform-v", validate)
        self._step(workflow, "validate-core", "Run Core tests")
        self._step(workflow, "validate-core", "Compile tracked Python")

        build = jobs["build-core-image"]
        self.assertEqual(build["needs"], "validate-core")
        refuse = self._step(
            workflow, "build-core-image", "Refuse an existing immutable image tag"
        )["run"]
        self.assertIn('VERSION="${RELEASE_TAG#core-v}"', refuse)
        self.assertIn("docker buildx imagetools inspect", refuse)

        publish = self._step(
            workflow, "build-core-image", "Build and push Core image"
        )["with"]
        self.assertEqual(publish["platforms"], "linux/amd64")
        self.assertTrue(publish["push"])
        self.assertEqual(
            set(publish["tags"].splitlines()),
            {
                "${{ env.CORE_IMAGE }}:${{ steps.version.outputs.version }}",
                "${{ env.CORE_IMAGE }}:latest",
            },
        )
        self.assertNotIn("gh release create", source)
        self.assertNotIn(".tpx", source)

    def test_feature_release_accepts_fixed_tag_families_and_is_serialized(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        triggers = workflow["on"]

        self.assertEqual(
            triggers["push"]["tags"],
            [
                "open115-v*",
                "media-search-v*",
                "renaming-v*",
                "plex-management-v*",
            ],
        )
        self.assertIn("release_tag", triggers["workflow_dispatch"]["inputs"])
        self.assertEqual(workflow["concurrency"]["group"], "feature-catalog-release")
        self.assertIs(workflow["concurrency"]["cancel-in-progress"], False)
        self.assertEqual(workflow["permissions"]["contents"], "write")

    def test_feature_release_maps_tag_to_branch_and_verifies_identity(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        resolve = self._step(
            workflow, "publish-feature", "Resolve immutable Feature identity"
        )["run"]
        checkout = self._step(
            workflow, "publish-feature", "Checkout fixed Feature branch"
        )["with"]
        verify = self._step(
            workflow, "publish-feature", "Build or reuse verified Feature artifact"
        )["run"]

        self.assertIn("parse_feature_tag", resolve)
        self.assertIn("FEATURE_BRANCHES", resolve)
        self.assertEqual(checkout["ref"], "${{ steps.feature.outputs.branch }}")
        self.assertIn("manifest.yaml", verify)
        self.assertIn("PLUGIN_ID", verify)
        self.assertIn("VERSION", verify)
        self.assertIn("FEATURE_BRANCH", verify)
        self.assertIn("FEATURE_COMMIT", verify)
        self.assertIn("verify_tpx", verify)

    def test_feature_release_bootstraps_catalog_and_reuses_previous_asset_bytes(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        bootstrap = self._step(
            workflow, "publish-feature", "Load previous catalog snapshot"
        )["run"]
        build_or_reuse = self._step(
            workflow, "publish-feature", "Build or reuse verified Feature artifact"
        )["run"]

        self.assertIn("refs/heads/catalog", bootstrap)
        self.assertIn("releases/latest/download/catalog.yaml", bootstrap)
        self.assertIn("previous/catalog.yaml", bootstrap)
        self.assertIn("previous catalog is empty", bootstrap)

        self.assertIn("previous/catalog.yaml", build_or_reuse)
        self.assertIn('["url"]', build_or_reuse)
        self.assertIn("curl", build_or_reuse)
        self.assertIn("EXPECTED_SHA256", build_or_reuse)
        self.assertIn("sha256", build_or_reuse)
        self.assertIn("tools/build_feature.py", build_or_reuse)
        self.assertIn("verify_tpx", build_or_reuse)

    def test_feature_release_carries_complete_catalog_then_publishes_branch(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        steps = workflow["jobs"]["publish-feature"]["steps"]
        names = [step.get("name") for step in steps]
        generate = self._step(
            workflow, "publish-feature", "Write merged catalog snapshot"
        )["run"]
        feature_release_step = self._step(
            workflow, "publish-feature", "Create immutable Feature Release"
        )["run"]
        catalog_publish_step = self._step(
            workflow, "publish-feature", "Publish catalog branch"
        )["run"]
        catalog_prepare_step = self._step(
            workflow, "publish-feature", "Prepare catalog branch commit"
        )["run"]

        self.assertIn("write_feature_catalog", generate)
        self.assertIn("catalog.yaml.sha256", generate)
        self.assertIn("*.tpx", feature_release_step)
        self.assertIn("catalog.yaml", feature_release_step)
        self.assertIn("catalog.yaml.sha256", feature_release_step)
        self.assertIn("gh release view", feature_release_step)
        self.assertIn("cmp", feature_release_step)
        self.assertIn("git -C catalog-publish add --all", catalog_prepare_step)
        self.assertIn("git push origin HEAD:catalog", catalog_publish_step)
        self.assertLess(
            names.index("Create immutable Feature Release"),
            names.index("Publish catalog branch"),
        )
        self.assertFalse(OLD_WORKFLOW.exists(), "unsafe legacy workflow still exists")

    def test_workflows_install_local_wheel_build_backends(self):
        core = self._workflow(CORE_WORKFLOW)
        feature = self._workflow(FEATURE_WORKFLOW)
        core_install = self._step(
            core, "validate-core", "Install Core test dependencies"
        )["run"]
        feature_install = self._step(
            feature, "publish-feature", "Install Feature release dependencies"
        )["run"]
        for package in ("setuptools", "wheel"):
            self.assertIn(package, core_install)
            self.assertIn(package, feature_install)


if __name__ == "__main__":
    unittest.main()
