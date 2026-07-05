import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init
from app.handlers.download_handler import download_task


class DownloadTaskStartupTest(unittest.TestCase):
    def setUp(self):
        init.logger = Mock()
        init.openapi_115 = None

    @patch("app.utils.message_queue.add_task_to_queue")
    def test_download_task_reports_unavailable_115_without_crashing(self, add_task_mock):
        download_task("magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567", "/电影", 123)

        add_task_mock.assert_called_once()
        self.assertEqual(add_task_mock.call_args.args[:2], (123, None))
        self.assertIn("115 OpenAPI 尚未初始化", add_task_mock.call_args.kwargs["message"])


if __name__ == "__main__":
    unittest.main()
