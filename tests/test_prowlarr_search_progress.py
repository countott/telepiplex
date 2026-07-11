import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.handlers import search_handler


class ProwlarrSearchProgressTest(unittest.IsolatedAsyncioTestCase):
    @patch.object(search_handler, "get_prowlarr_indexer_summary", return_value={})
    @patch.object(search_handler, "_send_search_message", new_callable=AsyncMock)
    @patch.object(search_handler, "_search_prowlarr_with_progress", new_callable=AsyncMock)
    @patch.object(search_handler, "_reply_or_send", new_callable=AsyncMock)
    async def test_search_starts_at_zero_and_reuses_status_message(
        self,
        reply_mock,
        progress_mock,
        send_mock,
        _summary_mock,
    ):
        status_message = SimpleNamespace(edit_text=AsyncMock())
        reply_mock.return_value = status_message
        progress_mock.return_value = []
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_user=SimpleNamespace(id=2),
            callback_query=None,
            message=SimpleNamespace(),
        )
        context = SimpleNamespace(bot=SimpleNamespace())

        await search_handler._send_search_results(
            update,
            context,
            "Example",
            metadata={"media_type": "movie"},
        )

        self.assertIn("已等待 0 秒", reply_mock.await_args.args[2])
        self.assertIs(
            progress_mock.await_args.kwargs["status_message"],
            status_message,
        )
        send_mock.assert_awaited_once()

    async def test_progress_edits_same_message_until_search_completes(self):
        result = [{"title": "Example.Release"}]
        fake_task = Mock()
        fake_task.result.return_value = result
        status_message = SimpleNamespace(edit_text=AsyncMock())

        def create_task(coroutine):
            coroutine.close()
            return fake_task

        with (
            patch.object(
                search_handler.asyncio,
                "create_task",
                side_effect=create_task,
            ),
            patch.object(
                search_handler.asyncio,
                "wait",
                new=AsyncMock(
                    side_effect=[
                        (set(), {fake_task}),
                        ({fake_task}, set()),
                    ]
                ),
            ),
            patch.object(
                search_handler,
                "_send_search_message",
                new_callable=AsyncMock,
            ) as send_mock,
        ):
            actual = await search_handler._search_prowlarr_with_progress(
                SimpleNamespace(),
                SimpleNamespace(),
                "Example",
                status_message=status_message,
                progress_interval=1,
                media_type="movie",
                clock=Mock(side_effect=[100.0, 101.1, 102.2]),
            )

        self.assertEqual(actual, result)
        edits = [
            call.kwargs["text"]
            for call in status_message.edit_text.await_args_list
        ]
        self.assertIn("已等待 1 秒", edits[0])
        self.assertIn("搜索完成", edits[1])
        self.assertIn("用时 2 秒", edits[1])
        send_mock.assert_not_awaited()

    async def test_edit_failure_does_not_cancel_search(self):
        result = [{"title": "Example.Release"}]
        fake_task = Mock()
        fake_task.result.return_value = result
        status_message = SimpleNamespace(
            edit_text=AsyncMock(side_effect=Exception("edit failed"))
        )

        def create_task(coroutine):
            coroutine.close()
            return fake_task

        with (
            patch.object(
                search_handler.asyncio,
                "create_task",
                side_effect=create_task,
            ),
            patch.object(
                search_handler.asyncio,
                "wait",
                new=AsyncMock(
                    side_effect=[
                        (set(), {fake_task}),
                        ({fake_task}, set()),
                    ]
                ),
            ),
        ):
            actual = await search_handler._search_prowlarr_with_progress(
                SimpleNamespace(),
                SimpleNamespace(),
                "Example",
                status_message=status_message,
                progress_interval=1,
                media_type="movie",
                clock=Mock(side_effect=[100.0, 101.1, 102.2]),
            )

        self.assertEqual(actual, result)


if __name__ == "__main__":
    unittest.main()
