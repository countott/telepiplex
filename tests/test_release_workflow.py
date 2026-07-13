import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"
OLD_WORKFLOW = ROOT / ".github/workflows/docker-build.yml"


class ReleaseWorkflowTest(unittest.TestCase):
    def _workflow(self):
        self.assertTrue(WORKFLOW.is_file(), "release workflow is missing")
        return yaml.load(
            WORKFLOW.read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )

    def test_release_is_tag_or_manual_only(self):
        workflow = self._workflow()
        triggers = workflow["on"]

        self.assertEqual(set(triggers), {"push", "workflow_dispatch"})
        self.assertEqual(triggers["push"]["tags"], ["platform-v*"])
        self.assertNotIn("branches", triggers["push"])
        self.assertNotIn("pull_request", triggers)
        self.assertIn("release_tag", triggers["workflow_dispatch"]["inputs"])
        self.assertIn("concurrency", workflow)

    def test_jobs_pin_ghcr_amd64_and_all_feature_branches(self):
        workflow = self._workflow()
        jobs = workflow["jobs"]

        self.assertEqual(
            set(jobs),
            {
                "validate-core",
                "build-features",
                "build-core-image",
                "publish-release",
            },
        )
        self.assertEqual(workflow["permissions"]["contents"], "write")
        self.assertEqual(workflow["permissions"]["packages"], "write")

        matrix = jobs["build-features"]["strategy"]["matrix"]["include"]
        refs = {item["plugin"]: item["ref"] for item in matrix}
        self.assertEqual(refs, {
            "open115": "feature/115",
            "media-search": "feature/media-search",
            "renaming": "feature/renaming",
            "plex-management": "feature/plex-management",
        })

        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ghcr.io/", source)
        self.assertIn("/telepiplex-core", source)
        self.assertIn("platforms: linux/amd64", source)
        self.assertNotIn("docker.io", source)
        self.assertNotIn("DOCKER_USERNAME", source)
        self.assertNotIn("DOCKER_PASSWORD", source)

    def test_publish_waits_for_all_builds_and_does_not_overwrite(self):
        workflow = self._workflow()
        publish = workflow["jobs"]["publish-release"]

        self.assertEqual(
            set(publish["needs"]),
            {"validate-core", "build-features", "build-core-image"},
        )
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("gh release view", source)
        self.assertIn("gh release create", source)
        self.assertNotIn("--clobber", source)
        self.assertIn("generate_release_catalog.py", source)
        self.assertFalse(OLD_WORKFLOW.exists(), "unsafe legacy workflow still exists")

    def test_test_and_feature_jobs_install_local_wheel_build_backends(self):
        workflow = self._workflow()
        jobs = workflow["jobs"]

        core_install = next(
            step["run"]
            for step in jobs["validate-core"]["steps"]
            if step["name"] == "Install Core test dependencies"
        )
        feature_install = next(
            step["run"]
            for step in jobs["build-features"]["steps"]
            if step["name"] == "Install Feature build dependencies"
        )
        for package in ("setuptools", "wheel"):
            self.assertIn(package, core_install)
            self.assertIn(package, feature_install)


if __name__ == "__main__":
    unittest.main()
