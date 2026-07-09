import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class ComposableRenamingModuleTest(unittest.TestCase):
    def test_renaming_module_registers_post_download_processors(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.renaming import register_module

        registry = ModuleRegistry()
        register_module(registry)

        self.assertEqual(
            [item.name for item in registry.post_download_processors],
            ["renaming.tvdb_episode", "renaming.generic_media"],
        )
        self.assertEqual(registry.config_sections, ["media", "metadata.tvdb", "ai"])


if __name__ == "__main__":
    unittest.main()
