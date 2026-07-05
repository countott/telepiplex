import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init
from app.handlers.search_handler import (
    METADATA_URL_PATTERN,
    SEARCH_TASK_TTL_SECONDS,
    _fetch_media_page_title,
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

    @patch("app.handlers.search_handler.requests.get")
    def test_douban_url_uses_builtin_subject_abstract_title(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        subject_response = Mock()
        subject_response.json.return_value = {"subject": {"title": "影", "release_year": "2018"}}
        rexxar_response = Mock()
        rexxar_response.json.return_value = {"title": "影", "original_title": "Shadow", "year": "2018"}
        mock_get.side_effect = [subject_response, rexxar_response]

        title = _fetch_media_page_title("https://movie.douban.com/subject/4864908/")

        self.assertEqual(title, "Shadow 2018")
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://movie.douban.com/j/subject_abstract?subject_id=4864908")
        self.assertEqual(mock_get.call_args_list[1].args[0], "https://m.douban.com/rexxar/api/v2/movie/4864908")
        self.assertEqual(mock_get.call_args_list[1].kwargs["timeout"], 10)

    @patch("app.handlers.search_handler.requests.get")
    def test_douban_builtin_empty_title_falls_back_to_page_title(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        subject_response = Mock()
        subject_response.json.return_value = {"subject": {"title": ""}}
        rexxar_response = Mock()
        rexxar_response.json.return_value = {"title": ""}
        mobile_response = Mock()
        mobile_response.text = "<html><head><title>豆瓣</title></head></html>"
        page_response = Mock()
        page_response.text = "<html><head><title>影 Shadow (2018) (豆瓣)</title></head></html>"
        mock_get.side_effect = [subject_response, rexxar_response, mobile_response, page_response]

        title = _fetch_media_page_title("https://movie.douban.com/subject/4864908/")

        self.assertEqual(title, "影 Shadow 2018")
        self.assertEqual(mock_get.call_count, 4)
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://movie.douban.com/j/subject_abstract?subject_id=4864908")
        self.assertEqual(mock_get.call_args_list[1].args[0], "https://m.douban.com/rexxar/api/v2/movie/4864908")
        self.assertEqual(mock_get.call_args_list[2].args[0], "https://m.douban.com/movie/subject/4864908/")
        self.assertEqual(mock_get.call_args_list[3].args[0], "https://movie.douban.com/subject/4864908/")

    def test_metadata_url_pattern_matches_supported_sites_only(self):
        self.assertRegex("https://movie.douban.com/subject/1234567/", METADATA_URL_PATTERN)
        self.assertRegex("https://www.imdb.com/title/tt2278388/", METADATA_URL_PATTERN)
        self.assertRegex("https://thetvdb.com/series/breaking-bad", METADATA_URL_PATTERN)
        self.assertNotRegex("https://example.com/movie.mkv", METADATA_URL_PATTERN)
        self.assertNotRegex("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567", METADATA_URL_PATTERN)

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
