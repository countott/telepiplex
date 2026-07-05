import sys
import time
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init
from app.handlers.search_handler import (
    METADATA_URL_PATTERN,
    SEARCH_TASK_TTL_SECONDS,
    _fetch_media_page_title,
    _metadata_matches_plain_query,
    _plex_metadata_for_selected_release,
    _resolve_search_request,
    build_results_text,
    format_size,
    get_pending_search_task,
    parse_douban_title,
    pending_search_tasks,
    _search_prowlarr_with_progress,
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
    def test_mobile_douban_url_uses_builtin_subject_abstract_title(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        subject_response = Mock()
        subject_response.json.return_value = {"subject": {"title": "影", "release_year": "2018"}}
        rexxar_response = Mock()
        rexxar_response.json.return_value = {"title": "影", "original_title": "Shadow", "year": "2018"}
        mock_get.side_effect = [subject_response, rexxar_response]

        title = _fetch_media_page_title("https://m.douban.com/movie/subject/4864908/")

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

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_search_request_keeps_douban_metadata_for_plex_rename(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        subject_response = Mock()
        subject_response.json.return_value = {
            "subject": {
                "title": "布达佩斯大饭店",
                "original_title": "The Grand Budapest Hotel",
                "release_year": "2014",
            }
        }
        mock_get.return_value = subject_response

        request = asyncio.run(_resolve_search_request("https://movie.douban.com/subject/11525673/"))

        self.assertEqual(request["query"], "The Grand Budapest Hotel 2014")
        self.assertEqual(
            request["plex_metadata"],
            {
                "source": "douban",
                "chinese_title": "布达佩斯大饭店",
                "english_title": "The Grand Budapest Hotel",
                "year": "2014",
            },
        )

    def test_plex_metadata_for_selected_release_adds_release_title_without_mutating_task(self):
        task_metadata = {
            "source": "douban",
            "chinese_title": "绝命毒师",
            "english_title": "Breaking Bad",
            "year": "2008",
        }
        task = {"plex_metadata": task_metadata}
        selected_item = {"title": "Breaking.Bad.1x02.1080p.WEB-DL"}

        metadata = _plex_metadata_for_selected_release(task, selected_item)

        self.assertEqual(metadata["release_title"], "Breaking.Bad.1x02.1080p.WEB-DL")
        self.assertNotIn("release_title", task_metadata)

    def test_resolve_plain_search_request_keeps_query_as_chinese_folder_hint(self):
        request = asyncio.run(_resolve_search_request("布达佩斯大饭店"))

        self.assertEqual(request["query"], "布达佩斯大饭店")
        self.assertEqual(
            request["plex_metadata"],
            {
                "source": "search_query",
                "chinese_title": "布达佩斯大饭店",
            },
        )

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_plain_search_request_uses_douban_exact_match_metadata(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        search_response = Mock()
        search_response.text = """
        <html>
          <a href="https://movie.douban.com/subject/11525673/">布达佩斯大饭店</a>
        </html>
        """
        subject_response = Mock()
        subject_response.json.return_value = {
            "subject": {
                "title": "布达佩斯大饭店",
                "original_title": "The Grand Budapest Hotel",
                "release_year": "2014",
            }
        }
        mock_get.side_effect = [search_response, subject_response]

        request = asyncio.run(_resolve_search_request("布达佩斯大饭店"))

        self.assertEqual(request["query"], "The Grand Budapest Hotel 2014")
        self.assertEqual(
            request["plex_metadata"],
            {
                "source": "douban",
                "chinese_title": "布达佩斯大饭店",
                "english_title": "The Grand Budapest Hotel",
                "year": "2014",
            },
        )
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://www.douban.com/search")
        self.assertEqual(mock_get.call_args_list[0].kwargs["params"], {"cat": "1002", "q": "布达佩斯大饭店"})

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_plain_search_request_falls_back_when_douban_match_is_not_exact(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        search_response = Mock()
        search_response.text = '<a href="https://movie.douban.com/subject/1291546/">霸王别姬</a>'
        subject_response = Mock()
        subject_response.json.return_value = {
            "subject": {
                "title": "霸王别姬",
                "original_title": "Farewell My Concubine",
                "release_year": "1993",
            }
        }
        mock_get.side_effect = [search_response, subject_response]

        request = asyncio.run(_resolve_search_request("英雄"))

        self.assertEqual(request["query"], "英雄")
        self.assertEqual(
            request["plex_metadata"],
            {
                "source": "search_query",
                "chinese_title": "英雄",
            },
        )

    def test_plain_query_metadata_match_ignores_case_punctuation_and_year(self):
        metadata = {
            "source": "douban",
            "chinese_title": "随心所欲",
            "english_title": "Vivre sa vie: Film en douze tableaux",
            "year": "1962",
        }

        self.assertTrue(_metadata_matches_plain_query(metadata, "Vivre sa vie Film en douze tableaux"))
        self.assertTrue(_metadata_matches_plain_query(metadata, "vivre-sa-vie film en douze tableaux 1962"))

    def test_plain_search_metadata_for_selected_release_uses_candidate_title(self):
        task = {
            "query": "布达佩斯大饭店",
            "plex_metadata": {
                "source": "search_query",
                "chinese_title": "布达佩斯大饭店",
            },
        }
        selected_item = {"title": "The.Grand.Budapest.Hotel.2014.1080p.BluRay.x265-GROUP"}

        metadata = _plex_metadata_for_selected_release(task, selected_item)

        self.assertEqual(metadata["source"], "search_query")
        self.assertEqual(metadata["chinese_title"], "布达佩斯大饭店")
        self.assertEqual(metadata["release_title"], "The.Grand.Budapest.Hotel.2014.1080p.BluRay.x265-GROUP")

    def test_metadata_url_pattern_matches_supported_sites_only(self):
        self.assertRegex("https://movie.douban.com/subject/1234567/", METADATA_URL_PATTERN)
        self.assertRegex("https://movie.douban.com:443/subject/1234567/?dt_dapp=1", METADATA_URL_PATTERN)
        self.assertRegex("http://movie.douban.com:80/subject/1234567/?from=share", METADATA_URL_PATTERN)
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

    @patch("app.handlers.search_handler.search_prowlarr")
    def test_search_prowlarr_progress_notifies_during_slow_queries(self, search_mock):
        def slow_search(query, media_type):
            time.sleep(0.05)
            return []

        search_mock.side_effect = slow_search
        update = Mock()
        update.effective_chat.id = 472943219
        context = Mock()
        context.bot.send_message = AsyncMock()

        items = asyncio.run(
            _search_prowlarr_with_progress(
                update,
                context,
                "Transformers: Dark of the Moon 2011",
                progress_interval=0.01,
            )
        )

        self.assertEqual(items, [])
        self.assertGreaterEqual(context.bot.send_message.await_count, 1)
        first_message = context.bot.send_message.await_args_list[0].kwargs["text"]
        self.assertIn("Prowlarr 仍在搜索", first_message)


if __name__ == "__main__":
    unittest.main()
