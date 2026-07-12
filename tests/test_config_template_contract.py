import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CATEGORY_KINDS = {
    "live_action_series",
    "live_action_movie",
    "animated_movie",
    "animated_series",
}
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
)


def assert_four_routes(test_case, routes):
    test_case.assertEqual(len(routes), 4)
    test_case.assertEqual({item["kind"] for item in routes}, CATEGORY_KINDS)
    test_case.assertTrue(all(item.get("path") for item in routes))
    test_case.assertTrue(all("plex_library_id" in item for item in routes))


class ConfigTemplateContractTest(unittest.TestCase):
    def test_runtime_templates_are_identical_core_only_contracts(self):
        app_source = (ROOT / "app/config.yaml.example").read_text(encoding="utf-8")
        runtime_source = (ROOT / "config/config.yaml.example").read_text(encoding="utf-8")

        self.assertEqual(app_source, runtime_source)
        parsed = yaml.safe_load(runtime_source)
        self.assertIn("bot_token", parsed)
        self.assertIn("allowed_user", parsed)
        assert_four_routes(self, parsed["category_folder"])
        for term in BUSINESS_TERMS:
            self.assertNotIn(term, parsed)

    def test_core_module_snippet_contains_only_modules_and_four_routes(self):
        parsed = yaml.safe_load(
            (ROOT / "config/modules/core.yaml.example").read_text(encoding="utf-8")
        )

        self.assertEqual(set(parsed), {"modules", "category_folder"})
        self.assertEqual(parsed["modules"], {"enabled": []})
        assert_four_routes(self, parsed["category_folder"])


if __name__ == "__main__":
    unittest.main()
