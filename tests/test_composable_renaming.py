import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


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

    def _event(self, storage):
        from app.core.module_registry import DownloadCompletedEvent

        return DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "a" * 40,
            selected_path="/电影",
            user_id=1,
            final_path="/电影/Raw.Release",
            resource_name="Test.Movie.2026.1080p",
            naming_metadata={
                "source": "douban",
                "chinese_title": "测试电影",
                "english_title": "Test Movie",
                "release_title": "Test.Movie.2026.1080p",
            },
            storage=storage,
        )

    def test_generic_rename_skips_multi_video_folder_without_deleting_source(self):
        import init
        from app.modules.renaming import process_generic_media

        init.logger = Mock()
        storage = Mock()
        storage.get_files_from_dir.return_value = ["part1.mkv", "part2.mkv"]

        result = process_generic_media(self._event(storage))

        self.assertFalse(result.handled)
        storage.create_dir_recursive.assert_not_called()
        storage.rename.assert_not_called()
        storage.move_file.assert_not_called()
        storage.delete_single_file.assert_not_called()

    def test_generic_rename_preserves_source_when_storage_operation_fails(self):
        import init
        from app.modules.renaming import process_generic_media

        init.logger = Mock()
        for failed_operation in ("create", "rename", "move"):
            with self.subTest(failed_operation=failed_operation):
                storage = Mock()
                storage.get_files_from_dir.return_value = ["Original.Name.mkv"]
                storage.create_dir_recursive.return_value = failed_operation != "create"
                storage.rename.return_value = failed_operation != "rename"
                storage.move_file.return_value = failed_operation != "move"

                with self.assertRaises(RuntimeError):
                    process_generic_media(self._event(storage))

                storage.delete_single_file.assert_not_called()

    def test_generic_rename_cleanup_failure_does_not_undo_success(self):
        import init
        from app.modules.renaming import process_generic_media

        init.logger = Mock()
        storage = Mock()
        storage.get_files_from_dir.return_value = ["Original.Name.mkv"]
        storage.create_dir_recursive.return_value = True
        storage.rename.return_value = True
        storage.move_file.return_value = True
        storage.delete_single_file.side_effect = RuntimeError("cleanup failed")

        result = process_generic_media(self._event(storage))

        self.assertTrue(result.handled)
        self.assertTrue(result.should_stop)
        init.logger.warn.assert_called()

    def test_tvdb_rename_cleanup_failure_does_not_undo_success(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules import renaming

        init.logger = Mock()
        init.bot_config = {
            "ai": {"api_url": "https://ai.example", "api_key": "key", "model": "model"}
        }
        storage = Mock()
        storage.create_dir_recursive.return_value = True
        storage.rename.return_value = True
        storage.move_file.return_value = True
        storage.delete_single_file.side_effect = RuntimeError("cleanup failed")
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "b" * 40,
            selected_path="/剧集",
            user_id=1,
            final_path="/剧集/Raw.Release",
            resource_name="Test.Show.S01E01",
            storage=storage,
        )
        plan = {
            "target_root": "/剧集/测试剧 (Test Show)",
            "tvdb_series_id": "100",
            "series_name": "Test Show",
            "operations": [
                {
                    "target_dir": "/剧集/测试剧 (Test Show)/Test Show Season 01",
                    "source_path": "/剧集/Raw.Release/Test.Show.S01E01.mkv",
                    "rename_to": "Test Show S01E01.mkv",
                    "renamed_source_path": "/剧集/Raw.Release/Test Show S01E01.mkv",
                }
            ],
            "warnings": [],
        }

        with unittest.mock.patch.object(
            renaming, "_get_tvdb_candidates_and_episodes", return_value=([{"tvdb_series_id": "100"}], [{}])
        ), unittest.mock.patch.object(
            renaming,
            "collect_storage_file_tree",
            return_value=[{"name": "Test.Show.S01E01.mkv", "relative_path": "Test.Show.S01E01.mkv", "is_dir": False}],
        ), unittest.mock.patch.object(
            renaming, "infer_tvdb_episode_plan_with_ai", return_value={}
        ), unittest.mock.patch.object(
            renaming, "build_tvdb_rename_plan", return_value=plan
        ):
            result = renaming._attempt_tvdb_ai_episode_rename(
                event,
                {"chinese_title": "测试剧", "english_title": "Test Show", "year": "2026"},
            )

        self.assertEqual(result, plan)
        init.logger.warn.assert_called()


if __name__ == "__main__":
    unittest.main()
