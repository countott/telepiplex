import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class MessageQueueTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app import init

        sys.modules.setdefault("init", init)
        from app.utils import message_queue

        self.module = message_queue
        self.original_logger = init.logger
        init.logger = SimpleNamespace(
            debug=lambda *_args, **_kwargs: None,
            info=lambda *_args, **_kwargs: None,
            warn=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        )
        self.original_queue = message_queue.message_queue
        self.original_loop = message_queue.global_loop
        message_queue.message_queue = asyncio.Queue()
        message_queue.global_loop = asyncio.get_running_loop()

    async def asyncTearDown(self):
        self.module.init.logger = self.original_logger
        self.module.message_queue = self.original_queue
        self.module.global_loop = self.original_loop

    async def test_feature_notification_is_sent_as_literal_plain_text(self):
        delivered = asyncio.Event()
        bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=lambda **_kwargs: delivered.set()),
            send_photo=AsyncMock(),
        )
        message = (
            "✅ 115 下载完成\n"
            "保存目录：/电影/HDR10+_unique_video_(Constantine)"
        )

        with patch.object(self.module, "Bot", return_value=bot):
            worker = asyncio.create_task(
                self.module.queue_worker(asyncio.get_running_loop(), "token")
            )
            try:
                await asyncio.to_thread(
                    self.module.add_task_to_queue, 123, None, message
                )
                await asyncio.wait_for(delivered.wait(), timeout=1)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

        sent = bot.send_message.await_args.kwargs
        self.assertEqual(sent["text"], message)
        self.assertNotIn("parse_mode", sent)


if __name__ == "__main__":
    unittest.main()
