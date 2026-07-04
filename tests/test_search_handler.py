import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.handlers.search_handler import (
    SEARCH_TASK_TTL_SECONDS,
    build_results_text,
    format_size,
    get_pending_search_task,
    parse_douban_title,
    pending_search_tasks,
)


class SearchHandlerHelpersTest(unittest.TestCase):
    def tearDown(self):
        pending_search_tasks.clear()

    def test_format_size_uses_readable_units(self):
        self.assertEqual(format_size(0), "未知")
        self.assertEqual(format_size(1536), "1.5 KB")
        self.assertEqual(format_size(5 * 1024**3), "5.0 GB")

    def test_parse_douban_title_removes_douban_suffix(self):
        html = "<html><head><title>布达佩斯大饭店 The Grand Budapest Hotel (豆瓣)</title></head></html>"

        self.assertEqual(parse_douban_title(html), "布达佩斯大饭店 The Grand Budapest Hotel")

    def test_build_results_text_contains_rank_score_size_seeders_indexer_and_features(self):
        text = build_results_text(
            "The Grand Budapest Hotel 2014",
            [
                {
                    "title": "The Grand Budapest Hotel 2014 1080p WEB-DL HEVC",
                    "score": 88,
                    "size": 8 * 1024**3,
                    "seeders": 32,
                    "indexer": "Indexer A",
                    "features": ["1080p", "WEB-DL", "HEVC"],
                }
            ],
        )

        self.assertIn("The Grand Budapest Hotel 2014", text)
        self.assertIn("1. 评分: 88", text)
        self.assertIn("大小: 8.0 GB", text)
        self.assertIn("seeders: 32", text)
        self.assertIn("indexer: Indexer A", text)
        self.assertIn("特征: 1080p / WEB-DL / HEVC", text)

    def test_get_pending_search_task_rejects_expired_tasks(self):
        pending_search_tasks["expired"] = {
            "created_at": time.time() - SEARCH_TASK_TTL_SECONDS - 1,
            "results": [],
        }

        self.assertIsNone(get_pending_search_task("expired"))
        self.assertNotIn("expired", pending_search_tasks)


if __name__ == "__main__":
    unittest.main()
