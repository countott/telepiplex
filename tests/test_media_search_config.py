import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class MediaSearchConfigTest(unittest.TestCase):
    def _module_config(self):
        return yaml.safe_load(
            (ROOT / "config/modules/media-search.yaml.example").read_text(
                encoding="utf-8"
            )
        )

    def test_module_snippet_has_exact_neutral_routes_and_search_services(self):
        payload = self._module_config()

        self.assertEqual(
            set(payload),
            {"category_folder", "search", "metadata", "ai"},
        )
        routes = payload["category_folder"]
        self.assertEqual(
            {item["kind"] for item in routes},
            {
                "live_action_series",
                "live_action_movie",
                "animated_movie",
                "animated_series",
            },
        )
        self.assertEqual(len(routes), 4)
        self.assertTrue(all(set(item) == {"kind", "path", "plex_library_id"} for item in routes))
        self.assertTrue(all(item["path"] for item in routes))
        self.assertTrue(all(item["plex_library_id"] == "" for item in routes))

        self.assertTrue(payload["search"]["enable"])
        self.assertIn("prowlarr", payload["search"])
        self.assertEqual(
            set(payload["metadata"]),
            {"wikipedia", "douban", "tvdb"},
        )
        wikipedia = payload["metadata"]["wikipedia"]
        self.assertTrue(wikipedia["enable"])
        self.assertEqual(wikipedia["languages"], ["zh", "en"])
        self.assertTrue(payload["metadata"]["douban"]["enable"])
        self.assertTrue(payload["metadata"]["tvdb"]["enable"])
        self.assertTrue(payload["ai"]["enable"])
        self.assertIn("api_url", payload["ai"])
        self.assertNotIn("base_url", payload["ai"])

    def test_full_templates_remain_identical_core_only_contracts(self):
        app_text = (ROOT / "app/config.yaml.example").read_text(encoding="utf-8")
        root_text = (ROOT / "config/config.yaml.example").read_text(encoding="utf-8")
        self.assertEqual(app_text, root_text)
        payload = yaml.safe_load(root_text)
        self.assertEqual(
            set(payload),
            {"log_level", "bot_token", "allowed_user", "category_folder"},
        )

    def test_media_search_module_declares_every_owned_section(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.media_search import register_module

        registry = ModuleRegistry()
        register_module(registry)
        self.assertEqual(
            registry.config_sections,
            [
                "search.prowlarr",
                "metadata.wikipedia",
                "metadata.douban",
                "metadata.tvdb",
                "ai",
            ],
        )


if __name__ == "__main__":
    unittest.main()
