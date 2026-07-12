import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class Composable115ModuleTest(unittest.TestCase):
    def test_auto_clean_all_returns_deleted_file_summary(self):
        import init
        from app.core.open_115 import OpenAPI_115

        init.logger = Mock()
        init.bot_config = {
            "clean_policy": {"switch": "on", "less_than": "400M"}
        }
        api = object.__new__(OpenAPI_115)
        api.get_file_info = Mock(return_value={"file_id": "root"})
        api.find_all_junk_files = Mock(return_value=[
            {"fid": "1", "fn": "sample.mkv", "pid": "root"},
            {"fid": "2", "fn": "subtitle.srt", "pid": "root"},
        ])
        api._batch_delete_files = Mock(return_value=True)

        summary = api.auto_clean_all("/电影/Raw.Release")

        self.assertEqual(summary, {
            "count": 2,
            "files": ["sample.mkv", "subtitle.srt"],
        })
        api._batch_delete_files.assert_called_once_with(["1", "2"])

    def test_open115_module_registers_provider_and_commands(self):
        from app.core.module_registry import ModuleRegistry
        from app.modules.open115 import register_module

        registry = ModuleRegistry()
        register_module(registry)

        self.assertEqual(
            [command.command for command in registry.bot_commands()],
            ["auth", "config", "magnet", "m", "q"],
        )
        self.assertIsNotNone(registry.download_provider)
        self.assertIsNotNone(registry.storage_provider)
        self.assertEqual(registry.config_sections, ["115", "open115"])
        self.assertEqual(
            [item.name for item in registry.post_download_processors],
            ["open115.unorganized_fallback"],
        )

    def test_unorganized_fallback_moves_unhandled_download(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules.open115 import process_unorganized_fallback

        init.bot_config = {"media": {"unorganized_path": "/未整理"}}
        storage = Mock()
        storage.create_dir_recursive.return_value = True
        storage.move_file.return_value = True
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "a" * 40,
            selected_path="/电影",
            user_id=1,
            final_path="/电影/Raw.Release",
            resource_name="Raw.Release",
            storage=storage,
        )

        result = process_unorganized_fallback(event)

        storage.create_dir_recursive.assert_called_once_with("/未整理")
        storage.move_file.assert_called_once_with("/电影/Raw.Release", "/未整理")
        self.assertTrue(result.handled)
        self.assertTrue(result.should_stop)
        self.assertEqual(result.final_path, "/未整理/Raw.Release")

    def test_core_entrypoint_does_not_import_115_handlers_directly(self):
        source = (ROOT / "app" / "115bot.py").read_text(encoding="utf-8")

        for symbol in (
            "register_auth_handlers",
            "register_config_handlers",
            "register_download_handlers",
            "initialize_115open",
        ):
            self.assertNotIn(symbol, source)


if __name__ == "__main__":
    unittest.main()
