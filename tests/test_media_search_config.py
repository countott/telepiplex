import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class MediaSearchConfigTest(unittest.TestCase):
    def test_both_templates_expose_wikipedia_soft_provider(self):
        for path in (
            ROOT / "app/config.yaml.example",
            ROOT / "config/config.yaml.example",
        ):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            wikipedia = payload["metadata"]["wikipedia"]
            self.assertTrue(wikipedia["enable"])
            self.assertEqual(wikipedia["languages"], ["zh", "en"])
            self.assertEqual(wikipedia["timeout"], 10)
            self.assertTrue(payload["ai"]["enable"])

    def test_media_search_module_declares_wikipedia_section(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.media_search import register_module

        registry = ModuleRegistry()
        register_module(registry)
        self.assertIn("metadata.wikipedia", registry.config_sections)


if __name__ == "__main__":
    unittest.main()
