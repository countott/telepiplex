from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from telepiplex_plugin_sdk.diagnostics import bounded_diagnostic_value
from telepiplex_search.candidate_locale import (
    CandidateLocaleError,
    localize_candidate_from_verified_douban,
)
from telepiplex_search.confirmed_enrichment import (
    ConfirmedIdentity,
    build_tvdb_query,
)
from telepiplex_search.service import SearchFeature
from tests.test_feature_service import series_ranked_search_plan


def _latin_candidate(index: int) -> dict:
    return {
        "candidate_id": f"wikipedia:Q{index}",
        "media_metadata": {"identity": {
            "chinese_title": "",
            "english_title": f"Honey and Clover {index}",
            "official_english_title": f"Honey and Clover {index}",
            "original_title": "ハチミツとクローバー",
            "original_language": "ja",
            "year": "2005",
            "content_kind": "series",
            "genres": ["Animation"],
            "aliases": [],
            "external_ids": {"wikidata": f"Q{index}"},
        }},
        "source_links": [],
    }


class CandidateLocalizationPressureTest(unittest.IsolatedAsyncioTestCase):
    async def test_veep_sixty_five_episode_result_is_compact_and_diagnostic_safe(self):
        async def planner(_raw_query, plan_id):
            plan = deepcopy(series_ranked_search_plan())
            plan["plan_id"] = plan_id
            plan["candidates"] = plan["candidates"][:1]
            contract = plan["candidates"][0]["media_metadata"]
            contract["metadata_id"] = plan_id
            contract["identity"].update({
                "chinese_title": "副总统",
                "english_title": "Veep",
                "year": "2012",
            })
            contract["items"] = [{
                "item_id": f"veep-s07e{episode:02d}",
                "content_role": "main_episode",
                "season_number": 7,
                "episode_number": episode,
                "aired": "2019-05-12",
            } for episode in range(1, 66)]
            contract["evidence"]["series_inventory"] = {
                "season_totals": {7: 65},
            }
            return plan

        feature = SearchFeature(
            config={},
            host=None,
            plan_builder=planner,
        )
        result = await feature.metadata_capability({
            "method": "resolve_metadata",
            "payload": {
                "query": "Veep S07",
                "probe": {
                    "content_shape": "season_pack",
                    "observed_seasons": [7],
                    "observed_episodes": [],
                    "video_count": 65,
                },
            },
        })

        encoded = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(
            result["media_metadata"]["identity"]["chinese_title"],
            "副总统",
        )
        self.assertEqual(len(result["media_metadata"]["items"]), 65)
        self.assertNotIn("source_queries", result)
        self.assertNotIn("evidence", result)
        self.assertLess(len(encoded), 32 * 1024)
        if len(encoded) > 8 * 1024:
            self.assertIn(
                "_diagnostic_summary",
                bounded_diagnostic_value(result),
            )

    def test_tvdb_root_year_pressure_1_000_scope_mismatches(self):
        for index in range(1_000):
            root_year = str(1980 + index % 40)
            identity = ConfirmedIdentity(
                provider="wikipedia",
                stable_id=f"Q{index}",
                chinese_title="",
                english_title=f"Series {index}",
                original_title=f"Series {index}",
                year=str(2000 + index % 20),
                media_type="series",
                requested_scope="season",
                original_language="en",
                genres=("Drama",),
                external_ids=(
                    {"tvdb": str(300_000 + index)}
                    if index % 2 == 0
                    else {}
                ),
                root_year=root_year,
                scope_year=str(2020 + index % 6),
            )

            query = build_tvdb_query(
                identity,
                {
                    "official_english_title": f"Series {index}",
                    "year": "2011",
                },
            )

            self.assertEqual(query["year"], root_year)
            if index % 2 == 0:
                self.assertEqual(query["tvdb_id"], str(300_000 + index))
            else:
                self.assertNotIn("tvdb_id", query)

    @patch("telepiplex_search.service.search_tvdb_series")
    @patch("telepiplex_search.service.get_tvdb_series")
    async def test_stable_tvdb_id_bypasses_title_year_search(
        self,
        get_tvdb_series_mock,
        search_tvdb_series_mock,
    ):
        candidate = _latin_candidate(371572)
        identity = candidate["media_metadata"]["identity"]
        identity.update({
            "english_title": "House of the Dragon",
            "official_english_title": "House of the Dragon",
            "original_title": "House of the Dragon",
            "original_language": "en",
            "year": "2022",
            "root_year": "2022",
            "scope_year": "2024",
            "external_ids": {
                "wikidata": "Q103768595",
                "tvdb": "371572",
            },
        })
        candidate["anchor_fact_id"] = "wikipedia:Q103768595"
        candidate["intended_scope"] = "season"
        candidate["requested_season_number"] = 2
        candidate["source_links"] = [{
            "provider": provider,
            "fact_id": (
                "wikipedia:Q103768595"
                if provider == "wikipedia"
                else f"{provider}:371572"
            ),
            "url": f"https://example.test/{provider}/371572",
            "external_ids": {provider: "371572"},
            "role": "series_root",
            "season_number": None,
            "episode_number": None,
            "verification": "fact_verified",
        } for provider in ("wikipedia", "tmdb", "douban", "anilist")]
        get_tvdb_series_mock.return_value = {
            "tvdb_series_id": "371572",
            "name": "House of the Dragon",
            "year": "2022",
            "episodes": [{
                "tvdb_episode_id": "episode-1",
                "season_number": 2,
                "episode_number": 1,
            }],
        }
        feature = SearchFeature(config={}, host=None)

        supplemented = await feature._supplement_selected_candidate(
            candidate,
            "龙之家族 第二季",
        )

        search_tvdb_series_mock.assert_not_called()
        get_tvdb_series_mock.assert_called_once_with("371572")
        self.assertIn(
            "tvdb",
            {item["provider"] for item in supplemented["source_links"]},
        )

    async def test_selected_candidate_applies_verified_chinese_before_rebuild(self):
        candidate = _latin_candidate(42)
        candidate["anchor_fact_id"] = "wikipedia:Q42"
        candidate["intended_scope"] = "whole_series"
        candidate["source_links"] = [{
            "provider": provider,
            "fact_id": (
                "wikipedia:Q42" if provider == "wikipedia" else f"{provider}:42"
            ),
            "url": f"https://example.test/{provider}/42",
            "external_ids": {provider: "42"},
            "role": "series_root",
            "season_number": None,
            "episode_number": None,
            "verification": "fact_verified",
        } for provider in ("wikipedia", "tmdb", "tvdb", "anilist")]
        feature = SearchFeature(config={}, host=None)
        feature._douban_provider = lambda _payload: {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "1770589",
                "chinese_title": "蜂蜜与四叶草",
                "english_title": "Honey and Clover 42",
                "year": "2005",
                "media_type": "series",
                "original_language": "ja",
                "external_ids": {"douban_subject": "1770589"},
            }],
        }

        supplemented = await feature._supplement_selected_candidate(
            candidate,
            "蜂蜜与四叶草",
        )

        self.assertEqual(
            supplemented["media_metadata"]["identity"]["chinese_title"],
            "蜂蜜与四叶草",
        )
        self.assertEqual(supplemented["douban_match_mode"], "strong_fields")

    async def test_preview_strong_field_lookup_is_bounded_to_five_candidates(self):
        calls = []

        def douban_provider(payload):
            calls.append(payload)
            query = payload["source_queries"]["douban"][0]
            index = int(query.split("Honey and Clover ", 1)[1].split()[0])
            return {
                "source": "douban",
                "status": "ok",
                "facts": [{
                    "subject_id": str(1770547 + index),
                    "chinese_title": f"蜂蜜与四叶草{index}",
                    "english_title": f"Honey and Clover {index}",
                    "year": "2005",
                    "media_type": "series",
                    "original_language": "ja",
                    "genres": ["Animation"],
                    "external_ids": {
                        "douban_subject": str(1770547 + index),
                    },
                }],
            }

        feature = SearchFeature(config={}, host=None)
        feature._douban_provider = douban_provider
        plan = {
            "candidates": [_latin_candidate(index) for index in range(7)],
        }

        localized = await feature._localize_exact_douban_candidates(
            plan,
            plan_id="bounded-locale",
        )

        self.assertEqual(len(calls), 5)
        self.assertEqual(
            [
                item["media_metadata"]["identity"]["chinese_title"]
                for item in localized["candidates"][:5]
            ],
            [f"蜂蜜与四叶草{index}" for index in range(5)],
        )
        self.assertEqual(
            [
                item["media_metadata"]["identity"]["chinese_title"]
                for item in localized["candidates"][5:]
            ],
            ["", ""],
        )

    def test_locale_fact_matrix_pressure_1_000_cases(self):
        for index in range(1_000):
            candidate = _latin_candidate(index)
            kind = index % 4
            fact = {
                "subject_id": str(2_000_000 + index),
                "chinese_title": (
                    f"蜂蜜与四叶草{index}"
                    if kind in {0, 1}
                    else "Honey and Clover"
                ),
                "english_title": f"Honey and Clover {index}",
                "media_type": "series",
                "external_ids": {
                    "douban_subject": str(2_000_000 + index),
                },
            }
            if kind in {0, 1}:
                localized = localize_candidate_from_verified_douban(
                    candidate,
                    fact,
                    match_mode=(
                        "strong_fields" if kind == 0 else "imdb_exact"
                    ),
                )
                self.assertIn(
                    "蜂蜜与四叶草",
                    localized["media_metadata"]["identity"]["chinese_title"],
                )
            else:
                with self.assertRaises(CandidateLocaleError):
                    localize_candidate_from_verified_douban(
                        candidate,
                        fact,
                        match_mode="strong_fields",
                    )
                self.assertEqual(
                    candidate["media_metadata"]["identity"]["chinese_title"],
                    "",
                )


if __name__ == "__main__":
    unittest.main()
