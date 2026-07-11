import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTest(unittest.TestCase):
    def test_plex_runtime_dependencies_are_installed_by_image(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("plexapi==4.18.0", requirements)
        self.assertIn("mcp>=1.26,<2", requirements)
        self.assertIn("uvicorn==0.40.0", requirements)
        self.assertIn("pip install -r requirements.txt", dockerfile)

    def test_plex_mcp_secrets_are_empty_in_committed_template(self):
        config = yaml.safe_load((ROOT / "config" / "config.yaml.example").read_text(encoding="utf-8"))

        self.assertEqual(config["media"]["plex"]["token"], "")
        self.assertEqual(config["media"]["plex"]["mcp"]["auth_token"], "")
        self.assertFalse(config["media"]["plex"]["mcp"]["enabled"])

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
