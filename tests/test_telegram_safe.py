import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.telegram_safe import safe_reply_text


class TelegramSafeTest(unittest.IsolatedAsyncioTestCase):
    async def test_safe_reply_text_logs_and_continues_when_reply_times_out(self):
        message = Mock()
        message.reply_text = Mock(side_effect=TimeoutError("timed out"))
        logger = Mock()

        result = await safe_reply_text(message, "processing", logger=logger)

        self.assertFalse(result)
        logger.warn.assert_called_once()

    async def test_safe_reply_text_returns_true_when_sent(self):
        async def reply_text(text):
            return "sent"

        message = Mock()
        message.reply_text = reply_text

        self.assertTrue(await safe_reply_text(message, "processing"))


if __name__ == "__main__":
    unittest.main()
