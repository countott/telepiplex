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
    select_unique_douban_fact,
    select_unique_tmdb_fact,
    select_unique_tvdb_series,
    select_unique_wikipedia_fact,
)
from telepiplex_search.direct_link import DirectEntity
from telepiplex_search.direct_plan import build_direct_entity_plan
from telepiplex_search.media_metadata_v2 import (
    project_confirmed_media_metadata_v2,
)
from telepiplex_search.service import SearchFeature
from telepiplex_search.work_discovery import build_root_work_search_plan


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

    def test_douban_chinese_fallback_requires_one_exact_identity(self):
        english_only = identity(
            provider="wikipedia",
            stable_id="Q74801",
            chinese_title="Veep",
            english_title="Veep",
            year="2012",
            external_ids={"wikidata": "Q74801", "imdb": "tt1759761"},
        )
        result = {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "1",
                "title": "副总统",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "year": "2012",
                "media_type": "series",
                "url": "https://movie.douban.com/subject/1/",
                "external_ids": {
                    "douban_subject": "1",
                    "imdb": "tt1759761",
                },
            }],
        }

        selected = select_unique_douban_fact(result, english_only)
        self.assertEqual(selected["subject_id"], "1")
        self.assertEqual(selected["douban_match_mode"], "imdb_exact")
        result["facts"].append({
            **result["facts"][0],
            "subject_id": "2",
            "url": "https://movie.douban.com/subject/2/",
        })
        self.assertIsNone(select_unique_douban_fact(result, english_only))

    def test_douban_same_imdb_season_entries_collapse_to_one_root_title(self):
        confirmed = identity(
            provider="wikipedia",
            stable_id="Q74801",
            chinese_title="副人之仁",
            english_title="Veep",
            year="2012",
            external_ids={"wikidata": "Q74801", "imdb": "tt1759761"},
        )
        result = {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": str(subject_id),
                "douban_title_raw": f"副总统 第{season}季",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "year": str(year),
                "media_type": "series",
                "season_number": season,
                "external_ids": {
                    "douban_subject": str(subject_id),
                    "imdb": "tt1759761",
                },
            } for subject_id, season, year in (
                (1, 1, 2012),
                (2, 2, 2013),
            )],
        }

        selected = select_unique_douban_fact(result, confirmed)

        self.assertEqual(selected["subject_id"], "1")
        self.assertEqual(selected["chinese_title"], "副总统")
        self.assertEqual(selected["douban_match_mode"], "imdb_exact")

    def test_douban_title_and_year_without_second_strong_field_is_rejected(self):
        english_only = identity(
            provider="wikipedia",
            stable_id="Q74801",
            chinese_title="副人之仁",
            english_title="Veep",
            year="2012",
            external_ids={"wikidata": "Q74801"},
        )
        result = {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "1",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "year": "2012",
                "media_type": "series",
                "external_ids": {"douban_subject": "1"},
            }],
        }

        self.assertIsNone(select_unique_douban_fact(result, english_only))

    def test_douban_uses_strong_fields_when_imdb_is_unavailable(self):
        confirmed = identity(
            provider="wikipedia",
            stable_id="Q74801",
            chinese_title="副人之仁",
            english_title="Veep",
            year="2012",
            original_language="en",
            external_ids={"wikidata": "Q74801"},
        )
        result = {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "5379824",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "year": "2012",
                "media_type": "series",
                "original_language": "en",
                "external_ids": {"douban_subject": "5379824"},
            }],
        }

        selected = select_unique_douban_fact(result, confirmed)

        self.assertEqual(selected["subject_id"], "5379824")
        self.assertEqual(selected["douban_match_mode"], "strong_fields")

    def test_wikidata_exact_douban_subject_needs_no_imdb_api(self):
        confirmed = identity(
            provider="wikipedia",
            stable_id="Q124175370",
            chinese_title="百年孤寂",
            english_title="One Hundred Years of Solitude",
            year="2024",
            original_language="es",
            external_ids={
                "wikidata": "Q124175370",
                "douban_subject": "30482958",
            },
        )
        result = {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "30482958",
                "chinese_title": "百年孤独",
                "english_title": "One Hundred Years of Solitude",
                "year": "2024",
                "media_type": "series",
                "external_ids": {"douban_subject": "30482958"},
            }],
        }

        selected = select_unique_douban_fact(result, confirmed)

        self.assertEqual(selected["subject_id"], "30482958")
        self.assertEqual(selected["douban_match_mode"], "wikidata_exact")

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

    def test_tvdb_query_uses_series_root_year_not_supplement_scope_year(self):
        query = build_tvdb_query(
            identity(
                english_title="House of the Dragon",
                year="2024",
                root_year="2022",
                scope_year="2024",
            ),
            {
                "official_english_title": "House of the Dragon",
                "year": "2011",
            },
        )

        self.assertEqual(query, {
            "title": "House of the Dragon",
            "year": "2022",
            "media_type": "series",
        })

    def test_tvdb_query_carries_stable_series_id(self):
        query = build_tvdb_query(
            identity(
                english_title="House of the Dragon",
                year="2022",
                root_year="2022",
                external_ids={"tvdb": "371572"},
            ),
            None,
        )

        self.assertEqual(query["tvdb_id"], "371572")
        self.assertEqual(query["year"], "2022")

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
    def test_direct_anilist_link_projects_same_entity_binding(self):
        plan = build_direct_entity_plan(
            DirectEntity(
                provider="anilist",
                evidence={
                    "source": "anilist",
                    "status": "ok",
                    "facts": [{
                        "anilist_id": "1142",
                        "title": "Hachimitsu to Clover II",
                        "original_title": "ハチミツとクローバーII",
                        "romanized_original_title": "Hachimitsu to Clover II",
                        "official_english_title": "Honey and Clover II",
                        "original_language": "ja",
                        "year": "2006",
                        "media_type": "series",
                        "genres": ["Anime"],
                        "url": "https://anilist.co/anime/1142",
                        "external_ids": {"anilist": "1142"},
                    }],
                },
                stable_identity=("anilist", "1142"),
                title="Hachimitsu to Clover II",
                year="2006",
                media_type="series",
                scope="work",
            ),
            raw_query="蜂蜜与四叶草II",
            plan_id="direct-anilist-entry",
        )
        candidate = plan["candidates"][0]

        self.assertEqual(candidate["source_links"][0]["role"], "anime_entry")
        identity_value = candidate["media_metadata"]["identity"]
        self.assertEqual(identity_value["work_root_ref"], {
            "provider": "anilist",
            "id": "1142",
        })
        self.assertEqual(identity_value["anime_entry_ref"], {
            "provider": "anilist",
            "id": "1142",
        })
        self.assertEqual(identity_value["binding_kind"], "same_entity")
        projected = project_confirmed_media_metadata_v2(
            candidate,
            requested_scope={
                "kind": "whole_series",
                "season_number": None,
                "episode_number": None,
            },
        )
        self.assertEqual(projected["identity"]["primary_ref"], {
            "provider": "anilist",
            "id": "1142",
        })
        self.assertNotIn("relation_group", candidate["media_metadata"])

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
        plan = build_direct_entity_plan(
            DirectEntity(
                provider="douban",
                evidence={
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
                stable_identity=("douban_subject", "36490422"),
                title="繁花",
                year="2023",
                media_type="series",
                scope="work",
            ),
            raw_query="繁花",
            plan_id="enrichment-1",
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
            "release_format": "TV",
            "status": "FINISHED",
            "episode_count": 24,
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
            purpose="anime_entry",
        )

        link = next(
            item for item in enriched["source_links"]
            if item["provider"] == "anilist"
        )
        self.assertEqual(link["role"], "anime_entry")
        self.assertEqual(link["external_ids"], {"anilist": "1142"})
        self.assertNotIn("relation_group", enriched)

    @patch("telepiplex_search.service.search_anilist")
    @patch("telepiplex_search.service.get_anilist_media")
    async def test_anilist_exact_id_binding_bypasses_title_search(
        self,
        get_anilist_mock,
        search_anilist_mock,
    ):
        fact = {
            "anilist_id": "116674",
            "title": "BLEACH: Sennen Kessen-hen",
            "official_english_title": "BLEACH: Thousand-Year Blood War",
            "original_title": "BLEACH 千年血戦篇",
            "year": "2022",
            "media_type": "series",
            "external_ids": {"anilist": "116674", "myanimelist": "41467"},
        }
        get_anilist_mock.return_value = fact

        selected, status = await SearchFeature._resolve_confirmed_anilist(
            identity(
                provider="wikidata",
                stable_id="Q112631839",
                chinese_title="死神 千年血战篇",
                english_title="",
                original_title="",
                year="2022",
                original_language="ja",
                genres=("Anime",),
                external_ids={
                    "wikidata": "Q112631839",
                    "anilist": "116674",
                },
            ),
        )

        self.assertEqual(status, "ok")
        self.assertEqual(selected["binding_method"], "wikidata_anilist_id")
        self.assertEqual(selected["binding_kind"], "root_to_entry")
        get_anilist_mock.assert_called_once_with("116674")
        search_anilist_mock.assert_not_called()

    @patch("telepiplex_search.service.search_anilist")
    async def test_anilist_mal_binding_bypasses_title_search(
        self,
        search_anilist_mock,
    ):
        fact = {
            "anilist_id": "16498",
            "title": "Shingeki no Kyojin",
            "official_english_title": "Attack on Titan",
            "original_title": "進撃の巨人",
            "year": "2013",
            "media_type": "series",
            "external_ids": {"anilist": "16498", "myanimelist": "16498"},
        }
        with patch(
            "telepiplex_search.service.get_anilist_media_by_mal_id",
            create=True,
            return_value=fact,
        ) as get_by_mal:
            selected, status = await SearchFeature._resolve_confirmed_anilist(
                identity(
                    provider="wikidata",
                    stable_id="Q22126305",
                    chinese_title="进击的巨人",
                    english_title="Attack on Titan",
                    original_title="進撃の巨人",
                    year="2013",
                    original_language="ja",
                    genres=("Anime",),
                    external_ids={
                        "wikidata": "Q22126305",
                        "myanimelist": "16498",
                    },
                ),
            )

        self.assertEqual(status, "ok")
        self.assertEqual(selected["binding_method"], "wikidata_mal_id")
        self.assertEqual(selected["binding_kind"], "root_to_entry")
        get_by_mal.assert_called_once_with("16498")
        search_anilist_mock.assert_not_called()

    @patch("telepiplex_search.service.search_anilist")
    @patch("telepiplex_search.service.get_anilist_media_by_mal_id")
    @patch("telepiplex_search.service.get_anilist_media", return_value=None)
    async def test_stale_anilist_id_falls_back_to_exact_mal_id_only(
        self,
        get_anilist_mock,
        get_by_mal_mock,
        search_anilist_mock,
    ):
        get_by_mal_mock.return_value = {
            "anilist_id": "16498",
            "title": "Shingeki no Kyojin",
            "official_english_title": "Attack on Titan",
            "original_title": "進撃の巨人",
            "year": "2013",
            "media_type": "series",
            "external_ids": {
                "anilist": "16498",
                "myanimelist": "16498",
            },
        }

        selected, status = await SearchFeature._resolve_confirmed_anilist(
            identity(
                provider="wikidata",
                stable_id="Q22126305",
                chinese_title="进击的巨人",
                english_title="Attack on Titan",
                original_title="進撃の巨人",
                year="2013",
                original_language="ja",
                genres=("Anime",),
                external_ids={
                    "wikidata": "Q22126305",
                    "anilist": "999999",
                    "myanimelist": "16498",
                },
            ),
        )

        self.assertEqual(status, "ok")
        self.assertEqual(selected["binding_method"], "wikidata_mal_id")
        get_anilist_mock.assert_called_once_with("999999")
        get_by_mal_mock.assert_called_once_with("16498")
        search_anilist_mock.assert_not_called()

    @patch("telepiplex_search.service.search_anilist")
    @patch(
        "telepiplex_search.service.get_anilist_media_by_mal_id",
        return_value=None,
    )
    @patch("telepiplex_search.service.get_anilist_media", return_value=None)
    async def test_failed_explicit_ids_never_fall_back_to_title_search(
        self,
        _get_anilist_mock,
        _get_by_mal_mock,
        search_anilist_mock,
    ):
        selected, status = await SearchFeature._resolve_confirmed_anilist(
            identity(
                original_language="ja",
                genres=("Anime",),
                external_ids={
                    "wikidata": "Q1",
                    "anilist": "999998",
                    "myanimelist": "999999",
                },
            ),
        )

        self.assertIsNone(selected)
        self.assertEqual(status, "not_found")
        search_anilist_mock.assert_not_called()

    async def test_legacy_anilist_root_link_is_reclassified_as_entry(self):
        candidate = await self._candidate()
        candidate["media_metadata"]["identity"].update({
            "original_language": "ja",
            "genres": ["Anime"],
        })
        candidate["source_links"].append({
            "provider": "anilist",
            "fact_id": "anilist:1142",
            "url": "https://anilist.co/anime/1142",
            "external_ids": {"anilist": "1142"},
            "role": "series_root",
            "season_number": None,
            "episode_number": None,
            "verification": "fact_verified",
            "proposed_season_number": None,
            "proposed_episode_number": None,
        })
        feature = SearchFeature(config={}, host=None)

        enriched = await feature._supplement_selected_candidate(
            candidate,
            "蜂蜜与四叶草",
            purpose="anime_entry",
        )

        links = [
            link for link in enriched["source_links"]
            if link["provider"] == "anilist"
        ]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["role"], "anime_entry")

    @patch(
        "telepiplex_search.service.search_tvdb_series",
        return_value=[],
    )
    async def test_tvdb_failure_preserves_requested_season_scope(
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

        self.assertEqual(enriched["intended_scope"], "season")
        self.assertEqual(enriched["requested_season_number"], 1)
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

    @patch("telepiplex_search.service.search_tvdb_series", return_value=[])
    @patch("telepiplex_search.service.search_tmdb", return_value=[])
    async def test_wikipedia_regional_chinese_still_uses_verified_douban_title(
        self,
        _tmdb,
        _tvdb,
    ):
        plan = build_root_work_search_plan(
            "副人之仁",
            "english-wiki",
            lambda _payload: {
                "source": "wikipedia",
                "status": "ok",
                "facts": [{
                    "language": "en",
                    "search_rank": 1,
                    "page_id": 1,
                    "is_disambiguation": False,
                    "title": "副人之仁",
                    "chinese_title": "副人之仁",
                    "english_title": "Veep",
                    "official_english_title": "Veep",
                    "extract": "American television series",
                    "url": "https://en.wikipedia.org/wiki/Veep",
                    "wikibase_item": "Q74801",
                }],
            },
            lambda _qids: {
                "Q74801": {
                    "wikibase_item": "Q74801",
                    "external_ids": {
                        "wikidata": "Q74801",
                        "imdb": "tt1759761",
                    },
                    "chinese_title": "副人之仁",
                    "english_title": "Veep",
                    "aliases": [],
                    "media_type": "series",
                    "year": "2012",
                    "countries": [],
                    "season_count": 7,
                    "episode_count": 65,
                },
            },
        )
        feature = SearchFeature(config={}, host=None)
        feature._douban_provider = lambda payload: {
            "source": "douban",
            "status": "ok",
            "facts": [{
                "subject_id": "5379824",
                "title": "副总统",
                "chinese_title": "副总统",
                "english_title": "Veep",
                "year": "2012",
                "media_type": "series",
                "url": "https://movie.douban.com/subject/5379824/",
                "cover_url": "https://img.example/veep.jpg",
                "external_ids": {
                    "douban_subject": "5379824",
                    "imdb": "tt1759761",
                },
            }],
        }

        enriched = await feature._supplement_selected_candidate(
            plan["candidates"][0],
            "副人之仁",
        )

        douban_link = next(
            item for item in enriched["source_links"]
            if item["provider"] == "douban"
        )
        self.assertEqual(
            douban_link["external_ids"]["douban_subject"],
            "5379824",
        )
        self.assertEqual(enriched["douban_match_mode"], "imdb_exact")


if __name__ == "__main__":
    unittest.main()
