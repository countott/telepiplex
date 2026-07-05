import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init
from app.handlers.download_handler import download_task, handle_manual_rename


class DownloadTaskStartupTest(unittest.TestCase):
    def setUp(self):
        init.logger = Mock()
        init.openapi_115 = None
        init.bot_config = {
            "strm_mode": "disable",
            "emby_server": "http://emby.example",
            "api_key": "",
            "aria2": {"enable": False},
        }

    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_reports_unavailable_115_without_crashing(self, add_task_mock):
        download_task("magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567", "/电影", 123)

        add_task_mock.assert_called_once()
        self.assertEqual(add_task_mock.call_args.args[:2], (123, None))
        self.assertIn("115 OpenAPI 尚未初始化", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.notice_emby_scan_library", return_value=True)
    @patch("app.handlers.download_handler.create_strm_file")
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_auto_renames_douban_result_for_plex(self, add_task_mock, create_strm_mock, notice_mock):
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
        create_strm_mock.assert_called_once_with(
            "/影视/电影/外语电影/布达佩斯大饭店/The Grand Budapest Hotel",
            ["The Grand Budapest Hotel.mkv"],
        )
        notice_mock.assert_called_once_with(
            "/影视/电影/外语电影/布达佩斯大饭店/The Grand Budapest Hotel"
        )
        self.assertIn("自动整理完成", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.notice_emby_scan_library", return_value=True)
    @patch("app.handlers.download_handler.create_strm_file")
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_auto_renames_plain_search_result_for_plex(self, add_task_mock, create_strm_mock, notice_mock):
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
        create_strm_mock.assert_called_once_with(
            "/影视/剧集/欧美剧/绝命毒师/Breaking Bad",
            ["Breaking Bad S02E03.mp4"],
        )
        notice_mock.assert_called_once_with(
            "/影视/剧集/欧美剧/绝命毒师/Breaking Bad"
        )
        self.assertIn("Breaking Bad S02E03.mp4", add_task_mock.call_args.kwargs["message"])

    @patch("app.core.subscribe_movie.is_subscribe", return_value=False)
    @patch("app.handlers.download_handler.get_movie_cover", return_value="")
    @patch("app.handlers.download_handler.notice_emby_scan_library", return_value=True)
    @patch("app.handlers.download_handler.create_strm_file")
    def test_manual_rename_stops_after_rename_without_aria_push(
        self,
        create_strm_mock,
        notice_mock,
        cover_mock,
        subscribe_mock,
    ):
        api = Mock()
        api.rename.return_value = True
        api.get_files_from_dir.return_value = ["movie.mkv"]
        init.openapi_115 = api
        init.aria2_client = Mock()
        if hasattr(init, "pending_push_tasks"):
            delattr(init, "pending_push_tasks")

        update = Mock()
        update.message.text = "The Grand Budapest Hotel"
        update.effective_chat.id = 123
        context = Mock()
        context.user_data = {
            "rename_data": {
                "resource_name": "old-name",
                "selected_path": "/影视/电影/外语电影",
                "link": "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
                "add2retry": False,
                "final_path": "/影视/电影/外语电影/old-name",
            }
        }
        context.bot.send_message = AsyncMock()

        import asyncio
        asyncio.run(handle_manual_rename(update, context))

        api.rename.assert_called_once_with(
            "/影视/电影/外语电影/old-name",
            "The Grand Budapest Hotel",
        )
        create_strm_mock.assert_called_once_with(
            "/影视/电影/外语电影/The Grand Budapest Hotel",
            ["movie.mkv"],
        )
        notice_mock.assert_called_once_with("/影视/电影/外语电影/The Grand Budapest Hotel")
        context.bot.send_message.assert_called()
        self.assertIn("重命名成功", context.bot.send_message.await_args.kwargs["text"])
        self.assertFalse(hasattr(init, "pending_push_tasks"))


if __name__ == "__main__":
    unittest.main()
