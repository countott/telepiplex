import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class ComposableRenamingModuleTest(unittest.TestCase):
    def test_confirmed_temporary_special_runs_when_tvdb_is_down(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules import renaming

        init.logger = Mock()
        init.bot_config = {
            "ai": {
                "api_url": "https://ai.example",
                "api_key": "key",
                "model": "model",
            },
            "media": {"unorganized_path": "/未整理"},
        }
        storage = Mock()
        storage.create_dir_recursive.return_value = True
        storage.rename.return_value = True
        storage.move_file.return_value = True
        storage.get_file_info.return_value = None
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "b" * 40,
            selected_path="/真人剧集",
            user_id=1,
            final_path="/真人剧集/Raw.Release",
            resource_name="Someday.or.One.Day.The.Movie.2022",
            metadata={
                "chinese_title": "想见你",
                "english_title": "Someday or One Day",
                "download_plan": {
                    "schema_version": 1,
                    "confirmed": True,
                    "relation": {"target_series_title": "Someday or One Day"},
                    "placement": {
                        "library_type": "series",
                        "season_number": 0,
                        "episode_number": 100,
                        "mapping_kind": "temporary_related_special",
                    },
                    "source_entry": {
                        "title": "想见你 (电影)",
                        "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    },
                },
            },
            storage=storage,
        )
        rename_plan = {
            "target_root": "/真人剧集/想见你 (Someday or One Day)",
            "series_name": "Someday or One Day",
            "operations": [
                {
                    "target_dir": "/真人剧集/想见你 (Someday or One Day)/Someday or One Day Season 00",
                    "source_path": "/真人剧集/Raw.Release/Movie.mkv",
                    "rename_to": "Someday or One Day S00E100.mkv",
                    "renamed_source_path": "/真人剧集/Raw.Release/Someday or One Day S00E100.mkv",
                }
            ],
            "unmatched_sources": ["Bonus.mkv"],
            "warnings": [],
        }

        with patch.object(
            renaming,
            "collect_storage_file_tree",
            return_value=[
                {"name": "Movie.mkv", "relative_path": "Movie.mkv", "is_dir": False},
                {"name": "Bonus.mkv", "relative_path": "Bonus.mkv", "is_dir": False},
            ],
        ), patch.object(
            renaming,
            "infer_tvdb_episode_plan_with_ai",
            return_value={
                "episode_map": [
                    {
                        "source_file": "Movie.mkv",
                        "season_number": 0,
                        "episode_number": 100,
                    }
                ]
            },
        ), patch.object(
            renaming, "build_confirmed_rename_plan", return_value=rename_plan
        ), patch.object(
            renaming, "_get_tvdb_candidates_and_episodes", return_value=([], [])
        ):
            result = renaming.process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertTrue(result.should_stop)
        storage.create_dir_recursive.assert_any_call("/未整理/Raw.Release")
        storage.move_file.assert_any_call(
            "/真人剧集/Raw.Release/Bonus.mkv", "/未整理/Raw.Release"
        )

    def test_confirmed_target_conflict_is_reported_before_any_move(self):
        from app.modules.renaming import (
            ConfirmedPlanConflict,
            _assert_no_target_conflicts,
        )

        storage = Mock()
        storage.get_file_info.return_value = {"file_id": "occupied"}
        rename_plan = {
            "operations": [
                {
                    "target_dir": "/真人剧集/想见你 (Someday or One Day)/Someday or One Day Season 00",
                    "rename_to": "Someday or One Day S00E100.mkv",
                }
            ]
        }

        with self.assertRaisesRegex(ConfirmedPlanConflict, "S00E100"):
            _assert_no_target_conflicts(storage, rename_plan)

        storage.rename.assert_not_called()
        storage.move_file.assert_not_called()

    def test_confirmed_mapping_failure_moves_source_directory_to_unorganized(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules import renaming

        init.logger = Mock()
        init.bot_config = {
            "ai": {
                "api_url": "https://ai.example",
                "api_key": "key",
                "model": "model",
            },
            "media": {"unorganized_path": "/未整理"},
        }
        storage = Mock()
        storage.create_dir_recursive.return_value = True
        storage.move_file.return_value = True
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "c" * 40,
            selected_path="/真人剧集",
            user_id=1,
            final_path="/真人剧集/Raw.Failed",
            resource_name="Raw.Failed",
            metadata={
                "download_plan": {
                    "schema_version": 1,
                    "confirmed": True,
                    "relation": {"target_series_title": "Someday or One Day"},
                    "placement": {
                        "library_type": "series",
                        "season_number": 0,
                        "episode_number": 100,
                        "mapping_kind": "temporary_related_special",
                    },
                    "source_entry": {
                        "title": "想见你 (电影)",
                        "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    },
                }
            },
            storage=storage,
        )

        with patch.object(
            renaming, "_attempt_tvdb_ai_episode_rename", return_value=None
        ):
            result = renaming.process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/未整理/Raw.Failed")
        storage.move_file.assert_called_once_with(
            "/真人剧集/Raw.Failed", "/未整理"
        )

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
