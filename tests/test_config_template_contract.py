import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConfigTemplateContractTest(unittest.TestCase):
    def test_category_routes_cover_exactly_four_kinds(self):
        import yaml

        parsed = yaml.safe_load(
            (ROOT / "config" / "config.yaml.example").read_text(encoding="utf-8")
        )
        routes = parsed["category_folder"]

        self.assertEqual(
            {item["kind"] for item in routes},
            {
                "live_action_series",
                "live_action_movie",
                "animated_movie",
                "animated_series",
            },
        )
        self.assertTrue(all(item.get("path") for item in routes))
        self.assertTrue(all("plex_library_id" in item for item in routes))

    def test_runtime_config_examples_are_identical_full_templates(self):
        root_template = (ROOT / "config" / "config.yaml.example").read_text(encoding="utf-8")
        app_template = (ROOT / "app" / "config.yaml.example").read_text(encoding="utf-8")

        self.assertEqual(root_template, app_template)
        for required in (
            "modules:",
            "enabled: all",
            "115_app_id:",
            "access_token:",
            "refresh_token:",
            "open115:",
            "search:",
            "prowlarr:",
            "metadata:",
            "tvdb:",
            "media:",
            "unorganized_path:",
            "plex:",
            "management:",
            "database_path:",
            "mcp:",
            "auth_token:",
            "tmdb:",
            "fanart:",
            "ai:",
            "api_url:",
        ):
            self.assertIn(required, root_template)
        self.assertIn('api_url: ""', root_template)
        self.assertNotIn('ai:\n  enable: false\n  api_key: ""\n  base_url: ""', root_template)

        import yaml

        parsed = yaml.safe_load(root_template)
        self.assertTrue(all("plex_library_id" in item for item in parsed["category_folder"]))
        self.assertEqual(parsed["media"]["plex"]["mcp"]["path"], "/mcp")
        self.assertEqual(parsed["media"]["plex"]["ai"]["max_tool_rounds"], 3)

    def test_feature_config_is_not_split_into_module_snippets(self):
        module_snippets = sorted((ROOT / "config" / "modules").glob("*.yaml.example"))

        self.assertEqual(module_snippets, [])


if __name__ == "__main__":
    unittest.main()
