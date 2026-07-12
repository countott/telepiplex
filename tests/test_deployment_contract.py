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


if __name__ == "__main__":
    unittest.main()
