import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.log_sanitizer import sanitize_log_value


class LogSanitizerTest(unittest.TestCase):
    def test_redacts_links_tokens_and_magnets(self):
        text = sanitize_log_value(
            {
                "Authorization": "Bearer access-secret",
                "download_link": "https://download.example/private.mkv",
                "magnet": "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            }
        )

        self.assertIn("***redacted***", text)
        self.assertNotIn("access-secret", text)
        self.assertNotIn("https://download.example/private.mkv", text)
        self.assertNotIn("0123456789ABCDEF0123456789ABCDEF01234567", text)

    def test_message_queue_final_failure_logs_are_sanitized(self):
        source = (ROOT / "app" / "utils" / "message_queue.py").read_text(encoding="utf-8")

        self.assertNotIn("失败消息内容: {message}", source)
        self.assertIn("失败消息内容: {sanitize_log_value(message)}", source)


if __name__ == "__main__":
    unittest.main()
