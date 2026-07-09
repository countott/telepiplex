import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class ComposableIntegrationTest(unittest.TestCase):
    def test_all_modules_register_without_rewriting_core_entrypoint(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.media_search import register_module as register_media_search
        from app.modules.open115 import register_module as register_open115
        from app.modules.renaming import register_module as register_renaming

        registry = ModuleRegistry()
        for register in (register_open115, register_media_search, register_renaming):
            register(registry)

        self.assertIsNotNone(registry.download_provider)
        self.assertIsNotNone(registry.storage_provider)
        self.assertEqual(
            [item.name for item in registry.post_download_processors],
            ["renaming.tvdb_episode", "renaming.generic_media"],
        )
        self.assertEqual(
            [command.command for command in registry.bot_commands()],
            ["auth", "config", "magnet", "m", "q", "search", "s"],
        )


if __name__ == "__main__":
    unittest.main()
