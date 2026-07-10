import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTest(unittest.TestCase):
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
