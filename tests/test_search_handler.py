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
    SEARCH_CONFIRM_ENTRY_SCOPE,
    SEARCH_SELECT_RESULT,
    SEARCH_SELECT_SUB_CATEGORY,
    SEARCH_TASK_TTL_SECONDS,
    _backfill_missing_chinese_title,
    _clean_prowlarr_query,
    _extract_douban_metadata,
    _extract_douban_subject_urls,
    _fetch_media_page_title,
    _is_supported_http_download,
    _metadata_for_selected_release,
    _metadata_matches_plain_query,
    _resolve_entry_candidates,
    _naming_metadata_for_selected_release,
    _resolve_search_request,
    _send_confirmed_candidate_search,
    _send_single_series_info_card,
    _send_search_results,
    build_results_text,
    confirm_entry_scope,
    format_size,
    get_pending_search_task,
    parse_douban_title,
    pending_entry_confirmations,
    pending_search_tasks,
    search_command,
    _search_prowlarr_with_progress,
    select_search_result,
    select_search_sub_category,
)


class SearchHandlerHelpersTest(unittest.TestCase):
    def tearDown(self):
        pending_search_tasks.clear()
        pending_entry_confirmations.clear()

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
            request["naming_metadata"],
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
        self.assertEqual(request["naming_metadata"]["chinese_title"], "嗜血法医：源罪")
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
        self.assertIsNone(request["naming_metadata"])
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
        self.assertEqual(request["naming_metadata"]["chinese_title"], "绝命毒师")
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
        self.assertEqual(request["naming_metadata"]["chinese_title"], "布达佩斯大饭店")
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"], {"cat": "1002", "q": "The Grand Budapest Hotel 2014"})

    def test_naming_metadata_for_selected_release_adds_release_title_without_mutating_task(self):
        task_metadata = {
            "source": "douban",
            "chinese_title": "绝命毒师",
            "english_title": "Breaking Bad",
            "year": "2008",
        }
        task = {"naming_metadata": task_metadata}
        selected_item = {"title": "Breaking.Bad.1x02.1080p.WEB-DL"}

        metadata = _naming_metadata_for_selected_release(task, selected_item)

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
                {"name": "真人剧集", "path": "/真人剧集"},
            ]
        }
        pending_search_tasks["task-1"] = {
            "created_at": time.time(),
            "user_id": 472943219,
            "query": "Dexter 2006",
            "results": [],
            "naming_metadata": {
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
            "search_selected_item": {
                "title": "Dexter.S01.1080p.BluRay-GROUP",
                "magnet_url": "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            },
        }

        asyncio.run(select_search_sub_category(update, context))

        submit_mock.assert_called_once()
        edit_messages = [
            call.args[0]
            for call in update.callback_query.edit_message_text.await_args_list
            if call.args
        ]
        self.assertIn("正在解析下载链接", edit_messages[0])
        self.assertIn("已加入下载队列", edit_messages[-1])
        self.assertEqual(submit_mock.call_args.args[2], "/真人剧集")
        self.assertEqual(submit_mock.call_args.kwargs["metadata"]["release_title"], "Dexter.S01.1080p.BluRay-GROUP")
        self.assertEqual(submit_mock.call_args.kwargs["metadata"]["external_ids"], {"imdb": "tt0773262"})

    def test_search_result_selection_shows_save_directories_without_category_step(self):
        init.bot_config = {
            "category_folder": [
                {"name": "真人电影", "path": "/真人电影"},
                {"name": "动画剧集", "path": "/动画剧集"},
            ]
        }
        pending_search_tasks["task-2"] = {
            "created_at": time.time(),
            "user_id": 472943219,
            "query": "Dexter",
            "results": [
                {
                    "title": "Dexter.S01E01.1080p",
                    "magnet_url": "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
                }
            ],
        }
        update = Mock()
        update.effective_user.id = 472943219
        update.callback_query.data = "search_pick:task-2:0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()
        context.user_data = {}

        state = asyncio.run(select_search_result(update, context))

        self.assertEqual(state, SEARCH_SELECT_SUB_CATEGORY)
        update.callback_query.edit_message_text.assert_awaited_once()
        self.assertIn("请选择保存目录", update.callback_query.edit_message_text.await_args.args[0])
        button_texts = [
            button.text
            for row in update.callback_query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("📁 真人电影", button_texts)
        self.assertNotIn("📁 媒体", button_texts)

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_plain_search_request_blocks_when_douban_misses(self, mock_get):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        search_response = Mock()
        search_response.text = "<html></html>"
        mock_get.return_value = search_response

        request = asyncio.run(_resolve_search_request("布达佩斯大饭店"))

        self.assertIsNone(request)

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
            request["naming_metadata"],
            {
                "source": "douban",
                "chinese_title": "布达佩斯大饭店",
                "english_title": "The Grand Budapest Hotel",
                "year": "2014",
            },
        )
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://www.douban.com/search")
        self.assertEqual(mock_get.call_args_list[0].kwargs["params"], {"cat": "1002", "q": "布达佩斯大饭店"})

    @patch("app.handlers.search_handler.normalize_search_query_with_ai")
    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_plain_search_request_strips_ai_episode_scope_before_douban_lookup(
        self,
        mock_get,
        normalize_mock,
    ):
        old_bot_config = init.bot_config
        init.bot_config = {"search": {}}
        self.addCleanup(setattr, init, "bot_config", old_bot_config)
        normalize_mock.return_value = {
            "status": "ok",
            "lookup_candidates": [
                {
                    "query": "瑞克和莫蒂 第九季 第七集",
                    "title": "瑞克和莫蒂",
                    "scope": "episode",
                    "season_number": 9,
                    "episode_number": 7,
                }
            ],
            "warnings": [],
        }
        raw_search_response = Mock()
        raw_search_response.text = "<html></html>"
        stripped_search_response = Mock()
        stripped_search_response.text = '<a href="https://movie.douban.com/subject/36508123/">瑞克和莫蒂 第九季</a>'
        subject_response = Mock()
        subject_response.json.return_value = {
            "subject": {
                "title": "瑞克和莫蒂 第九季",
                "original_title": "Rick and Morty Season 9",
                "release_year": "2026",
            }
        }
        mock_get.side_effect = [raw_search_response, stripped_search_response, subject_response]

        request = asyncio.run(_resolve_search_request("瑞克和莫迪第九季第七集"))

        self.assertEqual(request["query"], "Rick and Morty Season 9 2026")
        self.assertEqual(request["metadata"]["selected_scope"], "episode")
        self.assertEqual(request["metadata"]["season_number"], 9)
        self.assertEqual(request["metadata"]["episode_number"], 7)
        self.assertEqual(mock_get.call_args_list[1].kwargs["params"], {"cat": "1002", "q": "瑞克和莫蒂 第九季"})

    @patch("app.handlers.search_handler.requests.get")
    def test_resolve_plain_search_request_blocks_when_douban_match_is_not_exact(self, mock_get):
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

        self.assertIsNone(request)

    def test_clean_prowlarr_query_removes_colons_and_dashes(self):
        self.assertEqual(
            _clean_prowlarr_query("Vivre sa vie：Film en douze tableaux"),
            "Vivre sa vie Film en douze tableaux",
        )
        self.assertEqual(
            _clean_prowlarr_query("Transformers: Dark-of-the-Moon — 2011"),
            "Transformers Dark of the Moon 2011",
        )

    @patch("app.handlers.search_handler._send_search_results", new_callable=AsyncMock)
    @patch("app.handlers.search_handler._resolve_entry_candidates", new_callable=AsyncMock)
    def test_search_command_blocks_plain_query_without_verified_entry(self, resolve_mock, send_results_mock):
        init.check_user = Mock(return_value=True)
        resolve_mock.return_value = {
            "status": "blocked_no_verified_match",
            "message": "未匹配到明确的影视条目，请提供豆瓣/TVDB/IMDb/TMDB 链接或更明确的关键词。",
        }
        update = Mock()
        update.message.from_user.id = 472943219
        update.message.reply_text = AsyncMock()
        context = Mock()
        context.args = ["Vivre", "sa", "vie:", "Film", "en", "douze", "tableaux"]
        context.user_data = {}

        state = asyncio.run(search_command(update, context))

        self.assertEqual(state, -1)
        update.message.reply_text.assert_awaited_once()
        self.assertIn("未匹配", update.message.reply_text.await_args.args[0])
        send_results_mock.assert_not_awaited()

    @patch("app.handlers.search_handler._send_search_results", new_callable=AsyncMock)
    @patch("app.handlers.search_handler._resolve_entry_candidates", new_callable=AsyncMock)
    def test_search_command_runs_prowlarr_flow(self, resolve_mock, send_results_mock):
        init.check_user = Mock(return_value=True)
        resolve_mock.return_value = {
            "status": "auto_confirm",
            "message": "已识别电影：布达佩斯大饭店 The Grand Budapest Hotel (2014)",
            "candidate": {
                "media_type": "movie",
                "scope": "movie",
                "title": "The Grand Budapest Hotel",
                "chinese_title": "布达佩斯大饭店",
                "year": "2014",
                "naming_metadata": {"source": "douban", "chinese_title": "布达佩斯大饭店"},
            },
        }
        send_results_mock.return_value = SEARCH_SELECT_RESULT
        update = Mock()
        update.message.from_user.id = 472943219
        update.message.reply_text = AsyncMock()
        context = Mock()
        context.args = ["布达佩斯大饭店"]
        context.user_data = {}

        state = asyncio.run(search_command(update, context))

        self.assertEqual(state, SEARCH_SELECT_RESULT)
        update.message.reply_text.assert_awaited_once()
        self.assertIn("已识别电影", update.message.reply_text.await_args.args[0])
        send_results_mock.assert_awaited_once_with(
            update,
            context,
            "The Grand Budapest Hotel 2014",
            naming_metadata={"source": "douban", "chinese_title": "布达佩斯大饭店"},
            metadata={
                "source": "confirmed",
                "media_type": "movie",
                "english_title": "The Grand Budapest Hotel",
                "chinese_title": "布达佩斯大饭店",
                "year": "2014",
                "query": "The Grand Budapest Hotel 2014",
                "external_ids": {},
                "selected_scope": "movie",
                "season_number": None,
                "episode_number": None,
                "cover_url": "",
            },
        )

    @patch("app.handlers.search_handler._resolve_entry_candidates", new_callable=AsyncMock)
    def test_search_command_shows_entry_scope_confirmation(self, resolve_mock):
        init.check_user = Mock(return_value=True)
        resolve_mock.return_value = {
            "status": "needs_confirmation",
            "candidates": [
                {
                    "media_type": "series",
                    "scope": "episode",
                    "title": "Breaking Bad",
                    "year": "2008",
                    "external_ids": {"tvdb": "81189"},
                    "cover_url": "https://artworks.thetvdb.com/banners/series/81189/posters/main.jpg",
                    "chinese_title": "绝命毒师",
                    "season_number": 2,
                    "episode_number": 5,
                    "recommended": True,
                    "metadata": {"media_type": "series", "season_number": 2, "episode_number": 5},
                }
            ],
        }
        update = Mock()
        update.message.from_user.id = 472943219
        update.message.reply_text = AsyncMock()
        update.message.reply_photo = AsyncMock()
        context = Mock()
        context.args = ["绝命毒师", "S02E05"]
        context.user_data = {}

        state = asyncio.run(search_command(update, context))

        self.assertEqual(state, SEARCH_CONFIRM_ENTRY_SCOPE)
        update.message.reply_photo.assert_awaited_once()
        self.assertEqual(
            update.message.reply_photo.await_args.kwargs["photo"],
            "https://artworks.thetvdb.com/banners/series/81189/posters/main.jpg",
        )
        self.assertIn("Breaking Bad", update.message.reply_photo.await_args.kwargs["caption"])
        self.assertIn("TVDB：`81189`", update.message.reply_photo.await_args.kwargs["caption"])
        update.message.reply_text.assert_awaited_once()
        self.assertIn("请确认", update.message.reply_text.await_args.args[0])
        button_text = update.message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text
        self.assertIn("推荐", button_text)
        self.assertIn("S02E05", button_text)

    def test_single_series_info_card_failure_does_not_block_confirmation_flow(self):
        init.logger = Mock()
        update = Mock()
        update.message.reply_photo = AsyncMock(side_effect=RuntimeError("bad photo"))

        asyncio.run(
            _send_single_series_info_card(
                update,
                [
                    {
                        "media_type": "series",
                        "title": "Breaking Bad",
                        "year": "2008",
                        "external_ids": {"tvdb": "81189"},
                        "cover_url": "https://example.invalid/poster.jpg",
                    }
                ],
            )
        )

        update.message.reply_photo.assert_awaited_once()
        init.logger.warn.assert_called_once()

    @patch("app.handlers.search_handler._lookup_tvdb_entries")
    @patch("app.handlers.search_handler._resolve_search_request", new_callable=AsyncMock)
    def test_entry_resolution_uses_ai_normalized_episode_scope(self, resolve_mock, tvdb_mock):
        resolve_mock.return_value = {
            "query": "Rick and Morty S09E07",
            "naming_metadata": None,
            "metadata": {
                "source": "ai_verified",
                "media_type": "series",
                "english_title": "Rick and Morty",
                "query": "Rick and Morty S09E07",
                "selected_scope": "episode",
                "season_number": 9,
                "episode_number": 7,
                "external_ids": {"tvdb": "275274"},
            },
        }
        tvdb_mock.return_value = (
            [],
            {
                "275274": [
                    {
                        "season_number": 9,
                        "episode_number": 7,
                        "aired": "2026-06-30",
                    }
                ]
            },
        )

        resolution = asyncio.run(_resolve_entry_candidates("瑞克和莫迪第九季第七集"))

        self.assertEqual(resolution["status"], "needs_confirmation")
        candidate = resolution["candidates"][0]
        self.assertEqual(candidate["media_type"], "series")
        self.assertEqual(candidate["scope"], "episode")
        self.assertEqual(candidate["season_number"], 9)
        self.assertEqual(candidate["episode_number"], 7)

    @patch("app.handlers.search_handler._lookup_tvdb_entries")
    @patch("app.handlers.search_handler._resolve_search_request", new_callable=AsyncMock)
    def test_entry_resolution_reports_tvdb_episode_lookup_unavailable(self, resolve_mock, tvdb_mock):
        resolve_mock.return_value = {
            "query": "Rick and Morty S09E07",
            "naming_metadata": None,
            "metadata": {
                "source": "confirmed",
                "media_type": "series",
                "english_title": "Rick and Morty",
                "query": "Rick and Morty S09E07",
                "selected_scope": "episode",
                "season_number": 9,
                "episode_number": 7,
                "external_ids": {"tvdb": "275274"},
            },
        }
        tvdb_mock.return_value = ([], {"275274": None})

        resolution = asyncio.run(_resolve_entry_candidates("Rick and Morty S09E07"))

        self.assertEqual(resolution["status"], "blocked_tvdb_unavailable")
        self.assertIn("TVDB 剧集列表暂时不可用", resolution["message"])
        self.assertNotIn("尚未播出或不存在", resolution["message"])

    @patch("app.handlers.search_handler._lookup_tvdb_entries")
    @patch("app.handlers.search_handler._resolve_search_request", new_callable=AsyncMock)
    def test_entry_resolution_reports_unknown_air_date_separately(self, resolve_mock, tvdb_mock):
        resolve_mock.return_value = {
            "query": "Rick and Morty S09E07",
            "naming_metadata": None,
            "metadata": {
                "source": "confirmed",
                "media_type": "series",
                "english_title": "Rick and Morty",
                "query": "Rick and Morty S09E07",
                "selected_scope": "episode",
                "season_number": 9,
                "episode_number": 7,
                "external_ids": {"tvdb": "275274"},
            },
        }
        tvdb_mock.return_value = (
            [],
            {
                "275274": [
                    {
                        "season_number": 9,
                        "episode_number": 7,
                        "aired": "",
                    }
                ]
            },
        )

        resolution = asyncio.run(_resolve_entry_candidates("Rick and Morty S09E07"))

        self.assertEqual(resolution["status"], "blocked_air_date_unknown")
        self.assertIn("缺少播出日期", resolution["message"])
        self.assertNotIn("尚未播出或不存在", resolution["message"])

    @patch("app.handlers.search_handler._lookup_tvdb_entries")
    @patch("app.handlers.search_handler.normalize_search_query_with_ai")
    @patch("app.handlers.search_handler._resolve_search_request", new_callable=AsyncMock)
    def test_complex_episode_query_uses_tvdb_before_ai_fallback(
        self,
        resolve_mock,
        normalize_mock,
        tvdb_mock,
    ):
        resolve_mock.return_value = None
        normalize_mock.return_value = {
            "status": "ok",
            "lookup_candidates": [
                {
                    "query": "Rick and Morty Season 9 Episode 7",
                    "title": "Rick and Morty",
                    "scope": "episode",
                    "season_number": 9,
                    "episode_number": 7,
                }
            ],
            "warnings": [],
        }
        tvdb_mock.return_value = (
            [
                {
                    "media_type": "series",
                    "scope": "whole_series",
                    "title": "Rick and Morty",
                    "english_title": "Rick and Morty",
                    "year": "2013",
                    "external_ids": {"tvdb": "275274"},
                    "source": "tvdb",
                }
            ],
            {
                "275274": [
                    {
                        "season_number": 9,
                        "episode_number": 7,
                        "aired": "2026-07-06",
                    }
                ]
            },
        )

        resolution = asyncio.run(_resolve_entry_candidates("Rick and morty s09e07"))

        self.assertEqual(resolution["status"], "needs_confirmation")
        resolve_mock.assert_awaited_once_with("Rick and morty s09e07", allow_ai_fallback=False)
        normalize_mock.assert_not_called()
        tvdb_intent = tvdb_mock.call_args.args[0]
        self.assertEqual(tvdb_intent["title"], "Rick and morty")
        self.assertEqual(resolution["candidates"][0]["title"], "Rick and Morty")
        self.assertEqual(resolution["candidates"][0]["season_number"], 9)
        self.assertEqual(resolution["candidates"][0]["episode_number"], 7)

    @patch("app.handlers.search_handler._lookup_tvdb_entries")
    @patch("app.handlers.search_handler.normalize_search_query_with_ai")
    @patch("app.handlers.search_handler._resolve_search_request", new_callable=AsyncMock)
    def test_ai_fallback_candidate_reenters_douban_and_tvdb_lookup_chain(
        self,
        resolve_mock,
        normalize_mock,
        tvdb_mock,
    ):
        resolve_mock.side_effect = [None, None]
        normalize_mock.return_value = {
            "status": "ok",
            "lookup_candidates": [
                {
                    "query": "Rick and Morty Season 8 Episode 3",
                    "title": "Rick and Morty",
                    "scope": "episode",
                    "season_number": 8,
                    "episode_number": 3,
                }
            ],
            "warnings": [],
        }
        tvdb_mock.side_effect = [
            ([], {}),
            (
                [
                    {
                        "media_type": "series",
                        "scope": "whole_series",
                        "title": "Rick and Morty",
                        "english_title": "Rick and Morty",
                        "year": "2013",
                        "external_ids": {"tvdb": "275274"},
                        "source": "tvdb",
                    }
                ],
                {
                    "275274": [
                        {
                            "season_number": 8,
                            "episode_number": 3,
                            "aired": "2025-06-08",
                        }
                    ]
                },
            ),
        ]

        resolution = asyncio.run(_resolve_entry_candidates("瑞克和莫迪season8e03"))

        self.assertEqual(resolution["status"], "needs_confirmation")
        self.assertEqual(resolve_mock.await_args_list[0].args[0], "瑞克和莫迪season8e03")
        self.assertEqual(resolve_mock.await_args_list[0].kwargs, {"allow_ai_fallback": False})
        self.assertEqual(resolve_mock.await_args_list[1].args[0], "Rick and Morty Season 8")
        self.assertEqual(resolve_mock.await_args_list[1].kwargs, {"allow_ai_fallback": False})
        normalize_mock.assert_called_once_with("瑞克和莫迪season8e03")
        tvdb_intent = tvdb_mock.call_args_list[1].args[0]
        self.assertEqual(tvdb_intent["title"], "Rick and Morty")
        self.assertEqual(tvdb_intent["scope"], "episode")
        self.assertEqual(resolution["candidates"][0]["season_number"], 8)
        self.assertEqual(resolution["candidates"][0]["episode_number"], 3)

    @patch("app.handlers.search_handler._send_search_results", new_callable=AsyncMock)
    def test_confirm_entry_scope_generates_episode_query(self, send_results_mock):
        send_results_mock.return_value = SEARCH_SELECT_RESULT
        pending_entry_confirmations["confirm-1"] = {
            "created_at": time.time(),
            "user_id": 472943219,
            "candidates": [
                {
                    "media_type": "series",
                    "scope": "episode",
                    "title": "Breaking Bad",
                    "season_number": 2,
                    "episode_number": 5,
                    "metadata": {"media_type": "series", "season_number": 2, "episode_number": 5},
                }
            ],
        }
        update = Mock()
        update.effective_user.id = 472943219
        update.callback_query.data = "entry_confirm:confirm-1:0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()
        context.user_data = {}

        state = asyncio.run(confirm_entry_scope(update, context))

        self.assertEqual(state, SEARCH_SELECT_RESULT)
        send_results_mock.assert_awaited_once_with(
            update,
            context,
            "Breaking Bad S02E05",
            naming_metadata={
                "source": "confirmed",
                "media_type": "series",
                "chinese_title": "",
                "english_title": "Breaking Bad",
                "year": "",
            },
            metadata={"media_type": "series", "season_number": 2, "episode_number": 5},
        )

    @patch("app.handlers.search_handler.get_prowlarr_indexer_summary", return_value={})
    @patch("app.handlers.search_handler.rank_releases")
    @patch("app.handlers.search_handler._search_prowlarr_with_progress", new_callable=AsyncMock)
    @patch("app.handlers.search_handler._fetch_douban_metadata_for_external_title")
    def test_search_results_backfill_missing_chinese_title_after_prowlarr_results(
        self,
        douban_lookup_mock,
        prowlarr_mock,
        rank_mock,
        indexer_mock,
    ):
        douban_lookup_mock.return_value = (
            {
                "source": "douban",
                "chinese_title": "瑞克和莫蒂 第九季",
                "english_title": "Rick and Morty Season 9",
                "year": "2026",
            },
            "Rick and Morty Season 9 2026",
        )
        prowlarr_mock.return_value = [{"title": "Rick.and.Morty.S09E07.1080p", "magnet_url": "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567"}]
        rank_mock.return_value = prowlarr_mock.return_value
        naming_metadata = {
            "media_type": "series",
            "source": "tvdb",
            "english_title": "Rick and Morty",
            "chinese_title": "",
            "year": "2026",
        }
        metadata = {
            "source": "confirmed",
            "media_type": "series",
            "english_title": "Rick and Morty",
            "chinese_title": "",
            "year": "2026",
            "external_ids": {"tvdb": "275274"},
            "selected_scope": "episode",
            "season_number": 9,
            "episode_number": 7,
        }
        update = Mock()
        update.callback_query = None
        update.message.reply_text = AsyncMock()
        update.effective_user.id = 472943219
        update.effective_chat.id = 472943219
        context = Mock()
        context.bot.send_message = AsyncMock()

        state = asyncio.run(
            _send_search_results(
                update,
                context,
                "Rick and Morty S09E07",
                naming_metadata=naming_metadata,
                metadata=metadata,
            )
        )

        self.assertEqual(state, SEARCH_SELECT_RESULT)
        douban_lookup_mock.assert_called_once_with("Rick and Morty", "2026")
        task = next(iter(pending_search_tasks.values()))
        self.assertEqual(task["naming_metadata"]["chinese_title"], "瑞克和莫蒂 第九季")
        self.assertEqual(task["naming_metadata"]["english_title"], "Rick and Morty")
        self.assertEqual(task["metadata"]["external_ids"], {"tvdb": "275274"})
        self.assertEqual(task["metadata"]["season_number"], 9)
        self.assertEqual(task["metadata"]["chinese_title"], "瑞克和莫蒂 第九季")

    @patch("app.handlers.search_handler.infer_metadata_backfill_with_ai", create=True)
    @patch("app.handlers.search_handler._fetch_douban_metadata_for_external_title")
    def test_missing_chinese_title_backfill_uses_ai_when_douban_misses(
        self,
        douban_lookup_mock,
        ai_backfill_mock,
    ):
        douban_lookup_mock.return_value = (None, "Rick and Morty 2026")
        ai_backfill_mock.return_value = {
            "source": "ai_metadata_backfill",
            "media_type": "series",
            "chinese_title": "瑞克和莫蒂 第九季",
            "english_title": "Rick and Morty",
            "year": "2026",
            "external_ids": {"tvdb": "275274"},
        }
        naming_metadata = {
            "media_type": "series",
            "source": "tvdb",
            "english_title": "Rick and Morty",
            "chinese_title": "",
            "year": "2026",
        }
        metadata = {
            "source": "confirmed",
            "media_type": "series",
            "english_title": "Rick and Morty",
            "chinese_title": "",
            "year": "2026",
            "external_ids": {"tvdb": "275274"},
            "selected_scope": "episode",
            "season_number": 9,
            "episode_number": 7,
        }

        backfilled_naming, backfilled_metadata = asyncio.run(
            _backfill_missing_chinese_title(naming_metadata, metadata)
        )

        ai_context = ai_backfill_mock.call_args.args[0]
        self.assertEqual(ai_context["english_title"], "Rick and Morty")
        self.assertEqual(ai_context["year"], "2026")
        self.assertEqual(ai_context["external_ids"], {"tvdb": "275274"})
        self.assertEqual(backfilled_naming["chinese_title"], "瑞克和莫蒂 第九季")
        self.assertEqual(backfilled_naming["english_title"], "Rick and Morty")
        self.assertEqual(backfilled_metadata["chinese_title"], "瑞克和莫蒂 第九季")
        self.assertEqual(backfilled_metadata["external_ids"], {"tvdb": "275274"})
        self.assertEqual(backfilled_metadata["evidence"][-1]["source"], "ai_metadata_backfill")

    @patch("app.handlers.search_handler._send_search_results", new_callable=AsyncMock)
    def test_confirm_entry_scope_uses_english_original_title_for_movie_query(self, send_results_mock):
        send_results_mock.return_value = SEARCH_SELECT_RESULT
        pending_entry_confirmations["confirm-2"] = {
            "created_at": time.time(),
            "user_id": 472943219,
            "candidates": [
                {
                    "media_type": "movie",
                    "scope": "movie",
                    "title": "变形金刚4：绝迹重生",
                    "chinese_title": "变形金刚4：绝迹重生",
                    "english_title": "Transformers: Age-of-Extinction",
                    "year": "2014",
                    "metadata": {"media_type": "movie", "year": "2014"},
                }
            ],
        }
        update = Mock()
        update.effective_user.id = 472943219
        update.callback_query.data = "entry_confirm:confirm-2:0"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = Mock()
        context.user_data = {}

        state = asyncio.run(confirm_entry_scope(update, context))

        self.assertEqual(state, SEARCH_SELECT_RESULT)
        send_results_mock.assert_awaited_once_with(
            update,
            context,
            "Transformers Age of Extinction 2014",
            naming_metadata={
                "source": "confirmed",
                "media_type": "movie",
                "chinese_title": "变形金刚4：绝迹重生",
                "english_title": "Transformers: Age-of-Extinction",
                "year": "2014",
            },
            metadata={"media_type": "movie", "year": "2014"},
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
            "naming_metadata": {
                "source": "search_query",
                "chinese_title": "布达佩斯大饭店",
            },
        }
        selected_item = {"title": "The.Grand.Budapest.Hotel.2014.1080p.BluRay.x265-GROUP"}

        metadata = _naming_metadata_for_selected_release(task, selected_item)

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

    @patch("app.handlers.search_handler.search_prowlarr")
    def test_search_prowlarr_with_progress_merges_movie_and_tv_categories(self, search_mock):
        def category_search(query, media_type):
            if media_type == "movie":
                return [
                    {
                        "title": "Dexter.Original.Sin.S01E01.1080p.WEB-DL",
                        "magnet_url": "magnet:?xt=urn:btih:111",
                    }
                ]
            if media_type == "tv":
                return [
                    {
                        "title": "Dexter.Original.Sin.S01E01.1080p.WEB-DL",
                        "magnet_url": "magnet:?xt=urn:btih:111",
                    },
                    {
                        "title": "Dexter.Original.Sin.S01E02.1080p.WEB-DL",
                        "magnet_url": "magnet:?xt=urn:btih:222",
                    },
                ]
            return []

        search_mock.side_effect = category_search
        update = Mock()
        update.effective_chat.id = 472943219
        context = Mock()
        context.bot.send_message = AsyncMock()

        items = asyncio.run(_search_prowlarr_with_progress(update, context, "Dexter Original Sin 2024"))

        self.assertEqual([item["magnet_url"] for item in items], ["magnet:?xt=urn:btih:111", "magnet:?xt=urn:btih:222"])
        self.assertEqual(
            [call.args for call in search_mock.call_args_list],
            [("Dexter Original Sin 2024", "movie"), ("Dexter Original Sin 2024", "tv")],
        )


if __name__ == "__main__":
    unittest.main()
