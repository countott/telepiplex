import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_TERMS = (
    "115_app_id",
    "access_token",
    "refresh_token",
    "open115",
    "search",
    "prowlarr",
    "media",
    "metadata",
    "tvdb",
    "plex",
    "ai",
    "category_folder",
    "modules",
)


class ConfigTemplateContractTest(unittest.TestCase):
    def test_runtime_templates_are_identical_core_only_contracts(self):
        app_source = (ROOT / "app/config.yaml.example").read_text(encoding="utf-8")
        runtime_source = (ROOT / "config/config.yaml.example").read_text(encoding="utf-8")

        self.assertEqual(app_source, runtime_source)
        parsed = yaml.safe_load(runtime_source)
        self.assertEqual(
            set(parsed),
            {"log_level", "bot_token", "allowed_user", "plugins"},
        )
        self.assertEqual(parsed["plugins"]["root"], "/config/plugins")
        self.assertEqual(
            parsed["plugins"]["catalog"],
            "https://github.com/countott/telepiplex/releases/latest/download/catalog.yaml",
        )
        self.assertEqual(parsed["plugins"]["catalog_refresh_interval"], 21600)
        for term in BUSINESS_TERMS:
            self.assertNotIn(term, parsed)

    def test_legacy_core_module_snippet_is_removed(self):
        self.assertFalse((ROOT / "config/modules/core.yaml.example").exists())


if __name__ == "__main__":
    unittest.main()
