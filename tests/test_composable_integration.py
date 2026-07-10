import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


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
            ["renaming.tvdb_episode", "renaming.generic_media", "open115.unorganized_fallback"],
        )
        self.assertEqual(
            [command.command for command in registry.bot_commands()],
            ["auth", "config", "magnet", "m", "q", "search", "s"],
        )

    def test_terminal_processor_prevents_unorganized_fallback(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent, ModuleRegistry, PostDownloadResult
        from app.modules.open115 import register_module as register_open115

        init.bot_config = {"media": {"unorganized_path": "/未整理"}}
        storage = Mock()
        registry = ModuleRegistry()
        register_open115(registry)
        registry.add_post_download_processor(
            lambda event: PostDownloadResult(True, final_path="/已整理", should_stop=True),
            priority=100,
            name="test.terminal",
        )

        result = registry.run_post_download_pipeline(
            DownloadCompletedEvent(
                link="magnet:?xt=urn:btih:" + "b" * 40,
                selected_path="/电影",
                user_id=1,
                final_path="/电影/Raw.Release",
                resource_name="Raw.Release",
                storage=storage,
            )
        )

        self.assertEqual(result.final_path, "/已整理")
        storage.move_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
