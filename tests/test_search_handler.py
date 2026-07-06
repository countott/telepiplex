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
    SEARCH_RESOLVE_METADATA,
    SEARCH_SELECT_RESULT,
    SEARCH_TASK_TTL_SECONDS,
    _clean_prowlarr_query,
    _extract_douban_metadata,
    _extract_douban_subject_urls,
    _fetch_media_page_title,
    _is_supported_http_download,
    _metadata_for_selected_release,
    _metadata_matches_plain_query,
    _plex_metadata_for_selected_release,
    _resolve_search_request,
    build_results_text,
    douban_search_command,
    format_size,
    find_command,
    get_pending_search_task,
    parse_douban_title,
    pending_search_tasks,
    resolve_plain_search_metadata,
    _search_prowlarr_with_progress,
    select_search_sub_category,
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

    def test_extract_douban_metadata_splits_chinese_title_from_mixed_title(self):
        metadata = _extract_douban_metadata(
            {
                "subject": {
                    "title": "嗜血法医：源罪 Dexter: Original Sin‎ (2024)",
                    "release_year": "2024",
                }
            }
        )

        self.assertEqual(
            metadata,
            {
                "source": "douban",
                "chinese_title": "嗜血法医：源罪",
                "english_title": "Dexter Original Sin",
                "year": "2024",
            },
        )

    def test_extract_douban_subject_urls_supports_redirect_relative_and_escaped_links(self):
        html = """
        <a href="/subject/1111111/">relative</a>
        <a href="https://www.douban.com/link2/?url=https%3A%2F%2Fmovie.douban.com%2Fsubject%2F2222222%2F">redirect</a>
        <a href="https:\\/\\/movie.douban.com\\/subject\\/3333333\\/">escaped</a>
        <a href="https://movie.douban.com/subject/2222222/">duplicate</a>
        """

        self.assertEqual(
            _extract_douban_subject_urls(html),
            [
                "https://movie.douban.com/subject/1111111/",
                "https://movie.douban.com/subject/2222222/",
                "https://movie.douban.com/subject/3333333/",
            ],
        )

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_imdb_link_uses_english_title_year_for_douban_reverse_lookup(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        suggestion_response = Mock()
        suggestion_response.json.return_value = {
            "d": [{"id": "tt32252772", "l": "Dexter: Original Sin", "y": 2024}]
        }
        search_response = Mock()
        search_response.text = '<a href="https://movie.douban.com/subject/36235376/">Dexter Original Sin</a>'
        subject_response = Mock()
        subject_response.json.return_value = {
            "subject": {
                "title": "嗜血法医：源罪 Dexter: Original Sin‎ (2024)",
                "release_year": "2024",
            }
        }
        mock_get.side_effect = [suggestion_response, search_response, subject_response]

        request = asyncio.run(_resolve_search_request("https://www.imdb.com/title/tt32252772/?ref_=cht_all_t_20"))

        self.assertEqual(request["query"], "Dexter Original Sin 2024")
        self.assertEqual(request["plex_metadata"]["chinese_title"], "嗜血法医：源罪")
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://v3.sg.media-imdb.com/suggestion/t/tt32252772.json")
        self.assertEqual(mock_get.call_args_list[1].args[0], "https://www.douban.com/search")
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"], {"cat": "1002", "q": "Dexter Original Sin 2024"})

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_imdb_link_falls_back_to_imdb_suggestion_when_page_title_is_empty(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        suggestion_response = Mock()
        suggestion_response.json.return_value = {
            "d": [{"id": "tt0773262", "l": "Dexter", "y": 2006}]
        }
        douban_response = Mock()
        douban_response.text = "<html></html>"
        mock_get.side_effect = [suggestion_response, douban_response]

        request = asyncio.run(_resolve_search_request("https://www.imdb.com/title/tt0773262/?ref_=cht_all_int_t_20"))

        self.assertEqual(request["query"], "Dexter 2006")
        self.assertIsNone(request["plex_metadata"])
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://v3.sg.media-imdb.com/suggestion/t/tt0773262.json")
        self.assertEqual(mock_get.call_args_list[1].args[0], "https://www.douban.com/search")

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_tvdb_link_uses_external_title_year_for_douban_reverse_lookup(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        tvdb_response = Mock()
        tvdb_response.text = '<html><head><title>Breaking Bad (2008) - TheTVDB.com</title></head></html>'
        search_response = Mock()
        search_response.text = '<a href="https://movie.douban.com/subject/23761370/">Breaking Bad</a>'
        subject_response = Mock()
        subject_response.json.return_value = {
            "subject": {"title": "绝命毒师 Breaking Bad (2008)", "release_year": "2008"}
        }
        mock_get.side_effect = [tvdb_response, search_response, subject_response]

        request = asyncio.run(_resolve_search_request("https://thetvdb.com/series/breaking-bad"))

        self.assertEqual(request["query"], "Breaking Bad 2008")
        self.assertEqual(request["plex_metadata"]["chinese_title"], "绝命毒师")
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"], {"cat": "1002", "q": "Breaking Bad 2008"})

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_tmdb_link_uses_external_title_year_for_douban_reverse_lookup(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        tmdb_response = Mock()
        tmdb_response.text = """
        <html><head>
          <meta property="og:title" content="The Grand Budapest Hotel (2014) | The Movie Database (TMDB)" />
        </head></html>
        """
        search_response = Mock()
        search_response.text = '<a href="https://movie.douban.com/subject/11525673/">The Grand Budapest Hotel</a>'
        subject_response = Mock()
        subject_response.json.return_value = {
            "subject": {
                "title": "布达佩斯大饭店",
                "original_title": "The Grand Budapest Hotel",
                "release_year": "2014",
            }
        }
        mock_get.side_effect = [tmdb_response, search_response, subject_response]

        request = asyncio.run(_resolve_search_request("https://www.themoviedb.org/movie/120-the-grand-budapest-hotel"))

        self.assertEqual(request["query"], "The Grand Budapest Hotel 2014")
        self.assertEqual(request["plex_metadata"]["chinese_title"], "布达佩斯大饭店")
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"], {"cat": "1002", "q": "The Grand Budapest Hotel 2014"})

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

    def test_metadata_for_selected_release_adds_release_title_without_mutating_task(self):
        task_metadata = {
            "source": "imdb",
            "english_title": "Dexter",
            "year": "2006",
            "external_ids": {"imdb": "tt0773262"},
            "evidence": [{"source": "imdb", "field": "title_year"}],
        }
        task = {"metadata": task_metadata}
        selected_item = {"title": "Dexter.S01.1080p.BluRay-GROUP"}

        metadata = _metadata_for_selected_release(task, selected_item)

        self.assertEqual(metadata["release_title"], "Dexter.S01.1080p.BluRay-GROUP")
        self.assertEqual(metadata["external_ids"], {"imdb": "tt0773262"})
        self.assertNotIn("release_title", task_metadata)

    @patch("app.handlers.search_handler.download_executor.submit")
    @patch("app.handlers.search_handler._resolve_selected_link", new_callable=AsyncMock)
    def test_search_sub_category_passes_metadata_to_download_task(self, resolve_link_mock, submit_mock):
        resolve_link_mock.return_value = "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567"
        init.bot_config = {
            "category_folder": [
                {
                    "name": "series",
                    "path_map": [{"name": "真人剧集", "path": "/真人剧集"}],
                }
            ]
        }
        pending_search_tasks["task-1"] = {
            "created_at": time.time(),
            "user_id": 472943219,
            "query": "Dexter 2006",
            "results": [],
            "plex_metadata": {
                "source": "douban",
                "chinese_title": "嗜血法医",
                "english_title": "Dexter",
                "year": "2006",
            },
            "metadata": {
                "source": "imdb",
                "english_title": "Dexter",
                "year": "2006",
                "external_ids": {"imdb": "tt0773262"},
            },
        }
        update = Mock()
        update.effective_user.id = 472943219
        update.callback_query.data = "search_path:task-1:0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()
        context.user_data = {
            "search_selected_main_category": "series",
            "search_selected_item": {
                "title": "Dexter.S01.1080p.BluRay-GROUP",
                "magnet_url": "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            },
        }

        asyncio.run(select_search_sub_category(update, context))

        submit_mock.assert_called_once()
        self.assertEqual(submit_mock.call_args.args[2], "/真人剧集")
        self.assertEqual(submit_mock.call_args.kwargs["metadata"]["release_title"], "Dexter.S01.1080p.BluRay-GROUP")
        self.assertEqual(submit_mock.call_args.kwargs["metadata"]["external_ids"], {"imdb": "tt0773262"})

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_plain_search_request_requires_metadata_when_douban_misses(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        search_response = Mock()
        search_response.text = "<html></html>"
        mock_get.return_value = search_response

        request = asyncio.run(_resolve_search_request("布达佩斯大饭店"))

        self.assertEqual(request["query"], "布达佩斯大饭店")
        self.assertIsNone(request["plex_metadata"])
        self.assertTrue(request["needs_metadata"])

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
    def test_resolve_plain_search_request_requires_metadata_when_douban_match_is_not_exact(self, mock_get):
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
        self.assertIsNone(request["plex_metadata"])
        self.assertTrue(request["needs_metadata"])

    def test_clean_prowlarr_query_removes_colons_and_dashes(self):
        self.assertEqual(
            _clean_prowlarr_query("Vivre sa vie：Film en douze tableaux"),
            "Vivre sa vie Film en douze tableaux",
        )
        self.assertEqual(
            _clean_prowlarr_query("Transformers: Dark-of-the-Moon — 2011"),
            "Transformers Dark of the Moon 2011",
        )

    @patch("app.handlers.search_handler._resolve_search_request", new_callable=AsyncMock)
    def test_find_command_prompts_for_metadata_when_plain_reverse_lookup_misses(self, resolve_mock):
        init.check_user = Mock(return_value=True)
        resolve_mock.return_value = {
            "query": "Vivre sa vie Film en douze tableaux",
            "plex_metadata": None,
            "needs_metadata": True,
        }
        update = Mock()
        update.message.from_user.id = 472943219
        update.message.reply_text = AsyncMock()
        context = Mock()
        context.args = ["Vivre", "sa", "vie:", "Film", "en", "douze", "tableaux"]
        context.user_data = {}

        state = asyncio.run(find_command(update, context))

        self.assertEqual(state, SEARCH_RESOLVE_METADATA)
        self.assertEqual(context.user_data["pending_plain_search_query"], "Vivre sa vie Film en douze tableaux")
        self.assertIn("豆瓣链接", update.message.reply_text.await_args.args[0])

    @patch("app.handlers.search_handler._send_search_results", new_callable=AsyncMock)
    @patch("app.handlers.search_handler._resolve_search_request", new_callable=AsyncMock)
    def test_find_command_runs_prowlarr_flow(self, resolve_mock, send_results_mock):
        init.check_user = Mock(return_value=True)
        resolve_mock.return_value = {
            "query": "The Grand Budapest Hotel 2014",
            "plex_metadata": {"source": "douban", "chinese_title": "布达佩斯大饭店"},
        }
        send_results_mock.return_value = SEARCH_SELECT_RESULT
        update = Mock()
        update.message.from_user.id = 472943219
        update.message.reply_text = AsyncMock()
        context = Mock()
        context.args = ["布达佩斯大饭店"]
        context.user_data = {}

        state = asyncio.run(find_command(update, context))

        self.assertEqual(state, SEARCH_SELECT_RESULT)
        send_results_mock.assert_awaited_once_with(
            update,
            context,
            "The Grand Budapest Hotel 2014",
            plex_metadata={"source": "douban", "chinese_title": "布达佩斯大饭店"},
        )

    @patch("app.handlers.search_handler._send_search_results", new_callable=AsyncMock)
    def test_s_command_is_douban_placeholder_and_does_not_run_prowlarr(self, send_results_mock):
        init.check_user = Mock(return_value=True)
        update = Mock()
        update.message.from_user.id = 472943219
        update.message.reply_text = AsyncMock()
        context = Mock()
        context.args = ["布达佩斯大饭店"]

        state = asyncio.run(douban_search_command(update, context))

        self.assertIsNone(state)
        send_results_mock.assert_not_awaited()
        self.assertIn("豆瓣搜索入口已预留", update.message.reply_text.await_args.args[0])

    @patch("app.handlers.search_handler._send_search_results", new_callable=AsyncMock)
    def test_plain_metadata_reply_uses_chinese_name_and_original_query(self, send_results_mock):
        init.check_user = Mock(return_value=True)
        send_results_mock.return_value = SEARCH_SELECT_RESULT
        update = Mock()
        update.message.from_user.id = 472943219
        update.message.text = "随心所欲"
        context = Mock()
        context.user_data = {"pending_plain_search_query": "Vivre sa vie Film en douze tableaux"}

        state = asyncio.run(resolve_plain_search_metadata(update, context))

        self.assertEqual(state, SEARCH_SELECT_RESULT)
        self.assertNotIn("pending_plain_search_query", context.user_data)
        send_results_mock.assert_awaited_once_with(
            update,
            context,
            "Vivre sa vie Film en douze tableaux",
            plex_metadata={
                "source": "search_query",
                "chinese_title": "随心所欲",
            },
        )

    @patch("app.handlers.search_handler._send_search_results", new_callable=AsyncMock)
    @patch("app.handlers.search_handler._resolve_search_request", new_callable=AsyncMock)
    def test_plain_metadata_reply_accepts_douban_link(self, resolve_mock, send_results_mock):
        init.check_user = Mock(return_value=True)
        send_results_mock.return_value = SEARCH_SELECT_RESULT
        metadata = {
            "source": "douban",
            "chinese_title": "随心所欲",
            "english_title": "Vivre sa vie",
            "year": "1962",
        }
        resolve_mock.return_value = {
            "query": "Vivre sa vie 1962",
            "plex_metadata": metadata,
        }
        update = Mock()
        update.message.from_user.id = 472943219
        update.message.text = "https://movie.douban.com/subject/1293374/"
        context = Mock()
        context.user_data = {"pending_plain_search_query": "Vivre sa vie Film en douze tableaux"}

        state = asyncio.run(resolve_plain_search_metadata(update, context))

        self.assertEqual(state, SEARCH_SELECT_RESULT)
        self.assertNotIn("pending_plain_search_query", context.user_data)
        send_results_mock.assert_awaited_once_with(
            update,
            context,
            "Vivre sa vie 1962",
            plex_metadata=metadata,
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
        self.assertRegex("https://www.themoviedb.org/movie/120-the-lord-of-the-rings", METADATA_URL_PATTERN)
        self.assertNotRegex("https://example.com/movie.mkv", METADATA_URL_PATTERN)
        self.assertNotRegex("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567", METADATA_URL_PATTERN)

    def test_http_download_messages_only_allow_supported_metadata_sites(self):
        self.assertTrue(_is_supported_http_download("https://movie.douban.com/subject/1234567/"))
        self.assertTrue(_is_supported_http_download("https://www.imdb.com/title/tt2278388/"))
        self.assertFalse(_is_supported_http_download("https://example.com/movie.mkv"))

    def test_build_results_text_contains_rank_score_size_seeders_indexer_features_and_summary(self):
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
            indexer_summary={
                "enabled_indexers": ["Indexer A", "Indexer B"],
                "result_sources": {"Indexer A": 1},
                "down_indexers": [{"source": "Indexer B", "message": "Query failed"}],
                "error": "",
            },
        )

        self.assertIn("The Grand Budapest Hotel 2014", text)
        self.assertIn("1. 评分: 88", text)
        self.assertIn("大小: 8.0 GB", text)
        self.assertIn("seeders: 32", text)
        self.assertIn("indexer: Indexer A", text)
        self.assertIn("特征: 1080p / WEB-DL / HEVC", text)
        self.assertIn("搜刮器总结", text)
        self.assertIn("结果来源: Indexer A x1", text)
        self.assertIn("疑似 Down: Indexer B - Query failed", text)

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
