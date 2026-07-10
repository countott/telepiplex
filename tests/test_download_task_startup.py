import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init
from app.handlers.download_handler import SELECT_SUB_CATEGORY, SELECT_TARGET_FOLDER, download_task, magnet_command


class DownloadTaskStartupTest(unittest.TestCase):
    def setUp(self):
        init.logger = Mock()
        init.openapi_115 = None
        init.bot_config = {
            "category_folder": [
                {"name": "电影", "path": "/电影"},
                {"name": "剧集", "path": "/剧集"},
            ]
        }

    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_reports_unavailable_115_without_crashing(self, add_task_mock):
        download_task("magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567", "/电影", 123)

        add_task_mock.assert_called_once()
        self.assertEqual(add_task_mock.call_args.args[:2], (123, None))
        self.assertIn("115 OpenAPI 尚未初始化", add_task_mock.call_args.kwargs["message"])

    def test_magnet_command_accepts_magnet_argument_and_starts_directory_selection(self):
        init.check_user = Mock(return_value=True)
        update = Mock()
        update.message.from_user.id = 123
        update.message.reply_text = AsyncMock()
        update.effective_chat.id = 123
        context = Mock()
        context.args = ["magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567"]
        context.user_data = {}
        context.bot.send_message = AsyncMock()

        state = asyncio.run(magnet_command(update, context))

        self.assertEqual(state, SELECT_SUB_CATEGORY)
        self.assertEqual(context.user_data["link"], "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567")
        context.bot.send_message.assert_awaited_once()
        self.assertIn("请选择保存目录", context.bot.send_message.await_args.kwargs["text"])

    @patch("app.handlers.download_handler.download_executor.submit")
    def test_directory_selection_prompts_for_target_folder_name_before_download(self, submit_mock):
        from app.handlers.download_handler import select_sub_category

        update = Mock()
        update.effective_user.id = 123
        update.callback_query.data = "save_path:1"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()
        context.user_data = {"link": "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567"}

        state = asyncio.run(select_sub_category(update, context))

        self.assertEqual(state, SELECT_TARGET_FOLDER)
        self.assertEqual(context.user_data["selected_path"], "/剧集")
        submit_mock.assert_not_called()
        update.callback_query.edit_message_text.assert_awaited_once()
        self.assertIn("请输入保存后的文件夹名", update.callback_query.edit_message_text.await_args.kwargs["text"])

    @patch("app.handlers.download_handler.download_executor.submit")
    def test_target_folder_input_submits_download_with_custom_name(self, submit_mock):
        from app.handlers.download_handler import receive_target_folder_name

        update = Mock()
        update.message.text = "我的电影"
        update.message.reply_text = AsyncMock()
        update.effective_user.id = 123
        context = Mock()
        context.user_data = {
            "link": "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "selected_path": "/电影",
        }

        state = asyncio.run(receive_target_folder_name(update, context))

        self.assertEqual(state, -1)
        submit_mock.assert_called_once()
        self.assertEqual(
            submit_mock.call_args.args,
            (
                download_task,
                "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
                "/电影",
                123,
                "我的电影",
            ),
        )
        self.assertIn("已加入下载队列", update.message.reply_text.await_args.args[0])

    @patch("app.handlers.download_handler.time.sleep", return_value=None)
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_renames_completed_directory_to_user_folder_name(self, add_task_mock, sleep_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "Original.Release.2026", "HASH")
        api.is_directory.return_value = True
        api.auto_clean_all.return_value = None
        api.get_file_info.return_value = None
        api.rename.return_value = True
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/电影",
            123,
            target_folder_name="我的电影",
        )

        api.rename.assert_called_once_with("/电影/Original.Release.2026", "我的电影")
        add_task_mock.assert_called_once()
        self.assertIn("/电影/我的电影", add_task_mock.call_args.kwargs["message"])
        api.del_offline_task.assert_called_once_with("HASH", del_source_file=0)

    @patch("app.handlers.download_handler.time.sleep", return_value=None)
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_wraps_single_file_then_renames_top_folder_only(self, add_task_mock, sleep_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "movie.mkv", "HASH")
        api.is_directory.return_value = False
        api.create_dir_for_file.return_value = True
        api.move_file.return_value = True
        api.get_file_info.return_value = None
        api.rename.return_value = True
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/电影",
            123,
            target_folder_name="电影名",
        )

        api.create_dir_for_file.assert_called_once_with("/电影", "movie")
        api.move_file.assert_called_once_with("/电影/movie.mkv", "/电影/movie")
        api.rename.assert_called_once_with("/电影/movie", "电影名")
        self.assertIn("/电影/电影名", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.time.sleep", return_value=None)
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_sanitizes_target_name_and_appends_suffix_on_conflict(self, add_task_mock, sleep_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "Original.Release.2026", "HASH")
        api.is_directory.return_value = True
        api.auto_clean_all.return_value = None
        api.get_file_info.side_effect = [{"file_id": "exists"}, None]
        api.rename.return_value = True
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/电影",
            123,
            target_folder_name='坏/名字:"版"',
        )

        self.assertEqual(api.get_file_info.call_args_list[0].args[0], "/电影/坏名字:版")
        self.assertEqual(api.get_file_info.call_args_list[1].args[0], "/电影/坏名字:版 (2)")
        api.rename.assert_called_once_with("/电影/Original.Release.2026", "坏名字:版 (2)")

    @patch("app.handlers.download_handler.time.sleep", return_value=None)
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_keeps_original_name_when_user_enters_dash(self, add_task_mock, sleep_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "Original.Release.2026", "HASH")
        api.is_directory.return_value = True
        api.auto_clean_all.return_value = None
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/电影",
            123,
            target_folder_name="-",
        )

        api.rename.assert_not_called()
        self.assertIn("/电影/Original.Release.2026", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.time.sleep", return_value=None)
    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_keeps_original_directory_when_rename_fails(self, add_task_mock, sleep_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (True, "Original.Release.2026", "HASH")
        api.is_directory.return_value = True
        api.auto_clean_all.return_value = None
        api.get_file_info.return_value = None
        api.rename.return_value = False
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/电影",
            123,
            target_folder_name="新名字",
        )

        self.assertIn("重命名失败", add_task_mock.call_args.kwargs["message"])
        self.assertIn("/电影/Original.Release.2026", add_task_mock.call_args.kwargs["message"])

    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_timeout_only_notifies_failure_without_retry_db(self, add_task_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (False, "Timeout.Release", "HASH", 35)
        api.del_offline_task.return_value = True
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/电影",
            123,
            target_folder_name="目标",
        )

        self.assertIn("离线下载未完成", add_task_mock.call_args.kwargs["message"])
        self.assertIn("35.0%", add_task_mock.call_args.kwargs["message"])
        source = (ROOT / "app" / "handlers" / "download_handler.py").read_text(encoding="utf-8")
        self.assertNotIn("save_failed_download_to_db", source)

    @patch("app.handlers.download_handler.add_task_to_queue")
    def test_download_task_ignores_offline_task_cleanup_failure(self, add_task_mock):
        api = Mock()
        api.offline_download_specify_path.return_value = True
        api.check_offline_download_success.return_value = (False, "Timeout.Release", "HASH", 35)
        api.del_offline_task.side_effect = RuntimeError("cleanup failed")
        init.openapi_115 = api

        download_task(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/电影",
            123,
            target_folder_name="目标",
        )

        self.assertIn("离线下载未完成", add_task_mock.call_args.kwargs["message"])
        init.logger.warn.assert_called()


if __name__ == "__main__":
    unittest.main()
