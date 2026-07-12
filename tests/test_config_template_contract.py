import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROUTE_KINDS = {
    "live_action_series",
    "live_action_movie",
    "animated_movie",
    "animated_series",
}


class ConfigTemplateContractTest(unittest.TestCase):
    def test_runtime_config_examples_are_identical_core_templates(self):
        root_source = (
            ROOT / "config" / "config.yaml.example"
        ).read_text(encoding="utf-8")
        app_source = (
            ROOT / "app" / "config.yaml.example"
        ).read_text(encoding="utf-8")
        self.assertEqual(root_source, app_source)

        parsed = yaml.safe_load(root_source)
        self.assertEqual(
            set(parsed),
            {"log_level", "bot_token", "allowed_user", "category_folder"},
        )
        routes = parsed["category_folder"]
        self.assertEqual({route["kind"] for route in routes}, ROUTE_KINDS)
        self.assertTrue(all(route.get("path") for route in routes))
        self.assertTrue(all("plex_library_id" in route for route in routes))

    def test_exactly_one_plex_business_config_snippet_exists(self):
        snippets = sorted(
            path.name
            for path in (ROOT / "config" / "modules").glob("*.yaml.example")
        )
        self.assertEqual(snippets, ["plex-management.yaml.example"])

        parsed = yaml.safe_load(
            (
                ROOT
                / "config"
                / "modules"
                / "plex-management.yaml.example"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(parsed),
            {"category_folder", "media", "metadata", "artwork", "ai"},
        )
        self.assertEqual(
            {route["kind"] for route in parsed["category_folder"]},
            ROUTE_KINDS,
        )
        self.assertEqual(set(parsed["media"]), {"plex"})
        self.assertEqual(set(parsed["metadata"]), {"tmdb"})
        self.assertEqual(set(parsed["artwork"]), {"fanart"})


if __name__ == "__main__":
    unittest.main()
