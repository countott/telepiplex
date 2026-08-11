import unittest
from unittest.mock import patch

from telepiplex_search.confirmed_enrichment import (
    ConfirmedIdentity,
    build_anilist_query,
    build_tmdb_query,
    build_tvdb_query,
    build_wikipedia_queries,
    is_confirmed_japanese_animation,
    select_unique_anilist_fact,
    select_unique_tmdb_fact,
    select_unique_tvdb_series,
    select_unique_wikipedia_fact,
)
from telepiplex_search.discovery_flow import build_douban_first_search_plan
from telepiplex_search.service import SearchFeature


def identity(**overrides):
    values = {
        "provider": "douban",
        "stable_id": "36490422",
        "chinese_title": "繁花",
        "english_title": "Blossoms Shanghai",
        "original_title": "",
        "year": "2023",
        "media_type": "series",
        "requested_scope": "work",
        "original_language": "zh",
        "genres": ("Drama",),
        "external_ids": {"douban_subject": "36490422"},
    }
    values.update(overrides)
    return ConfirmedIdentity(**values)


class ConfirmedEnrichmentTest(unittest.TestCase):
    def test_tmdb_query_uses_confirmed_identity_without_double_source_gate(self):
        self.assertEqual(build_tmdb_query(identity()), {
            "title": "Blossoms Shanghai",
            "year": "2023",
            "media_type": "series",
        })

    def test_tmdb_requires_one_exact_identity(self):
        result = {
            "source": "tmdb",
            "status": "ok",
            "facts": [{
                "tmdb_id": "209867",
                "title": "Blossoms Shanghai",
                "year": "2023",
                "media_type": "series",
                "external_ids": {"tmdb": "209867", "tvdb": "433045"},
            }],
        }

        self.assertEqual(
            select_unique_tmdb_fact(result, identity())["tmdb_id"],
            "209867",
        )
        result["facts"].append({
            **result["facts"][0],
            "tmdb_id": "209868",
            "external_ids": {"tmdb": "209868"},
        })
        self.assertIsNone(select_unique_tmdb_fact(result, identity()))

    def test_anilist_is_only_queried_for_confirmed_japanese_animation(self):
        anime = identity(
            english_title="Honey and Clover",
            original_title="ハチミツとクローバー",
            year="2005",
            original_language="ja",
            genres=("Animation", "Drama"),
        )

        self.assertTrue(is_confirmed_japanese_animation(anime))
        self.assertEqual(build_anilist_query(anime), {
            "title": "Honey and Clover",
            "year": "2005",
        })
        self.assertIsNone(build_anilist_query(identity()))

    def test_anilist_requires_one_exact_identity(self):
        anime = identity(
            english_title="Honey and Clover",
            original_title="ハチミツとクローバー",
            year="2005",
            original_language="ja",
            genres=("Anime",),
        )
        result = {
            "source": "anilist",
            "status": "ok",
            "facts": [{
                "anilist_id": "1142",
                "title": "Hachimitsu to Clover",
                "official_english_title": "Honey and Clover",
                "original_title": "ハチミツとクローバー",
                "year": "2005",
                "media_type": "series",
                "external_ids": {"anilist": "1142"},
            }],
        }

        self.assertEqual(
            select_unique_anilist_fact(result, anime)["anilist_id"],
            "1142",
        )

    def test_wikipedia_queries_use_only_confirmed_identity(self):
        queries = build_wikipedia_queries(identity())

        self.assertEqual(
            queries,
            {
                "wikipedia_zh": ["繁花 2023 电视剧"],
                "wikipedia_en": ["Blossoms Shanghai 2023 TV series"],
            },
        )

    def test_wikipedia_requires_one_same_work(self):
        result = {
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "wikibase_item": "Q1",
                "title": "繁花",
                "chinese_title": "繁花",
                "official_english_title": "Blossoms Shanghai",
                "year": "2023",
                "media_type": "series",
            }],
        }

        self.assertEqual(
            select_unique_wikipedia_fact(result, identity())[
                "wikibase_item"
            ],
            "Q1",
        )
        result["facts"].append({
            **result["facts"][0],
            "wikibase_item": "Q2",
        })
        self.assertIsNone(
            select_unique_wikipedia_fact(result, identity())
        )

    def test_wikipedia_entity_without_verified_media_type_is_not_same_work(self):
        result = {
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "wikibase_item": "Q-person",
                "title": "繁花",
                "chinese_title": "繁花",
                "year": "2023",
                "media_type": "",
            }],
        }

        self.assertIsNone(
            select_unique_wikipedia_fact(result, identity())
        )

    def test_tvdb_prefers_verified_wikipedia_english_title(self):
        query = build_tvdb_query(
            identity(english_title="Douban English"),
            {
                "official_english_title": "Blossoms Shanghai",
                "year": "2023",
            },
        )

        self.assertEqual(query, {
            "title": "Blossoms Shanghai",
            "year": "2023",
            "media_type": "series",
        })

    def test_tvdb_is_skipped_without_reliable_latin_identity(self):
        self.assertIsNone(build_tvdb_query(
            identity(english_title="", original_title="繁花"),
            None,
        ))

    def test_tvdb_requires_one_same_series(self):
        result = {
            "source": "tvdb",
            "status": "ok",
            "facts": [{
                "movies": [],
                "series": [{
                    "tvdb_series_id": "100",
                    "name": "Blossoms Shanghai",
                    "year": "2023",
                }],
                "episodes_by_series": {},
            }],
        }

        selected = select_unique_tvdb_series(result, identity())
        self.assertEqual(selected["tvdb_series_id"], "100")
        result["facts"][0]["series"].append({
            "tvdb_series_id": "101",
            "name": "Blossoms Shanghai",
            "year": "2023",
        })
        self.assertIsNone(select_unique_tvdb_series(result, identity()))


class ConfirmedEnrichmentIntegrationTest(unittest.IsolatedAsyncioTestCase):
    @patch(
        "telepiplex_search.service.search_tmdb",
        side_effect=AssertionError("title search must not run"),
    )
    @patch("telepiplex_search.service.get_tmdb_entity")
    @patch("telepiplex_search.service.find_tmdb_by_external_id")
    async def test_tmdb_external_id_binding_precedes_title_search(
        self,
        find_mock,
        get_mock,
        search_mock,
    ):
        tmdb_fact = {
            "tmdb_id": "71912",
            "title": "The Witcher",
            "official_english_title": "The Witcher",
            "year": "2019",
            "media_type": "series",
            "external_ids": {"tmdb": "71912", "tvdb": "362696"},
        }
        find_mock.return_value = [tmdb_fact]
        get_mock.return_value = tmdb_fact

        fact, status = await SearchFeature._resolve_confirmed_tmdb(
            identity(
                english_title="The Witcher",
                chinese_title="猎魔人",
                year="2019",
                external_ids={"tvdb": "362696"},
            )
        )

        self.assertEqual(status, "ok")
        self.assertEqual(fact["tmdb_id"], "71912")
        find_mock.assert_called_once_with("tvdb", "362696", "series")
        search_mock.assert_not_called()

    async def _candidate(self):
        plan = await build_douban_first_search_plan(
            "繁花",
            "enrichment-1",
            lambda _payload: {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": "36490422",
                    "title": "繁花",
                    "chinese_title": "繁花",
                    "english_title": "Blossoms Shanghai",
                    "year": "2023",
                    "media_type": "series",
                    "url": (
                        "https://movie.douban.com/subject/36490422/"
                    ),
                }],
            },
            ai_decider=lambda _payload: None,
        )
        return plan["candidates"][0]

    @patch("telepiplex_search.service.search_tvdb_series", return_value=[])
    @patch("telepiplex_search.service.get_tmdb_entity")
    @patch("telepiplex_search.service.search_tmdb")
    async def test_tmdb_peer_adds_stable_cross_ids(
        self,
        search_tmdb_mock,
        get_tmdb_mock,
        _search_tvdb_mock,
    ):
        tmdb_fact = {
            "tmdb_id": "209867",
            "title": "Blossoms Shanghai",
            "official_english_title": "Blossoms Shanghai",
            "year": "2023",
            "media_type": "series",
            "url": "https://www.themoviedb.org/tv/209867",
            "external_ids": {
                "tmdb": "209867",
                "tvdb": "433045",
                "imdb": "tt13885302",
            },
        }
        search_tmdb_mock.return_value = [tmdb_fact]
        get_tmdb_mock.return_value = tmdb_fact
        feature = SearchFeature(config={}, host=None)
        feature._wikipedia_provider = lambda _payload: {
            "source": "wikipedia",
            "status": "not_found",
            "facts": [],
        }

        enriched = await feature._supplement_selected_candidate(
            await self._candidate(),
            "繁花",
        )

        link = next(
            item for item in enriched["source_links"]
            if item["provider"] == "tmdb"
        )
        self.assertEqual(link["fact_id"], "tmdb:209867")
        self.assertEqual(link["external_ids"]["tvdb"], "433045")

    @patch("telepiplex_search.service.search_tvdb_series", return_value=[])
    @patch("telepiplex_search.service.get_anilist_media")
    @patch("telepiplex_search.service.search_anilist")
    async def test_anilist_is_added_only_after_japanese_animation_confirmation(
        self,
        search_anilist_mock,
        get_anilist_mock,
        _search_tvdb_mock,
    ):
        anilist_fact = {
            "anilist_id": "1142",
            "title": "Hachimitsu to Clover",
            "official_english_title": "Honey and Clover",
            "romanized_original_title": "Hachimitsu to Clover",
            "original_title": "ハチミツとクローバー",
            "original_language": "ja",
            "year": "2005",
            "media_type": "series",
            "url": "https://anilist.co/anime/1142",
            "external_ids": {"anilist": "1142"},
        }
        search_anilist_mock.return_value = [anilist_fact]
        get_anilist_mock.return_value = anilist_fact
        candidate = await self._candidate()
        candidate["media_metadata"]["identity"].update({
            "chinese_title": "蜂蜜与四叶草",
            "english_title": "Honey and Clover",
            "official_english_title": "Honey and Clover",
            "original_title": "ハチミツとクローバー",
            "original_language": "ja",
            "genres": ["Anime"],
            "year": "2005",
        })
        feature = SearchFeature(config={}, host=None)
        feature._wikipedia_provider = lambda _payload: {
            "source": "wikipedia",
            "status": "not_found",
            "facts": [],
        }

        enriched = await feature._supplement_selected_candidate(
            candidate,
            "蜂蜜与四叶草",
        )

        self.assertIn(
            "anilist",
            {item["provider"] for item in enriched["source_links"]},
        )

    @patch(
        "telepiplex_search.service.search_tvdb_series",
        return_value=[],
    )
    async def test_tvdb_failure_rewrites_requested_scope_to_whole_series(
        self,
        _search,
    ):
        feature = SearchFeature(config={}, host=None)
        feature._wikipedia_provider = lambda _payload: {
            "source": "wikipedia",
            "status": "not_found",
            "facts": [],
        }
        candidate = await self._candidate()
        candidate["intended_scope"] = "season"
        candidate["requested_season_number"] = 1

        enriched = await feature._supplement_selected_candidate(
            candidate,
            "繁花 第一季",
        )

        self.assertEqual(enriched["intended_scope"], "whole_series")
        self.assertIsNone(enriched["requested_season_number"])
        self.assertIn("tvdb:not_found", enriched["unresolved_sources"])
        self.assertNotIn(
            "tvdb",
            {
                item["provider"]
                for item in enriched["source_links"]
            },
        )

    @patch("telepiplex_search.service.get_tvdb_series")
    @patch("telepiplex_search.service.search_tvdb_series")
    async def test_unique_wikipedia_then_tvdb_adds_verified_links(
        self,
        search,
        get,
    ):
        search.return_value = [{
            "tvdb_series_id": "100",
            "name": "Blossoms Shanghai",
            "english_title": "Blossoms Shanghai",
            "year": "2023",
        }]
        get.return_value = {
            "tvdb_series_id": "100",
            "name": "Blossoms Shanghai",
            "english_title": "Blossoms Shanghai",
            "year": "2023",
            "episodes": [{
                "tvdb_episode_id": "e1",
                "season_number": 1,
                "episode_number": 1,
            }],
        }
        feature = SearchFeature(config={}, host=None)
        feature._wikipedia_provider = lambda _payload: {
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "wikibase_item": "Q1",
                "title": "繁花",
                "chinese_title": "繁花",
                "official_english_title": "Blossoms Shanghai",
                "year": "2023",
                "media_type": "series",
                "url": "https://zh.wikipedia.org/wiki/繁花",
            }],
        }

        enriched = await feature._supplement_selected_candidate(
            await self._candidate(),
            "繁花",
        )

        self.assertEqual(
            {
                item["provider"]
                for item in enriched["source_links"]
            },
            {"douban", "wikipedia", "tvdb"},
        )
        self.assertNotIn("tvdb:not_found", enriched["unresolved_sources"])

    @patch("telepiplex_search.service.get_tvdb_series")
    @patch("telepiplex_search.service.search_tvdb_series")
    async def test_wikipedia_english_title_can_validate_tvdb_match(
        self,
        search,
        get,
    ):
        search.return_value = [{
            "tvdb_series_id": "100",
            "name": "Blossoms Shanghai",
            "year": "2023",
        }]
        get.return_value = {
            "tvdb_series_id": "100",
            "name": "Blossoms Shanghai",
            "year": "2023",
            "episodes": [{
                "tvdb_episode_id": "e1",
                "season_number": 1,
                "episode_number": 1,
            }],
        }
        feature = SearchFeature(config={}, host=None)
        feature._wikipedia_provider = lambda _payload: {
            "source": "wikipedia",
            "status": "ok",
            "facts": [{
                "wikibase_item": "Q1",
                "title": "繁花",
                "chinese_title": "繁花",
                "official_english_title": "Blossoms Shanghai",
                "year": "2023",
                "media_type": "series",
                "url": "https://zh.wikipedia.org/wiki/繁花",
            }],
        }
        candidate = await self._candidate()
        candidate["media_metadata"]["identity"].update({
            "english_title": "",
            "official_english_title": "",
            "original_title": "",
        })

        enriched = await feature._supplement_selected_candidate(
            candidate,
            "繁花",
        )

        self.assertIn(
            "tvdb",
            {
                item["provider"]
                for item in enriched["source_links"]
            },
        )


if __name__ == "__main__":
    unittest.main()
