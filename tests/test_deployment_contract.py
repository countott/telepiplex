import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTest(unittest.TestCase):
    def test_image_contains_only_core_runtime_and_plugin_toolchain(self):
        source = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY ./app /app", source)
        self.assertIn("COPY ./sdk /opt/telepiplex/sdk", source)
        self.assertIn("COPY ./tools /opt/telepiplex/tools", source)
        self.assertIn("RUN mkdir -p /config/plugins /tmp/telepiplex", source)
        self.assertIn('VOLUME ["/config"]', source)
        self.assertNotIn("ADD ./app .", source)
        self.assertNotIn("COPY ./examples", source)

    def test_compose_runs_one_core_service_with_persistent_config_only(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        self.assertEqual(list(compose["services"]), ["telepiplex-core"])
        service = compose["services"]["telepiplex-core"]
        self.assertEqual(service["image"], "telepiplex-core:latest")
        self.assertEqual(service["volumes"], ["/to/your/path/config:/config"])
        self.assertNotIn("ports", service)

    def test_core_documentation_describes_runtime_feature_contract(self):
        for name in ("README.md", "README_EN.md"):
            source = (ROOT / name).read_text(encoding="utf-8")
            for term in (
                "/plugin install",
                "name@version",
                ".tpx",
                "/config/plugins",
                "Feature",
            ):
                self.assertIn(term, source, f"{name}: {term}")

    def test_build_script_only_references_existing_dockerfiles(self):
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        dockerfiles = re.findall(r"docker\s+build\s+-f\s+([^\s]+)", source)

        self.assertTrue(dockerfiles)
        for dockerfile in dockerfiles:
            self.assertTrue((ROOT / dockerfile).is_file(), dockerfile)

    def test_build_script_outputs_the_compose_image(self):
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        service = next(iter(compose["services"].values()))
        image = service["image"]

        self.assertIn(f"-t {image}", source)
        self.assertIn(f"docker image inspect {image}", source)

    def test_documentation_describes_aggregate_release_contract(self):
        required = (
            "ghcr.io/<owner>/telepiplex-core",
            "platform-v1.0.0",
            "catalog.yaml",
            "open115",
            "media-search",
            "renaming",
            "plex-management",
            "不会静默更新",
        )
        chinese = (ROOT / "README.md").read_text(encoding="utf-8")
        for term in required:
            self.assertIn(term, chinese, term)

        english = (ROOT / "README_EN.md").read_text(encoding="utf-8")
        for term in (
            "ghcr.io/<owner>/telepiplex-core",
            "platform-v1.0.0",
            "catalog.yaml",
            "open115",
            "media-search",
            "renaming",
            "plex-management",
            "never updates silently",
        ):
            self.assertIn(term, english, term)

        decisions = (
            ROOT / "docs/todos/2026-07-12-business-module-decisions.md"
        ).read_text(encoding="utf-8")
        self.assertIn("OPS-TODO-01A GitHub 聚合发布（已实现）", decisions)
        self.assertIn("OPS-TODO-01B 远程更新发现（已实现）", decisions)
        self.assertIn("GitHub 聚合发布流水线已经落地", decisions)
        self.assertNotIn("GitHub 自动发布 Core 镜像、Feature `.tpx` 和远程 catalog 尚未落地", decisions)

    def test_documentation_describes_remote_update_discovery(self):
        remote_catalog = (
            "https://github.com/countott/telepiplex/releases/latest/"
            "download/catalog.yaml"
        )
        chinese = (ROOT / "README.md").read_text(encoding="utf-8")
        for term in (
            remote_catalog,
            "catalog_refresh_interval: 21600",
            "确认更新",
            "/config/plugins/catalog.yaml",
            "不会静默更新",
        ):
            self.assertIn(term, chinese, term)

        english = (ROOT / "README_EN.md").read_text(encoding="utf-8")
        for term in (
            remote_catalog,
            "catalog_refresh_interval: 21600",
            "Confirm update",
            "/config/plugins/catalog.yaml",
            "never updates silently",
        ):
            self.assertIn(term, english, term)


if __name__ == "__main__":
    unittest.main()
