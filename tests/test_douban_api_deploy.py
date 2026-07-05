import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DoubanApiDeployTest(unittest.TestCase):
    def test_unraid_installer_applies_detail_patch_with_legacy_backup(self):
        installer = (ROOT / "deploy" / "douban-api" / "install-unraid.sh").read_text(encoding="utf-8")
        patch = (ROOT / "deploy" / "douban-api" / "patches" / "detail.js").read_text(encoding="utf-8")

        self.assertIn("models/detail.legacy.js", installer)
        self.assertIn("patches/detail.js", installer)
        self.assertIn("subject_abstract", patch)
        self.assertIn("rexxar/api/v2/movie", patch)
        self.assertIn("legacy._detail", patch)


if __name__ == "__main__":
    unittest.main()
