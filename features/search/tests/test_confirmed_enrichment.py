import unittest
from unittest.mock import patch

from telepiplex_search.confirmed_enrichment import (
    ConfirmedIdentity,
    build_tvdb_query,
    build_wikipedia_queries,
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
    }
    values.update(overrides)
    return ConfirmedIdentity(**values)


class ConfirmedEnrichmentTest(unittest.TestCase):
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
