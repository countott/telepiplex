import asyncio
import inspect
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.handlers import search_handler
from app.utils import search_resolution
from app.utils.search_plan import (
    TemporarySpecialAllocator,
    confirm_media_metadata,
    finalize_search_plan,
)


class DoubanMetadataFusionTest(unittest.TestCase):
    def test_douban_series_keeps_type_subject_and_cover(self):
        metadata = search_handler._extract_douban_metadata(
            {
                "id": "35314632",
                "title": "黑暗荣耀",
                "original_title": "더 글로리",
                "aka": ["The Glory"],
                "year": "2022",
                "type": "tv",
                "subtype": "tv",
                "is_tv": True,
                "cover_url": "https://img.example/glory.jpg",
                "pic": {"large": "https://img.example/glory-large.jpg"},
            }
        )

        self.assertEqual(metadata["media_type"], "series")
        self.assertEqual(metadata["subject_id"], "35314632")
        self.assertEqual(metadata["cover_url"], "https://img.example/glory.jpg")
        self.assertEqual(metadata["english_title"], "The Glory")

    def test_chinese_movie_without_latin_title_remains_usable(self):
        metadata = search_handler._extract_douban_metadata(
            {
                "id": "1",
                "title": "中文电影",
                "year": "2024",
                "type": "movie",
                "pic": {"large": "https://img.example/movie.jpg"},
            }
        )

        self.assertIsNotNone(metadata)
        self.assertEqual(metadata["media_type"], "movie")
        self.assertEqual(metadata["english_title"], "")
        self.assertEqual(metadata["cover_url"], "https://img.example/movie.jpg")


class PrimaryEntryMergeTest(unittest.TestCase):
    def test_douban_and_tvdb_glory_merge_into_one_series(self):
        merged = search_resolution.merge_primary_entries(
            [
                {
                    "source": "douban",
                    "media_type": "series",
                    "title": "The Glory",
                    "chinese_title": "黑暗荣耀",
                    "english_title": "The Glory",
                    "year": "2022",
                    "external_ids": {"douban_subject": "35314632"},
                    "cover_url": "https://img.example/douban.jpg",
                    "cover_source": "douban",
                },
                {
                    "source": "tvdb",
                    "media_type": "series",
                    "scope": "whole_series",
                    "title": "더 글로리",
                    "english_title": "The Glory",
                    "year": "2022",
                    "aliases": ["The Glory (2022)"],
                    "external_ids": {"tvdb": "411469"},
                    "cover_url": "https://img.example/tvdb.jpg",
                    "cover_source": "tvdb",
                },
            ]
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["media_type"], "series")
        self.assertEqual(merged[0]["english_title"], "The Glory")
        self.assertEqual(merged[0]["chinese_title"], "黑暗荣耀")
        self.assertEqual(
            merged[0]["external_ids"],
            {"douban_subject": "35314632", "tvdb": "411469"},
        )
        self.assertEqual(merged[0]["cover_url"], "https://img.example/tvdb.jpg")
        self.assertEqual(merged[0]["cover_source"], "tvdb")

    def test_same_title_movie_and_series_remain_separate(self):
        merged = search_resolution.merge_primary_entries(
            [
                {
                    "source": "douban",
                    "media_type": "movie",
                    "english_title": "The Glory",
                    "year": "2022",
                },
                {
                    "source": "tvdb",
                    "media_type": "series",
                    "english_title": "The Glory",
                    "year": "2022",
                    "external_ids": {"tvdb": "411469"},
                },
            ]
        )

        self.assertEqual(len(merged), 2)

    def test_tvdb_without_cover_keeps_douban_fallback(self):
        merged = search_resolution.merge_primary_entries(
            [
                {
                    "source": "douban",
                    "media_type": "movie",
                    "chinese_title": "中文电影",
                    "english_title": "Chinese Movie",
                    "year": "2024",
                    "cover_url": "https://img.example/douban-movie.jpg",
                    "cover_source": "douban",
                },
                {
                    "source": "tvdb",
                    "media_type": "movie",
                    "english_title": "Chinese Movie",
                    "year": "2024",
                    "external_ids": {"tvdb_movie": "123"},
                    "cover_url": "",
                    "cover_source": "",
                },
            ]
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["cover_url"], "https://img.example/douban-movie.jpg")
        self.assertEqual(merged[0]["cover_source"], "douban")

    def test_one_douban_entry_does_not_absorb_multiple_tvdb_entries(self):
        merged = search_resolution.merge_primary_entries(
            [
                {
                    "source": "douban",
                    "media_type": "series",
                    "chinese_title": "黑暗荣耀",
                    "english_title": "The Glory",
                    "year": "2022",
                },
                {
                    "source": "tvdb",
                    "media_type": "series",
                    "english_title": "The Glory",
                    "year": "2022",
                    "external_ids": {"tvdb": "411469"},
                },
                {
                    "source": "tvdb",
                    "media_type": "series",
                    "english_title": "The Glory",
                    "year": "2022",
                    "external_ids": {"tvdb": "999999"},
                },
            ]
        )

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["external_ids"]["tvdb"], "411469")


class PrimaryResolutionIntegrationTest(unittest.TestCase):
    def test_tvdb_series_entry_uses_normalized_english_title_and_cover(self):
        entry = search_handler._entry_from_tvdb_series(
            {
                "tvdb_series_id": "411469",
                "name": "더 글로리",
                "english_title": "The Glory",
                "aliases": ["The Glory (2022)"],
                "year": "2022",
                "cover_url": "https://img.example/tvdb.jpg",
            }
        )

        self.assertEqual(entry["title"], "The Glory")
        self.assertEqual(entry["english_title"], "The Glory")
        self.assertEqual(entry["cover_url"], "https://img.example/tvdb.jpg")
        self.assertEqual(entry["aliases"], ["The Glory (2022)"])

    def test_tvdb_movie_entry_is_supported(self):
        entry = search_handler._entry_from_tvdb_movie(
            {
                "tvdb_movie_id": "123",
                "name": "中文电影",
                "english_title": "Chinese Movie",
                "year": "2024",
                "cover_url": "https://img.example/tvdb-movie.jpg",
            }
        )

        self.assertEqual(entry["media_type"], "movie")
        self.assertEqual(entry["external_ids"], {"tvdb": "123"})

    @patch.object(search_handler, "_lookup_tvdb_entries")
    @patch.object(search_handler, "_resolve_search_request", new_callable=AsyncMock)
    def test_explicit_douban_link_locks_type_and_merges_tvdb_before_candidates(self, request_mock, tvdb_mock):
        request_mock.return_value = {
            "query": "The Glory 2022",
            "naming_metadata": {
                "source": "douban",
                "media_type": "series",
                "chinese_title": "黑暗荣耀",
                "english_title": "The Glory",
                "year": "2022",
                "cover_url": "https://img.example/douban.jpg",
            },
            "metadata": {
                "source": "douban",
                "media_type": "series",
                "chinese_title": "黑暗荣耀",
                "english_title": "The Glory",
                "year": "2022",
                "external_ids": {"douban_subject": "35314632"},
                "cover_url": "https://img.example/douban.jpg",
            },
        }
        tvdb_mock.return_value = (
            [
                {
                    "source": "tvdb",
                    "media_type": "series",
                    "scope": "whole_series",
                    "title": "The Glory",
                    "english_title": "The Glory",
                    "year": "2022",
                    "external_ids": {"tvdb": "411469"},
                    "cover_url": "https://img.example/tvdb.jpg",
                    "cover_source": "tvdb",
                }
            ],
            {"411469": []},
        )

        entries, episodes, intent = asyncio.run(
            search_handler._resolve_entries_with_primary_sources(
                "https://movie.douban.com/subject/35314632/",
                {
                    "raw_query": "https://movie.douban.com/subject/35314632/",
                    "title": "黑暗荣耀",
                    "scope": "movie_or_series",
                    "year": "",
                },
            )
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["english_title"], "The Glory")
        self.assertEqual(entries[0]["external_ids"]["tvdb"], "411469")
        self.assertEqual(intent["media_type"], "series")
        self.assertEqual(episodes, {"411469": []})

    @patch.object(search_handler, "_lookup_tvdb_entries")
    @patch.object(search_handler, "_resolve_search_request", new_callable=AsyncMock)
    def test_plain_title_douban_hit_keeps_movie_and_series_lookup_ambiguous(self, request_mock, tvdb_mock):
        request_mock.return_value = {
            "query": "Someday or One Day 2019",
            "naming_metadata": {
                "source": "douban",
                "media_type": "series",
                "chinese_title": "想见你",
                "english_title": "Someday or One Day",
                "year": "2019",
            },
            "metadata": {
                "source": "douban",
                "media_type": "series",
                "chinese_title": "想见你",
                "english_title": "Someday or One Day",
                "year": "2019",
                "external_ids": {"douban_subject": "30468961"},
            },
        }
        tvdb_mock.return_value = (
            [
                {
                    "source": "tvdb",
                    "media_type": "movie",
                    "scope": "movie",
                    "title": "Someday or One Day",
                    "english_title": "Someday or One Day",
                    "year": "2022",
                    "external_ids": {"tvdb": "movie-2022"},
                },
                {
                    "source": "tvdb",
                    "media_type": "series",
                    "scope": "whole_series",
                    "title": "Someday or One Day",
                    "english_title": "Someday or One Day",
                    "year": "2019",
                    "external_ids": {"tvdb": "series-2019"},
                },
            ],
            {"series-2019": []},
        )
        base_intent = {
            "raw_query": "想见你",
            "title": "想见你",
            "scope": "movie_or_series",
            "year": "",
        }

        entries, episodes, intent = asyncio.run(
            search_handler._resolve_entries_with_primary_sources("想见你", base_intent)
        )

        lookup_intent = tvdb_mock.call_args.args[0]
        self.assertEqual(lookup_intent, base_intent)
        self.assertEqual(intent, base_intent)
        self.assertEqual(
            {(entry["media_type"], entry["year"]) for entry in entries},
            {("series", "2019"), ("movie", "2022")},
        )
        self.assertEqual(episodes, {"series-2019": []})

    @patch.object(search_handler, "search_tvdb_movies", create=True)
    @patch.object(search_handler, "search_tvdb_series")
    def test_confirmed_movie_type_only_queries_tvdb_movies(self, series_mock, movies_mock):
        movies_mock.return_value = []

        entries, episodes = search_handler._lookup_tvdb_entries(
            {"title": "中文电影", "year": "2024", "scope": "movie_or_series", "media_type": "movie"}
        )

        self.assertEqual(entries, [])
        self.assertEqual(episodes, {})
        movies_mock.assert_called_once_with("中文电影", year="2024")
        series_mock.assert_not_called()


class CoverAndHandoffTest(unittest.IsolatedAsyncioTestCase):
    def test_confirmation_candidate_preserves_cover_provenance(self):
        candidates = search_resolution.build_confirmation_candidates(
            [
                {
                    "media_type": "movie",
                    "english_title": "Chinese Movie",
                    "year": "2024",
                    "external_ids": {"tvdb": "123"},
                    "tvdb_movie_id": "123",
                    "cover_url": "https://img.example/tvdb-movie.jpg",
                    "cover_source": "tvdb",
                    "metadata": {"source": "tvdb"},
                    "naming_metadata": {"source": "douban"},
                }
            ],
            {"scope": "movie_or_series"},
        )

        self.assertEqual(candidates[0]["cover_source"], "tvdb")
        self.assertEqual(candidates[0]["tvdb_movie_id"], "123")
        self.assertEqual(candidates[0]["metadata"], {"source": "tvdb"})
        self.assertEqual(candidates[0]["naming_metadata"], {"source": "douban"})

    async def test_douban_movie_cover_is_not_overwritten_by_series_candidate(self):
        candidates = [
            {
                "media_type": "movie",
                "cover_url": "https://img.example/movie.jpg",
                "cover_source": "douban",
                "external_ids": {},
            },
            {
                "media_type": "series",
                "cover_url": "https://img.example/series.jpg",
                "cover_source": "tvdb",
                "external_ids": {"tvdb": "2"},
                "tvdb_series_id": "2",
            },
        ]

        result = await search_handler._backfill_candidate_covers(candidates)

        self.assertEqual(result[0]["cover_url"], "https://img.example/movie.jpg")
        self.assertEqual(result[1]["cover_url"], "https://img.example/series.jpg")

    @patch.object(search_handler, "get_tvdb_movie_artwork_url")
    async def test_tvdb_movie_artwork_replaces_douban_fallback_for_same_candidate(self, artwork_mock):
        artwork_mock.return_value = "https://img.example/tvdb-movie.jpg"
        candidates = [
            {
                "media_type": "movie",
                "cover_url": "https://img.example/douban-movie.jpg",
                "cover_source": "douban",
                "external_ids": {"tvdb": "123"},
                "tvdb_movie_id": "123",
            }
        ]

        result = await search_handler._backfill_candidate_covers(candidates)

        self.assertEqual(result[0]["cover_url"], "https://img.example/tvdb-movie.jpg")
        self.assertEqual(result[0]["cover_source"], "tvdb")

    def test_candidate_metadata_overlays_stale_nested_movie_type(self):
        candidate = {
            "media_type": "series",
            "scope": "whole_series",
            "chinese_title": "黑暗荣耀",
            "english_title": "The Glory",
            "year": "2022",
            "external_ids": {"tvdb": "411469"},
            "cover_url": "https://img.example/glory.jpg",
            "metadata": {"media_type": "movie", "selected_scope": "movie", "english_title": "더 글로리"},
        }

        metadata = search_handler._candidate_search_metadata(candidate)

        self.assertEqual(metadata["media_type"], "series")
        self.assertEqual(metadata["selected_scope"], "whole_series")
        self.assertEqual(metadata["english_title"], "The Glory")
        self.assertEqual(metadata["external_ids"], {"tvdb": "411469"})
        self.assertEqual(metadata["cover_url"], "https://img.example/glory.jpg")

    def test_candidate_naming_metadata_overlays_stale_nested_values(self):
        candidate = {
            "media_type": "series",
            "chinese_title": "黑暗荣耀",
            "english_title": "The Glory",
            "year": "2022",
            "naming_metadata": {"media_type": "movie", "english_title": "더 글로리"},
        }

        metadata = search_handler._candidate_naming_metadata(candidate)

        self.assertEqual(metadata["media_type"], "series")
        self.assertEqual(metadata["chinese_title"], "黑暗荣耀")
        self.assertEqual(metadata["english_title"], "The Glory")

    async def test_movie_info_card_sends_selected_movie_cover(self):
        reply_photo = AsyncMock()
        update = SimpleNamespace(message=SimpleNamespace(reply_photo=reply_photo))

        await search_handler._send_candidate_info_card(
            update,
            {
                "media_type": "movie",
                "title": "Chinese Movie",
                "chinese_title": "中文电影",
                "year": "2024",
                "cover_url": "https://img.example/movie.jpg",
                "external_ids": {"tvdb": "123"},
            },
        )

        reply_photo.assert_awaited_once()
        self.assertIn("已识别电影", reply_photo.await_args.kwargs["caption"])
        self.assertEqual(reply_photo.await_args.kwargs["photo"], "https://img.example/movie.jpg")

    @patch.object(search_handler, "_send_search_results", new_callable=AsyncMock)
    @patch.object(search_handler, "_send_candidate_info_card", new_callable=AsyncMock, create=True)
    async def test_confirmed_search_sends_selected_card_before_search(self, card_mock, search_mock):
        search_mock.return_value = 42
        candidate = {
            "media_type": "movie",
            "scope": "movie",
            "english_title": "Chinese Movie",
            "chinese_title": "中文电影",
            "year": "2024",
            "cover_url": "https://img.example/movie.jpg",
        }

        result = await search_handler._send_confirmed_candidate_search(object(), object(), candidate)

        self.assertEqual(result, 42)
        card_mock.assert_awaited_once()
        search_mock.assert_awaited_once()

    @patch.object(search_handler, "search_prowlarr")
    async def test_prowlarr_category_lookup_uses_confirmed_series_type(self, search_mock):
        search_mock.return_value = []
        self.assertIn(
            "media_type",
            inspect.signature(search_handler._search_prowlarr_release_categories).parameters,
        )

        search_handler._search_prowlarr_release_categories(
            "Someday or One Day 2019",
            media_type="series",
        )

        search_mock.assert_called_once_with("Someday or One Day 2019", "tv")

    @patch.object(search_handler, "_send_search_message", new_callable=AsyncMock)
    @patch.object(search_handler, "_search_prowlarr_with_progress", new_callable=AsyncMock)
    @patch.object(search_handler, "_reply_or_send", new_callable=AsyncMock)
    async def test_search_results_forwards_confirmed_media_type(
        self,
        reply_mock,
        progress_mock,
        send_mock,
    ):
        progress_mock.return_value = []
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_user=SimpleNamespace(id=1),
            callback_query=None,
            message=SimpleNamespace(),
        )
        context = SimpleNamespace(bot=SimpleNamespace())

        await search_handler._send_search_results(
            update,
            context,
            "Someday or One Day 2019",
            metadata={"media_type": "series"},
        )

        progress_mock.assert_awaited_once_with(
            update,
            context,
            "Someday or One Day 2019",
            media_type="series",
            status_message=reply_mock.return_value,
        )
        send_mock.assert_awaited_once()

    @patch.object(search_handler, "_backfill_candidate_covers", new_callable=AsyncMock)
    @patch.object(search_handler, "get_tvdb_series_episodes")
    @patch.object(search_handler, "_resolve_entries_with_primary_sources", new_callable=AsyncMock)
    async def test_movie_tvdb_id_is_not_used_for_series_episode_lookup(
        self,
        resolution_mock,
        episodes_mock,
        covers_mock,
    ):
        entry = {
            "source": "tvdb",
            "media_type": "movie",
            "scope": "movie",
            "title": "Chinese Movie",
            "english_title": "Chinese Movie",
            "year": "2024",
            "external_ids": {"tvdb": "123"},
            "tvdb_movie_id": "123",
        }
        intent = {
            "raw_query": "Chinese Movie",
            "title": "Chinese Movie",
            "scope": "movie_or_series",
            "year": "2024",
            "media_type": "movie",
        }
        resolution_mock.return_value = ([entry], {}, intent)
        covers_mock.side_effect = lambda candidates: candidates

        result = await search_handler._resolve_entry_candidates("Chinese Movie")

        self.assertEqual(result["status"], "needs_confirmation")
        episodes_mock.assert_not_called()


class PlanEvidenceProviderTest(unittest.TestCase):
    @patch.object(search_handler, "search_tvdb_series", return_value=[])
    @patch.object(search_handler, "search_tvdb_movies", return_value=[])
    def test_tvdb_empty_hypothesis_wrapper_is_not_ok(
        self, _movies_mock, _series_mock
    ):
        result = search_handler._tvdb_plan_provider({
            "hypotheses": [{"title": "Missing", "year": "2026"}]
        })

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["facts"], [])

    @patch.object(search_handler, "search_tvdb_series", return_value=[])
    @patch.object(search_handler, "search_tvdb_movies", return_value=[{}])
    def test_tvdb_empty_result_object_is_not_real_support(
        self, _movies_mock, _series_mock
    ):
        result = search_handler._tvdb_plan_provider({
            "hypotheses": [{"title": "Missing", "year": "2026"}]
        })

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["facts"], [])

    @patch.object(search_handler, "search_tvdb_series", return_value=[])
    @patch.object(
        search_handler,
        "search_tvdb_movies",
        return_value=[{"tvdb_movie_id": "movie-1", "name": "Found"}],
    )
    def test_tvdb_real_movie_result_is_ok(self, _movies_mock, _series_mock):
        result = search_handler._tvdb_plan_provider({
            "hypotheses": [{"title": "Found", "year": "2026"}]
        })

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["facts"][0]["movies"][0]["tvdb_movie_id"], "movie-1")


class TemporarySpecialStorageScopeTest(unittest.TestCase):
    def _draft(self, plan_id="plan-a"):
        return {
            "plan_id": plan_id,
            "media_metadata": {
                "schema_version": 1,
                "metadata_id": "",
                "confirmed": False,
                "identity": {
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day The Movie",
                    "year": "2022",
                    "content_kind": "extension_movie",
                    "summary": "电影版延续电视剧故事。",
                    "original_release_date": "2022-12-24",
                    "poster_url": "https://image.example/poster.jpg",
                    "poster_source": "douban",
                    "external_ids": {},
                },
                "relation": {
                    "type": "sequel",
                    "target_series": {
                        "chinese_title": "想见你",
                        "english_title": "Someday or One Day",
                        "year": "2019",
                        "external_ids": {},
                    },
                    "source": "wikipedia",
                },
                "placement": {
                    "library_type": "series",
                    "category_kind": "live_action_series",
                    "season_number": 0,
                    "episode_number": None,
                    "mapping_kind": "temporary_related_special",
                    "mapping_source": "local_allocator",
                    "tvdb_episode_id": "",
                },
                "source_entry": {
                    "title": "想见你 (电影)",
                    "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    "provider": "wikipedia",
                    "verification": "verified",
                },
                "items": [],
                "evidence": {
                    "provider_statuses": {
                        "wikipedia": "ok",
                        "douban": "ok",
                        "tvdb": "not_found",
                    },
                    "provider_support": {
                        "wikipedia": {
                            "has_facts": True,
                            "source_urls": [
                                "https://zh.wikipedia.org/wiki/想見你_(電影)"
                            ],
                        },
                        "douban": {"has_facts": True, "source_urls": []},
                        "tvdb": {"has_facts": False, "source_urls": []},
                    },
                },
                "warnings": [],
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
        }

    def test_restart_visible_temporary_numbers_are_scoped_by_target_series(self):
        first_season_path = (
            "/真人剧集/想见你 (Someday or One Day)/"
            "Someday or One Day Season 00"
        )
        storage = Mock()
        storage.get_file_info.side_effect = lambda path: (
            {"file_id": "category-root"}
            if path == "/真人剧集"
            else {"file_id": "season-a"}
            if path == first_season_path
            else None
        )
        storage.get_files_from_dir.return_value = ["Existing S00E100.mkv"]
        config = {
            "category_folder": [{
                "kind": "live_action_series",
                "name": "显示名可变",
                "path": "/真人剧集",
                "plex_library_id": "13",
            }]
        }
        allocator = TemporarySpecialAllocator()

        with (
            patch.object(search_handler.init, "bot_config", config),
            patch.object(search_handler.init, "openapi_115", storage, create=True),
        ):
            first = self._draft("plan-a")
            first_occupied = search_handler._occupied_special_numbers(
                first["media_metadata"]
            )
            first_confirmed = confirm_media_metadata(
                finalize_search_plan(first, allocator, first_occupied)
            )

            second = self._draft("plan-b")
            second["media_metadata"]["relation"]["target_series"] = {
                "chinese_title": "另一部剧",
                "english_title": "Another Show",
                "year": "2020",
                "external_ids": {},
            }
            second_occupied = search_handler._occupied_special_numbers(
                second["media_metadata"]
            )
            second_confirmed = confirm_media_metadata(
                finalize_search_plan(second, allocator, second_occupied)
            )

        self.assertEqual(first_occupied, {100})
        self.assertEqual(
            first_confirmed["placement"]["episode_number"],
            101,
        )
        self.assertEqual(second_occupied, set())
        self.assertEqual(
            second_confirmed["placement"]["episode_number"],
            100,
        )
        storage.get_files_from_dir.assert_called_once_with(first_season_path)


if __name__ == "__main__":
    unittest.main()
