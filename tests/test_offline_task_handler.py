import sys
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.handlers.offline_task_handler import handle_clear_retry_list, handle_single_retry_action, view_retry_list


class OfflineTaskHandlerTest(unittest.TestCase):
    def setUp(self):
        init.logger = Mock()
        init.openapi_115 = Mock()
        init.openapi_115.offline_download_specify_path.return_value = True
        init.openapi_115.get_offline_tasks.return_value = []
        init.bot_config = {"allowed_user": 472943219}
        init.check_user = Mock(side_effect=lambda user_id: user_id == 472943219)

    @patch("app.handlers.offline_task_handler.remove_task_from_retry_list")
    @patch("app.handlers.download_handler.download_executor.submit")
    @patch("app.handlers.download_handler.download_task")
    @patch(
        "app.handlers.offline_task_handler.get_retry_task",
        return_value={
            "id": 7,
            "title": "Partial.Release",
            "magnet": "magnet:?xt=urn:btih:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
            "save_path": "/真人剧集",
            "progress_percent": 42,
            "retry_category": "partial",
            "last_error": "115 离线下载超时",
        },
    )
    def test_single_retry_removes_task_before_reusing_main_download_chain(
        self,
        get_retry_task_mock,
        download_task_mock,
        submit_mock,
        remove_task_mock,
    ):
        events = []

        def remove_task(task_id):
            events.append(("remove", task_id))

        def submit_download(func, link, save_path, user_id):
            events.append(("submit", func, link, save_path, user_id))

        remove_task_mock.side_effect = remove_task
        submit_mock.side_effect = submit_download
        update = Mock()
        update.effective_user.id = 472943219
        update.callback_query.data = "retry_task:7"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()

        asyncio.run(handle_single_retry_action(update, context))

        get_retry_task_mock.assert_called_once_with(7)
        self.assertEqual(events[0], ("remove", 7))
        self.assertEqual(
            events[1],
            (
                "submit",
                download_task_mock,
                "magnet:?xt=urn:btih:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
                "/真人剧集",
                472943219,
            ),
        )
        update.callback_query.edit_message_text.assert_awaited_once()

    @patch(
        "app.handlers.offline_task_handler.get_failed_tasks",
        return_value=[
            {
                "id": 7,
                "title": "Partial.Release",
                "magnet": "magnet:?xt=urn:btih:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
                "save_path": "/真人剧集",
                "progress_percent": 42,
                "retry_category": "partial",
                "last_error": "115 离线下载超时",
            }
        ],
    )
    def test_view_retry_list_shows_single_retry_actions_and_progress(self, get_failed_tasks_mock):
        update = Mock()
        update.message.from_user.id = 472943219
        update.message.reply_text = AsyncMock()
        context = Mock()

        asyncio.run(view_retry_list(update, context))

        text = update.message.reply_text.await_args.args[0]
        keyboard = update.message.reply_text.await_args.kwargs["reply_markup"]
        callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]

        self.assertIn("Partial.Release", text)
        self.assertIn("42.0%", text)
        self.assertIn("████████", text)
        self.assertIn("retry_task:7", callback_data)
        self.assertIn("drop_retry:7", callback_data)

    @patch("app.handlers.offline_task_handler.get_failed_tasks")
    def test_view_retry_list_rejects_unauthorized_user(self, get_failed_tasks_mock):
        update = Mock()
        update.message.from_user.id = 1
        update.message.reply_text = AsyncMock()
        context = Mock()

        asyncio.run(view_retry_list(update, context))

        get_failed_tasks_mock.assert_not_called()
        update.message.reply_text.assert_awaited_once_with("⚠️ 当前账号无权使用此机器人。")

    @patch("app.handlers.offline_task_handler.get_retry_task")
    def test_single_retry_rejects_unauthorized_user(self, get_retry_task_mock):
        update = Mock()
        update.effective_user.id = 1
        update.callback_query.data = "retry_task:7"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()

        asyncio.run(handle_single_retry_action(update, context))

        get_retry_task_mock.assert_not_called()
        update.callback_query.edit_message_text.assert_awaited_once_with("⚠️ 当前账号无权使用此机器人。")

    @patch("app.handlers.offline_task_handler.clear_failed_tasks")
    def test_clear_retry_list_rejects_unauthorized_user(self, clear_failed_tasks_mock):
        update = Mock()
        update.effective_user.id = 1
        update.callback_query.data = "clear_all"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()

        asyncio.run(handle_clear_retry_list(update, context))

        clear_failed_tasks_mock.assert_not_called()
        update.callback_query.edit_message_text.assert_awaited_once_with("⚠️ 当前账号无权使用此机器人。")

    def test_get_retry_task_only_reads_pending_retry_rows(self):
        from app.handlers.offline_task_handler import get_retry_task

        fake_sqlite = Mock()
        fake_sqlite.__enter__ = Mock(return_value=fake_sqlite)
        fake_sqlite.__exit__ = Mock(return_value=False)
        fake_sqlite.query_all.return_value = []

        with patch("app.handlers.offline_task_handler.SqlLiteLib", return_value=fake_sqlite):
            self.assertIsNone(get_retry_task(7))

        fake_sqlite.query_all.assert_called_once_with(
            "SELECT * FROM offline_task WHERE id = ? AND is_download = 0",
            (7,),
        )


if __name__ == "__main__":
    unittest.main()
