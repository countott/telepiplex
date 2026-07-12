import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class ComposableRenamingModuleTest(unittest.TestCase):
    def _temporary_media_metadata(self):
        return {
            "schema_version": 1,
            "metadata_id": "metadata-a",
            "confirmed": True,
            "identity": {
                "chinese_title": "想见你",
                "english_title": "Someday or One Day The Movie",
                "year": "2022",
                "content_kind": "extension_movie",
                "external_ids": {},
            },
            "relation": {
                "type": "sequel",
                "target_series": {
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day",
                    "year": "2019",
                    "external_ids": {},
                },
                "source": "wikipedia",
            },
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": 0,
                "episode_number": 100,
                "mapping_kind": "temporary_related_special",
                "mapping_source": "local_allocator",
                "tvdb_episode_id": "",
            },
            "source_entry": {
                "title": "想见你 (电影)",
                "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
            },
            "items": [],
            "evidence": {},
            "warnings": [],
        }

    def _standalone_media_metadata(self, category_kind, library_type):
        is_series = library_type == "series"
        return {
            "schema_version": 1,
            "metadata_id": f"metadata-{category_kind}",
            "confirmed": True,
            "identity": {
                "chinese_title": "测试影视",
                "english_title": "Test Media",
                "year": "2026",
                "content_kind": "series" if is_series else "movie",
                "external_ids": {},
            },
            "relation": {"type": "primary", "target_series": {}, "source": "user"},
            "placement": {
                "library_type": library_type,
                "category_kind": category_kind,
                "season_number": None,
                "episode_number": None,
                "mapping_kind": "standalone",
                "mapping_source": "user",
                "tvdb_episode_id": "",
            },
            "source_entry": {},
            "items": ([{
                "item_id": "episode-1",
                "content_role": "main_episode",
                "season_number": 1,
                "episode_number": 1,
            }] if is_series else []),
            "evidence": {},
            "warnings": [],
        }

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
                "media_metadata": self._temporary_media_metadata(),
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
                    "content_role": "extension_movie",
                    "season_number": 0,
                    "episode_number": 100,
                    "source_relative_path": "Movie.mkv",
                    "target_relative_path": "Someday or One Day Season 00/Someday or One Day S00E100.mkv",
                    "final_path": "/真人剧集/想见你 (Someday or One Day)/Someday or One Day Season 00/Someday or One Day S00E100.mkv",
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
        self.assertEqual(
            result.metadata["media_metadata"]["metadata_id"],
            "metadata-a",
        )
        self.assertTrue(
            result.metadata["media_metadata"]["items"][0]["final_path"].endswith(
                "S00E100.mkv"
            )
        )
        self.assertNotIn("_".join(("download", "plan")), result.metadata)
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
            metadata={"media_metadata": self._temporary_media_metadata()},
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

    def test_present_invalid_media_metadata_stops_without_legacy_inference(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules import renaming

        init.logger = Mock()
        init.bot_config = {
            "ai": {
                "api_url": "https://ai.example",
                "api_key": "key",
                "model": "model",
            }
        }
        storage = Mock()
        event_metadata = {
            "english_title": "Legacy Fallback Bait",
            "media_metadata": {"schema_version": 999, "confirmed": True},
        }
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "d" * 40,
            selected_path="/真人剧集",
            user_id=1,
            final_path="/真人剧集/Raw.Invalid",
            resource_name="Legacy.Fallback.Bait.S01E01",
            metadata=event_metadata,
            storage=storage,
        )

        with patch.object(
            renaming,
            "_attempt_legacy_tvdb_ai_episode_rename",
            return_value=None,
        ) as legacy_attempt:
            result = renaming.process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertTrue(result.should_stop)
        self.assertEqual(result.final_path, event.final_path)
        self.assertEqual(result.metadata, event_metadata)
        self.assertIn("media_metadata", result.message)
        legacy_attempt.assert_not_called()
        storage.rename.assert_not_called()
        storage.move_file.assert_not_called()

    def test_confirmed_s01e01_rule_mapping_prevents_ai_from_changing_target(self):
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
        storage.delete_single_file.return_value = True
        contract = self._standalone_media_metadata(
            "live_action_series",
            "series",
        )
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "e" * 40,
            selected_path="/真人剧集",
            user_id=1,
            final_path="/真人剧集/Test.Show.S01E01",
            resource_name="Test.Show.S01E01",
            metadata={"media_metadata": contract},
            storage=storage,
        )

        with patch.object(
            renaming,
            "collect_storage_file_tree",
            return_value=[{
                "name": "Test.Show.S01E01.mkv",
                "relative_path": "Test.Show.S01E01.mkv",
                "is_dir": False,
            }],
        ), patch.object(
            renaming,
            "_get_tvdb_candidates_and_episodes",
            return_value=([], []),
        ), patch.object(
            renaming,
            "infer_tvdb_episode_plan_with_ai",
            return_value={
                "episode_map": [{
                    "source_file": "Test.Show.S01E01.mkv",
                    "season_number": 1,
                    "episode_number": 2,
                }]
            },
        ) as ai_mapper:
            result = renaming.process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertTrue(result.should_stop)
        self.assertEqual(result.final_path, "/真人剧集/测试影视 (Test Media)")
        self.assertEqual(
            result.metadata["media_metadata"]["items"][0]["episode_number"],
            1,
        )
        ai_mapper.assert_not_called()

    def test_valid_standalone_movie_skips_episode_inference(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules import renaming

        init.logger = Mock()
        init.bot_config = {
            "ai": {
                "api_url": "https://ai.example",
                "api_key": "key",
                "model": "model",
            }
        }
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "f" * 40,
            selected_path="/真人电影",
            user_id=1,
            final_path="/真人电影/Raw.Movie",
            resource_name="Test.Media.2026.1080p",
            metadata={
                "media_metadata": self._standalone_media_metadata(
                    "live_action_movie",
                    "movie",
                )
            },
            storage=Mock(),
        )

        with patch.object(
            renaming,
            "_attempt_legacy_tvdb_ai_episode_rename",
            return_value=None,
        ) as legacy_attempt:
            result = renaming.process_tvdb_episode(event)

        self.assertFalse(result.handled)
        legacy_attempt.assert_not_called()

    def test_confirmed_series_rule_mapping_skips_download_time_ai(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules import renaming

        init.logger = Mock()
        init.bot_config = {
            "ai": {"api_url": "https://ai.example", "api_key": "key", "model": "model"},
            "media": {"unorganized_path": "/未整理"},
        }
        storage = Mock()
        storage.create_dir_recursive.return_value = True
        storage.rename.return_value = True
        storage.move_file.return_value = True
        storage.get_file_info.return_value = None
        storage.delete_single_file.return_value = True
        contract = self._standalone_media_metadata("live_action_series", "series")
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "7" * 40,
            selected_path="/真人剧集",
            user_id=1,
            final_path="/真人剧集/Test.Show.S01E01",
            resource_name="Test.Show.S01E01",
            metadata={"media_metadata": contract},
            storage=storage,
        )

        with patch.object(
            renaming,
            "collect_storage_file_tree",
            return_value=[{
                "name": "Test.Show.S01E01.mkv",
                "relative_path": "Test.Show.S01E01.mkv",
                "is_dir": False,
            }],
        ), patch.object(
            renaming,
            "_get_tvdb_candidates_and_episodes",
            return_value=([], []),
        ), patch.object(
            renaming,
            "infer_tvdb_episode_plan_with_ai",
        ) as ai_mapper:
            result = renaming.process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.metadata["media_metadata"]["items"][0]["final_path"].rsplit("/", 1)[-1], "Test Media S01E01.mkv")
        ai_mapper.assert_not_called()

    def test_confirmed_series_ai_receives_only_rule_unresolved_files_and_items(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules import renaming

        init.logger = Mock()
        init.bot_config = {
            "ai": {"api_url": "https://ai.example", "api_key": "key", "model": "model"},
            "media": {"unorganized_path": "/未整理"},
        }
        storage = Mock()
        storage.create_dir_recursive.return_value = True
        storage.rename.return_value = True
        storage.move_file.return_value = True
        storage.get_file_info.return_value = None
        storage.delete_single_file.return_value = True
        contract = self._standalone_media_metadata("live_action_series", "series")
        contract["items"].append({
            "item_id": "episode-2",
            "content_role": "main_episode",
            "season_number": 1,
            "episode_number": 2,
        })
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "8" * 40,
            selected_path="/真人剧集",
            user_id=1,
            final_path="/真人剧集/Test.Show.Release",
            resource_name="Test.Show.Release",
            metadata={"media_metadata": contract},
            storage=storage,
        )
        mapper = Mock(return_value={
            "episode_map": [{
                "source_file": "Episode.Two.Final.mkv",
                "season_number": 1,
                "episode_number": 2,
            }],
            "warnings": [],
        })

        with patch.object(
            renaming,
            "collect_storage_file_tree",
            return_value=[
                {"name": "Test.Show.S01E01.mkv", "relative_path": "Test.Show.S01E01.mkv", "is_dir": False},
                {"name": "Episode.Two.Final.mkv", "relative_path": "Episode.Two.Final.mkv", "is_dir": False},
            ],
        ), patch.object(
            renaming,
            "_get_tvdb_candidates_and_episodes",
            return_value=([], []),
        ), patch.object(
            renaming,
            "infer_tvdb_episode_plan_with_ai",
            mapper,
        ):
            result = renaming.process_tvdb_episode(event)

        context = mapper.call_args.args[0]
        self.assertEqual(
            [item["relative_path"] for item in context["file_tree"]],
            ["Episode.Two.Final.mkv"],
        )
        self.assertEqual(
            [item["item_id"] for item in context["confirmed_items"]],
            ["episode-2"],
        )
        self.assertTrue(result.handled)
        self.assertEqual(
            sorted(item["episode_number"] for item in result.metadata["media_metadata"]["items"] if item.get("final_path")),
            [1, 2],
        )

    def test_confirmed_series_mid_batch_failure_returns_terminal_partial_summary(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules import renaming

        init.logger = Mock()
        init.bot_config = {
            "ai": {"api_url": "https://ai.example", "api_key": "key", "model": "model"},
            "media": {"unorganized_path": "/未整理"},
        }
        storage = Mock()
        storage.create_dir_recursive.return_value = True
        storage.rename.return_value = True
        storage.get_file_info.return_value = None
        storage.move_file.side_effect = [True, False, True]
        storage.delete_single_file.return_value = True
        contract = self._standalone_media_metadata("live_action_series", "series")
        contract["items"].append({
            "item_id": "episode-2",
            "content_role": "main_episode",
            "season_number": 1,
            "episode_number": 2,
        })
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "a" * 40,
            selected_path="/真人剧集",
            user_id=1,
            final_path="/真人剧集/Test.Show.S01",
            resource_name="Test.Show.S01",
            metadata={
                "download_cleanup": {"count": 3, "files": ["sample.mkv", "poster.jpg", "subtitle.srt"]},
                "media_metadata": contract,
            },
            storage=storage,
        )

        with patch.object(
            renaming,
            "collect_storage_file_tree",
            return_value=[
                {"name": "Test.Show.S01E01.mkv", "relative_path": "Test.Show.S01E01.mkv", "is_dir": False, "is_video": True},
                {"name": "Test.Show.S01E02.mkv", "relative_path": "Test.Show.S01E02.mkv", "is_dir": False, "is_video": True},
                {"name": "large.nfo", "relative_path": "large.nfo", "is_dir": False, "is_video": False},
            ],
        ), patch.object(
            renaming,
            "_get_tvdb_candidates_and_episodes",
            return_value=([], []),
        ), patch.object(
            renaming,
            "infer_tvdb_episode_plan_with_ai",
        ) as ai_mapper:
            result = renaming.process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertTrue(result.should_stop)
        self.assertIn("部分完成", result.message)
        self.assertIn("正式目录：1", result.message)
        self.assertIn("未整理：1", result.message)
        self.assertIn("清理：4", result.message)
        self.assertIn("移动失败", result.message)
        resolved = [
            item
            for item in result.metadata["media_metadata"]["items"]
            if item.get("final_path")
        ]
        self.assertEqual([item["episode_number"] for item in resolved], [1])
        self.assertEqual(result.final_path, "/真人剧集/测试影视 (Test Media)")
        ai_mapper.assert_not_called()
        storage.delete_single_file.assert_called_once_with(
            "/真人剧集/Test.Show.S01/large.nfo"
        )
        storage.move_file.assert_any_call(
            "/真人剧集/Test.Show.S01/Test Media S01E02.mkv",
            "/未整理/Test.Show.S01",
        )

    def test_confirmed_series_failed_mapping_cleans_non_video_and_unorganizes_large_video(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules import renaming

        init.logger = Mock()
        init.bot_config = {
            "ai": {"api_url": "https://ai.example", "api_key": "key", "model": "model"},
            "media": {"unorganized_path": "/未整理"},
        }
        storage = Mock()
        storage.create_dir_recursive.return_value = True
        storage.move_file.return_value = True
        storage.delete_single_file.return_value = True
        contract = self._standalone_media_metadata("live_action_series", "series")
        event = DownloadCompletedEvent(
            link="magnet:?xt=urn:btih:" + "b" * 40,
            selected_path="/真人剧集",
            user_id=1,
            final_path="/真人剧集/Unknown.Release",
            resource_name="Unknown.Release",
            metadata={"media_metadata": contract},
            storage=storage,
        )

        with patch.object(
            renaming,
            "collect_storage_file_tree",
            return_value=[
                {"name": "Unknown.Video.mkv", "relative_path": "Unknown.Video.mkv", "is_dir": False, "is_video": True},
                {"name": "large.nfo", "relative_path": "large.nfo", "is_dir": False, "is_video": False},
            ],
        ), patch.object(
            renaming,
            "_get_tvdb_candidates_and_episodes",
            return_value=([], []),
        ), patch.object(
            renaming,
            "infer_tvdb_episode_plan_with_ai",
            return_value={"episode_map": [], "warnings": ["无法判断"]},
        ):
            result = renaming.process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertTrue(result.should_stop)
        self.assertIn("自动整理失败", result.message)
        self.assertIn("正式目录：0", result.message)
        self.assertIn("未整理：1", result.message)
        self.assertIn("清理：1", result.message)
        storage.delete_single_file.assert_called_once_with(
            "/真人剧集/Unknown.Release/large.nfo"
        )
        storage.move_file.assert_called_once_with(
            "/真人剧集/Unknown.Release/Unknown.Video.mkv",
            "/未整理/Unknown.Release",
        )

    def test_all_four_standalone_categories_use_contract_generic_naming(self):
        import init
        from app.core.module_registry import DownloadCompletedEvent
        from app.modules.renaming import process_generic_media

        init.logger = Mock()
        routes = (
            ("live_action_movie", "movie", "/真人电影"),
            ("animated_movie", "movie", "/动画电影"),
            ("live_action_series", "series", "/真人剧集"),
            ("animated_series", "series", "/动画剧集"),
        )
        for category_kind, library_type, selected_path in routes:
            with self.subTest(category_kind=category_kind):
                storage = Mock()
                storage.get_files_from_dir.return_value = ["Original.Name.mkv"]
                storage.create_dir_recursive.return_value = True
                storage.rename.return_value = True
                storage.move_file.return_value = True
                storage.delete_single_file.return_value = True
                contract = self._standalone_media_metadata(
                    category_kind,
                    library_type,
                )
                event = DownloadCompletedEvent(
                    link="magnet:?xt=urn:btih:" + "1" * 40,
                    selected_path=selected_path,
                    user_id=1,
                    final_path=f"{selected_path}/Raw.Release",
                    resource_name="Test.Media.2026.1080p",
                    metadata={"media_metadata": contract},
                    storage=storage,
                )

                result = process_generic_media(event)

                self.assertTrue(result.handled)
                self.assertTrue(result.should_stop)
                self.assertEqual(
                    result.metadata["media_metadata"]["metadata_id"],
                    contract["metadata_id"],
                )
                self.assertEqual(result.metadata, event.metadata)
                self.assertEqual(
                    result.final_path,
                    f"{selected_path}/测试影视 (Test Media)",
                )

    def test_tvdb_prompt_names_core_confirmed_metadata_context(self):
        from app.utils.ai import TVDB_EPISODE_PLAN_PROMPT

        self.assertIn("confirmed_media_metadata", TVDB_EPISODE_PLAN_PROMPT)
        self.assertIn("尚未被规则映射", TVDB_EPISODE_PLAN_PROMPT)
        self.assertIn("confirmed_items", TVDB_EPISODE_PLAN_PROMPT)
        self.assertNotIn(
            "_".join(("confirmed", "download", "plan")),
            TVDB_EPISODE_PLAN_PROMPT,
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
