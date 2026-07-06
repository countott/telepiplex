import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init
from app.handlers.download_handler import download_task
from app.utils.ai import get_movie_tmdb_name_with_ai
from app.utils.cover_capture import get_movie_cover


class DownloadTaskStartupTest(unittest.TestCase):
    def setUp(self):
        init.logger = Mock()
        init.openapi_115 = None
        init.bot_config = {
            "media": {
                "unorganized_path": "/未整理",
            },
            "aria2": {"enable": False},
        }

    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_reports_unavailable_115_without_crashing(self, add_task_mock):
        download_task("magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567", "/电影", 123)

        add_task_mock.assert_called_once()
        self.assertEqual(add_task_mock.call_args.args[:2], (123, None))
        self.assertIn("115 OpenAPI 尚未初始化", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.handle_media_library_update", return_value=None)
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_auto_renames_douban_result_for_plex(self, add_task_mock, media_update_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "The.Grand.Budapest.Hotel.2014.1080p", "HASH")
        api.is_directory.return_value = True
        api.get_files_from_dir.return_value = ["movie.mkv"]
        api.create_dir_recursive.return_value = {"file_id": "target"}
        api.rename.return_value = True
        api.move_file.return_value = True
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/影视/电影/外语电影",
            123,
            plex_metadata={
                "source": "douban",
                "chinese_title": "布达佩斯大饭店",
                "english_title": "The Grand Budapest Hotel",
                "year": "2014",
                "release_title": "The.Grand.Budapest.Hotel.2014.1080p.BluRay.x265-GROUP",
            },
        )

        api.create_dir_recursive.assert_called_once_with(
            "/影视/电影/外语电影/布达佩斯大饭店/The Grand Budapest Hotel"
        )
        api.rename.assert_called_once_with(
            "/影视/电影/外语电影/The.Grand.Budapest.Hotel.2014.1080p/movie.mkv",
            "The Grand Budapest Hotel.mkv",
        )
        api.move_file.assert_called_once_with(
            "/影视/电影/外语电影/The.Grand.Budapest.Hotel.2014.1080p/The Grand Budapest Hotel.mkv",
            "/影视/电影/外语电影/布达佩斯大饭店/The Grand Budapest Hotel",
        )
        api.delete_single_file.assert_called_once_with(
            "/影视/电影/外语电影/The.Grand.Budapest.Hotel.2014.1080p"
        )
        media_update_mock.assert_called_once_with(
            "/影视/电影/外语电影/布达佩斯大饭店/The Grand Budapest Hotel"
        )
        self.assertIn("自动整理完成", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.handle_media_library_update", return_value=None)
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_auto_renames_plain_search_result_for_plex(self, add_task_mock, media_update_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "Breaking.Bad.S02E03.1080p", "HASH")
        api.is_directory.return_value = True
        api.get_files_from_dir.return_value = ["episode.mp4"]
        api.create_dir_recursive.return_value = {"file_id": "target"}
        api.rename.return_value = True
        api.move_file.return_value = True
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/影视/剧集/欧美剧",
            123,
            plex_metadata={
                "source": "search_query",
                "chinese_title": "绝命毒师",
                "release_title": "Breaking.Bad.S02E03.1080p.WEB-DL.H264-GROUP",
            },
        )

        api.create_dir_recursive.assert_called_once_with(
            "/影视/剧集/欧美剧/绝命毒师/Breaking Bad"
        )
        api.rename.assert_called_once_with(
            "/影视/剧集/欧美剧/Breaking.Bad.S02E03.1080p/episode.mp4",
            "Breaking Bad S02E03.mp4",
        )
        api.move_file.assert_called_once_with(
            "/影视/剧集/欧美剧/Breaking.Bad.S02E03.1080p/Breaking Bad S02E03.mp4",
            "/影视/剧集/欧美剧/绝命毒师/Breaking Bad",
        )
        media_update_mock.assert_called_once_with(
            "/影视/剧集/欧美剧/绝命毒师/Breaking Bad"
        )
        self.assertIn("Breaking Bad S02E03.mp4", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_moves_success_without_metadata_to_unorganized(self, add_task_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "Unknown.Release.2026", "HASH")
        api.is_directory.return_value = True
        api.create_dir_recursive.return_value = {"file_id": "unorganized"}
        api.move_file.return_value = True
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/影视/电影/外语电影",
            123,
        )

        api.create_dir_recursive.assert_called_once_with("/未整理")
        api.move_file.assert_called_once_with(
            "/影视/电影/外语电影/Unknown.Release.2026",
            "/未整理",
        )
        self.assertIn("/未整理/Unknown.Release.2026", add_task_mock.call_args.kwargs["message"])
        self.assertNotIn("TMDB", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_moves_auto_rename_failure_to_unorganized(self, add_task_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "Bad.Release", "HASH")
        api.is_directory.return_value = True
        api.get_files_from_dir.return_value = []
        api.create_dir_recursive.return_value = {"file_id": "unorganized"}
        api.move_file.return_value = True
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/影视/电影/外语电影",
            123,
            plex_metadata={
                "source": "douban",
                "chinese_title": "未知",
                "english_title": "Unknown",
                "year": "2026",
            },
        )

        api.move_file.assert_called_once_with("/影视/电影/外语电影/Bad.Release", "/未整理")
        self.assertIn("未自动整理", add_task_mock.call_args.kwargs["message"])
        self.assertNotIn("TMDB", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.time.sleep", return_value=None)
    @patch("app.handlers.download_handler.handle_media_library_update", return_value=None)
    @patch("app.handlers.download_handler.infer_tvdb_episode_plan_with_ai")
    @patch("app.handlers.download_handler.get_tvdb_series_episodes")
    @patch("app.handlers.download_handler.search_tvdb_series")
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_uses_115_tree_and_tvdb_ai_plan_before_legacy_rename(
        self,
        add_task_mock,
        search_tvdb_mock,
        episodes_mock,
        ai_plan_mock,
        media_update_mock,
        sleep_mock,
    ):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "Dexter.Release", "HASH")
        api.is_directory.return_value = True
        api.get_file_info.return_value = {"file_id": "root", "file_category": "0"}
        api.get_file_list.return_value = [
            {"fn": "Dexter.S01E01.mkv", "fid": "file-1", "fc": "1", "fs": 1024}
        ]
        api.create_dir_recursive.return_value = {"file_id": "season"}
        api.rename.return_value = True
        api.move_file.return_value = True
        api.delete_single_file.return_value = True
        api.del_offline_task.return_value = True
        init.openapi_115 = api
        init.bot_config["ai"] = {
            "api_url": "https://api.example/v1",
            "api_key": "key",
            "model": "model",
        }

        search_tvdb_mock.return_value = [{"tvdb_series_id": "79349", "name": "Dexter", "year": "2006"}]
        episodes_mock.return_value = [
            {"tvdb_episode_id": 349232, "season_number": 1, "episode_number": 1, "name": "Dexter"}
        ]
        ai_plan_mock.return_value = {
            "tvdb_series_id": "79349",
            "series_name": "Dexter",
            "episode_map": [
                {
                    "source_file": "Dexter.S01E01.mkv",
                    "target_relative_path": "Season 01/Dexter - S01E01 - Dexter.mkv",
                    "tvdb_episode_id": 349232,
                    "season_number": 1,
                    "episode_number": 1,
                }
            ],
            "warnings": [],
        }

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/真人剧集",
            123,
            plex_metadata={
                "source": "douban",
                "chinese_title": "嗜血法医",
                "english_title": "Dexter",
                "year": "2006",
            },
            metadata={
                "source": "douban",
                "chinese_title": "嗜血法医",
                "english_title": "Dexter",
                "year": "2006",
                "query": "Dexter 2006",
                "release_title": "Dexter.S01.1080p",
            },
        )

        api.get_file_info.assert_any_call("/真人剧集/Dexter.Release")
        api.get_file_list.assert_any_call({"cid": "root", "limit": 1000, "show_dir": 1})
        search_tvdb_mock.assert_called_once_with("Dexter", year="2006")
        episodes_mock.assert_called_once_with("79349", season_type="default")
        self.assertEqual(ai_plan_mock.call_args.args[0]["file_tree"][0]["relative_path"], "Dexter.S01E01.mkv")
        api.create_dir_recursive.assert_called_once_with("/真人剧集/嗜血法医/Dexter/Season 01")
        api.rename.assert_called_once_with(
            "/真人剧集/Dexter.Release/Dexter.S01E01.mkv",
            "Dexter - S01E01 - Dexter.mkv",
        )
        api.move_file.assert_called_once_with(
            "/真人剧集/Dexter.Release/Dexter - S01E01 - Dexter.mkv",
            "/真人剧集/嗜血法医/Dexter/Season 01",
        )
        api.delete_single_file.assert_any_call("/真人剧集/Dexter.Release")
        api.get_files_from_dir.assert_not_called()
        media_update_mock.assert_called_once_with("/真人剧集/嗜血法医/Dexter")
        self.assertIn("TVDB 自动整理完成", add_task_mock.call_args.kwargs["message"])
        self.assertIn("1 个文件", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.time.sleep", return_value=None)
    @patch("app.handlers.download_handler.search_tvdb_series")
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_skips_tvdb_lookup_when_ai_config_missing(
        self,
        add_task_mock,
        search_tvdb_mock,
        sleep_mock,
    ):
        init.bot_config = {
            "media": {"unorganized_path": "/未整理"},
            "metadata": {"tvdb": {"enable": True, "api_key": "tvdb-key"}},
        }
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "Dexter.Release", "HASH")
        api.is_directory.return_value = True
        api.create_dir_recursive.return_value = {"file_id": "unorganized"}
        api.move_file.return_value = True
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/真人剧集",
            123,
            metadata={"source": "imdb", "english_title": "Dexter", "year": "2006"},
        )

        search_tvdb_mock.assert_not_called()
        api.get_file_info.assert_not_called()
        init.logger.warn.assert_not_called()
        self.assertIn("/未整理/Dexter.Release", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_timeout_offers_retry_without_tmdb_rename(self, add_task_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (False, "Unknown.Release.2026", "HASH")
        api.del_offline_task.return_value = True
        init.openapi_115 = api
        init.pending_tasks = {}

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/影视/电影/外语电影",
            123,
        )

        keyboard = add_task_mock.call_args.kwargs["keyboard"]
        callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertTrue(any(item.startswith("retry_") for item in callback_data))
        self.assertIn("cancel_download", callback_data)
        self.assertFalse(any(item.startswith("rename_") for item in callback_data))
        self.assertNotIn("TMDB", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.notice_plex_scan_library", return_value="queued")
    @patch("app.handlers.download_handler.create_strm_file")
    def test_plex_media_config_takes_precedence_over_emby_strm(self, create_strm_mock, plex_scan_mock):
        from app.handlers.download_handler import handle_media_library_update

        init.bot_config = {
            "media": {
                "plex": {
                    "base_url": "http://plex.example:32400",
                    "token": "plex-token",
                    "library_id": "1",
                },
                "emby": {
                    "base_url": "http://emby.example:8096",
                    "api_key": "emby-token",
                    "strm_mode": "strm_302",
                },
            }
        }

        self.assertEqual(handle_media_library_update("/影视/电影/片名", ["movie.mkv"]), "plex")
        plex_scan_mock.assert_called_once_with("/影视/电影/片名")
        create_strm_mock.assert_not_called()

    def test_ai_and_cover_helpers_remain_available_for_future_naming_pipeline(self):
        self.assertTrue(callable(get_movie_tmdb_name_with_ai))
        self.assertTrue(callable(get_movie_cover))


if __name__ == "__main__":
    unittest.main()
