import unittest
from unittest.mock import patch

from telepiplex_search.adapters.tvdb import TvdbConfigError
from telepiplex_search.adapters.douban import DoubanSubjectLookupError
from telepiplex_search.adapters.wikipedia import WikipediaPageLookupError
from telepiplex_search.adapters.tmdb import TmdbConfigError
from telepiplex_search.adapters.anilist import AniListRequestError
from telepiplex_search.direct_link import (
    DirectLinkError,
    resolve_direct_link,
    resolve_shared_metadata_link,
)
from telepiplex_search.input_contract import MetadataLink, ParsedInput


class DirectLinkTest(unittest.TestCase):
    @patch("telepiplex_search.direct_link.lookup_wikipedia_episode_page")
    @patch("telepiplex_search.direct_link.enrich_wikidata_entities")
    @patch("telepiplex_search.direct_link.lookup_wikipedia_page")
    def test_wikipedia_root_follows_its_exact_episode_list_link(
        self,
        page,
        enrich,
        episodes,
    ):
        page.return_value = {
            "title": "Bleach (TV series)",
            "canonical_title": "Bleach (TV series)",
            "official_english_title": "Bleach",
            "url": "https://en.wikipedia.org/wiki/Bleach_(TV_series)",
            "wikibase_item": "Q5362638",
            "is_disambiguation": False,
        }
        enrich.return_value = {"Q5362638": {
            "wikibase_item": "Q5362638",
            "media_type": "series",
            "year": "2004",
            "episode_count": 366,
        }}
        episodes.side_effect = [{
            "status": "absent",
            "items": [],
            "season_totals": {},
            "source_language": "en",
            "source_url": "https://en.wikipedia.org/wiki/Bleach_(TV_series)",
            "revision_id": 1,
            "episode_list_links": [{
                "title": "List of Bleach episodes",
                "href": "/wiki/List_of_Bleach_episodes",
            }, {
                "title": "List of Bleach episodes (season 1)",
                "href": "/wiki/List_of_Bleach_episodes_(season_1)",
            }, {
                "title": "List of Bleach episodes (season 2)",
                "href": "/wiki/List_of_Bleach_episodes_(season_2)",
            }],
            "error": "wikipedia_table_absent",
        }, {
            "status": "complete",
            "items": [{
                "season_number": 1,
                "episode_number": 1,
                "air_date": "2004-10-05",
            }],
            "season_totals": {1: 1},
            "source_language": "en",
            "source_url": "https://en.wikipedia.org/wiki/List_of_Bleach_episodes",
            "revision_id": 2,
            "episode_list_links": [],
            "error": "",
        }]

        direct = resolve_direct_link(MetadataLink(
            provider="wikipedia",
            media_type="",
            entity_id="en:Bleach_(TV_series)",
            scope="work",
            url="https://en.wikipedia.org/wiki/Bleach_(TV_series)",
        ))

        self.assertEqual(
            [call.args[1] for call in episodes.call_args_list],
            ["Bleach (TV series)", "List of Bleach episodes"],
        )
        inventory = direct.evidence["facts"][0][
            "wikipedia_episode_inventory"
        ]
        self.assertEqual(inventory["status"], "complete")
        self.assertEqual(
            inventory["episode_list_relationship"]["from_title"],
            "Bleach (TV series)",
        )

    @patch("telepiplex_search.direct_link.lookup_wikipedia_episode_page")
    @patch("telepiplex_search.direct_link.enrich_wikidata_entities")
    @patch("telepiplex_search.direct_link.lookup_wikipedia_page")
    def test_wikipedia_series_uses_same_qid_english_episode_table(
        self,
        lookup_page,
        enrich,
        lookup_episodes,
    ):
        lookup_page.side_effect = [
            {
                "wikibase_item": "Q124175370",
                "title": "百年孤独 (电视剧)",
                "canonical_title": "百年孤独 (电视剧)",
                "official_english_title": "One Hundred Years of Solitude",
                "english_page_title": "One Hundred Years of Solitude (TV series)",
                "year": "2024",
                "media_type": "series",
                "url": "https://zh.wikipedia.org/wiki/百年孤独_(电视剧)",
            },
            {
                "wikibase_item": "Q124175370",
                "title": "One Hundred Years of Solitude (TV series)",
                "canonical_title": "One Hundred Years of Solitude (TV series)",
                "official_english_title": "One Hundred Years of Solitude",
                "year": "2024",
                "media_type": "series",
                "url": "https://en.wikipedia.org/wiki/One_Hundred_Years_of_Solitude_(TV_series)",
            },
        ]
        enrich.return_value = {"Q124175370": {
            "wikibase_item": "Q124175370",
            "external_ids": {"wikidata": "Q124175370"},
            "english_title": "One Hundred Years of Solitude",
            "year": "2024",
            "media_type": "series",
            "season_count": 2,
            "episode_count": 16,
        }}
        lookup_episodes.side_effect = [
            {
                "status": "partial",
                "items": [{
                    "season_number": None,
                    "episode_number": None,
                    "overall_number": 1,
                    "air_date": "2024-12-11",
                }],
                "season_totals": {},
                "source_url": "https://zh.wikipedia.org/wiki/百年孤独_(电视剧)",
                "source_language": "zh",
                "revision_id": 100,
                "error": "",
            },
            {
                "status": "complete",
                "items": [
                    {
                        "season_number": season,
                        "episode_number": episode,
                        "overall_number": (season - 1) * 8 + episode,
                        "air_date": (
                            "2024-12-11"
                            if season == 1
                            else "2026-08-05"
                            if episode < 8
                            else "2026-08-26"
                        ),
                    }
                    for season in (1, 2)
                    for episode in range(1, 9)
                ],
                "season_totals": {1: 8, 2: 8},
                "source_url": "https://en.wikipedia.org/wiki/One_Hundred_Years_of_Solitude_(TV_series)",
                "source_language": "en",
                "revision_id": 200,
                "error": "",
            },
        ]

        direct = resolve_direct_link(MetadataLink(
            provider="wikipedia",
            media_type="",
            entity_id="zh:百年孤独 (电视剧)",
            scope="work",
            url="https://zh.wikipedia.org/wiki/百年孤独_(电视剧)",
        ))

        fact = direct.evidence["facts"][0]
        self.assertEqual(len(fact["episodes"]), 16)
        self.assertEqual(fact["wikipedia_episode_inventory"]["status"], "complete")
        self.assertEqual(
            fact["wikipedia_episode_inventory"]["season_totals"],
            {1: 8, 2: 8},
        )
        self.assertEqual(
            [call.args[:2] for call in lookup_episodes.call_args_list],
            [
                ("zh", "百年孤独 (电视剧)"),
                ("en", "One Hundred Years of Solitude (TV series)"),
            ],
        )

    @patch("telepiplex_search.direct_link.requests.get")
    def test_short_wikipedia_link_resolves_to_stable_article(self, get):
        get.side_effect = [
            type("Response", (), {
                "status_code": 302,
                "headers": {
                    "Location": (
                        "https://zh.wikipedia.org/wiki/"
                        "%E7%B9%81%E8%8A%B1_(2023%E5%B9%B4%E7%94%B5%E8%A7%86%E5%89%A7)"
                    ),
                },
                "text": "",
                "url": "https://w.wiki/AbCd",
            })(),
            type("Response", (), {
                "status_code": 200,
                "headers": {},
                "text": "<title>繁花 (2023年电视剧) - 维基百科</title>",
                "url": (
                    "https://zh.wikipedia.org/wiki/"
                    "%E7%B9%81%E8%8A%B1_(2023%E5%B9%B4%E7%94%B5%E8%A7%86%E5%89%A7)"
                ),
            })(),
        ]
        parsed = ParsedInput(
            kind="resolvable_link",
            raw_query="分享 https://w.wiki/AbCd",
            link=MetadataLink(
                provider="wikipedia",
                media_type="",
                entity_id="",
                scope="work",
                url="https://w.wiki/AbCd",
            ),
            urls=("https://w.wiki/AbCd",),
            fallback_title="分享",
        )

        link, fallback_title = resolve_shared_metadata_link(parsed)

        self.assertEqual(link.provider, "wikipedia")
        self.assertTrue(link.entity_id.startswith("zh:繁花"))
        self.assertEqual(fallback_title, "分享")

    @patch("telepiplex_search.direct_link.requests.get")
    def test_unresolved_supported_page_returns_clean_page_title(self, get):
        get.return_value = type("Response", (), {
            "status_code": 200,
            "headers": {},
            "text": "<meta property='og:title' content='The Glory (2022)'>",
            "url": "https://thetvdb.com/search?query=glory",
        })()
        parsed = ParsedInput(
            kind="resolvable_link",
            raw_query="https://thetvdb.com/search?query=glory",
            link=MetadataLink(
                provider="tvdb",
                media_type="",
                entity_id="",
                scope="work",
                url="https://thetvdb.com/search?query=glory",
            ),
            urls=("https://thetvdb.com/search?query=glory",),
        )

        link, fallback_title = resolve_shared_metadata_link(parsed)

        self.assertIsNone(link)
        self.assertEqual(fallback_title, "The Glory 2022")

    @patch("telepiplex_search.direct_link.requests.get")
    def test_redirect_outside_supported_hosts_is_rejected(self, get):
        get.return_value = type("Response", (), {
            "status_code": 302,
            "headers": {"Location": "http://127.0.0.1/private"},
            "text": "",
            "url": "https://w.wiki/AbCd",
        })()
        parsed = ParsedInput(
            kind="resolvable_link",
            raw_query="https://w.wiki/AbCd",
            link=MetadataLink(
                provider="wikipedia",
                media_type="",
                entity_id="",
                scope="work",
                url="https://w.wiki/AbCd",
            ),
            urls=("https://w.wiki/AbCd",),
        )

        with self.assertRaisesRegex(
            DirectLinkError,
            "direct_link_redirect_rejected",
        ):
            resolve_shared_metadata_link(parsed)

    @patch("telepiplex_search.direct_link.requests.get")
    def test_shared_link_http_failure_has_safe_reason_code(self, get):
        get.return_value = type("Response", (), {
            "status_code": 418,
            "headers": {},
            "text": "",
            "url": (
                "https://www.douban.com/doubanapp/dispatch/movie/"
                "36235977?dt_dapp=1"
            ),
        })()
        parsed = ParsedInput(
            kind="resolvable_link",
            raw_query=(
                "https://www.douban.com/doubanapp/dispatch/movie/"
                "36235977?dt_dapp=1"
            ),
            link=MetadataLink(
                provider="douban",
                media_type="",
                entity_id="",
                scope="work",
                url=(
                    "https://www.douban.com/doubanapp/dispatch/movie/"
                    "36235977?dt_dapp=1"
                ),
            ),
            urls=(
                "https://www.douban.com/doubanapp/dispatch/movie/"
                "36235977?dt_dapp=1",
            ),
        )

        with self.assertRaises(DirectLinkError) as failed:
            resolve_shared_metadata_link(parsed)

        self.assertEqual(failed.exception.code, "fixed_link_read_failed")
        self.assertEqual(failed.exception.details, ("http_status:418",))

    @patch("telepiplex_search.direct_link.enrich_wikidata_entities")
    @patch("telepiplex_search.direct_link.lookup_wikipedia_page")
    def test_wikipedia_article_locks_wikibase_identity(self, lookup, enrich):
        enrich.return_value = {}
        lookup.return_value = {
            "wikibase_item": "Q123",
            "title": "The Grand Budapest Hotel",
            "official_english_title": "The Grand Budapest Hotel",
            "year": "2014",
            "media_type": "movie",
            "url": "https://en.wikipedia.org/wiki/The_Grand_Budapest_Hotel",
        }

        direct = resolve_direct_link(MetadataLink(
            provider="wikipedia",
            media_type="",
            entity_id="en:The Grand Budapest Hotel",
            scope="work",
            url="https://en.wikipedia.org/wiki/The_Grand_Budapest_Hotel",
        ))

        self.assertEqual(direct.stable_identity, ("wikipedia", "Q123"))
        self.assertEqual(direct.media_type, "movie")
        self.assertEqual(direct.query, "The Grand Budapest Hotel")

    @patch("telepiplex_search.direct_link.lookup_wikipedia_episode_page")
    @patch("telepiplex_search.direct_link.enrich_wikidata_entities")
    @patch("telepiplex_search.direct_link.lookup_wikipedia_page")
    def test_wikipedia_exact_read_restores_structural_wikidata_fields(
        self,
        lookup,
        enrich,
        lookup_episodes,
    ):
        lookup.return_value = {
            "wikibase_item": "Q74801",
            "title": "Veep",
            "official_english_title": "Veep",
            "year": "2012",
            "media_type": "series",
            "url": "https://en.wikipedia.org/wiki/Veep",
        }
        enrich.return_value = {"Q74801": {
            "wikibase_item": "Q74801",
            "chinese_title": "副人之仁",
            "english_title": "Veep",
            "aliases": ["副总统"],
            "year": "2012",
            "media_type": "series",
            "countries": ["Q30"],
            "season_count": 7,
            "episode_count": 65,
        }}
        lookup_episodes.return_value = {
            "status": "absent",
            "items": [],
            "season_totals": {},
            "source_url": "https://en.wikipedia.org/wiki/Veep",
            "source_language": "en",
            "revision_id": 1,
            "error": "wikipedia_table_absent",
        }

        direct = resolve_direct_link(MetadataLink(
            provider="wikipedia",
            media_type="",
            entity_id="en:Veep",
            scope="work",
            url="https://en.wikipedia.org/wiki/Veep",
        ))

        fact = direct.evidence["facts"][0]
        self.assertEqual(fact["chinese_title"], "副人之仁")
        self.assertEqual(fact["season_count"], 7)
        self.assertEqual(fact["episode_count"], 65)

    @patch("telepiplex_search.direct_link.lookup_wikipedia_page")
    def test_wikipedia_disambiguation_page_is_not_a_frozen_identity(self, lookup):
        lookup.return_value = {
            "wikibase_item": "Q-disambiguation",
            "title": "副总统",
            "is_disambiguation": True,
            "extract": "副总统可以指多个条目。",
            "url": "https://zh.wikipedia.org/wiki/副总统",
        }

        with self.assertRaises(DirectLinkError) as failed:
            resolve_direct_link(MetadataLink(
                provider="wikipedia",
                media_type="",
                entity_id="zh:副总统",
                scope="work",
                url="https://zh.wikipedia.org/wiki/副总统",
            ))

        self.assertEqual(failed.exception.code, "wikipedia_disambiguation")
        self.assertEqual(failed.exception.details, ("副总统",))

    @patch("telepiplex_search.direct_link.lookup_douban_subject")
    def test_douban_subject_locks_stable_identity(self, lookup):
        lookup.return_value = {
            "subject_id": "35314632",
            "title": "The Glory",
            "english_title": "The Glory",
            "year": "2022",
            "media_type": "series",
        }

        direct = resolve_direct_link(MetadataLink(
            provider="douban",
            media_type="",
            entity_id="35314632",
            scope="work",
            url="https://movie.douban.com/subject/35314632/",
        ))

        self.assertEqual(direct.stable_identity, ("douban_subject", "35314632"))
        self.assertEqual(direct.scope, "work")
        self.assertEqual(direct.media_type, "series")
        self.assertEqual(direct.query, "The Glory")

    @patch("telepiplex_search.direct_link.lookup_douban_subject")
    def test_douban_season_link_preserves_scope_and_normalizes_root(self, lookup):
        lookup.return_value = {
            "subject_id": "36666949",
            "title": "龙之家族",
            "chinese_title": "龙之家族",
            "douban_title_raw": "龙之家族 第三季",
            "english_title": "House of the Dragon Season 3",
            "original_title": "House of the Dragon Season 3",
            "year": "2026",
            "media_type": "series",
            "season_number": 3,
            "external_ids": {"douban_subject": "36666949"},
        }

        direct = resolve_direct_link(MetadataLink(
            provider="douban",
            media_type="",
            entity_id="36666949",
            scope="work",
            url="https://movie.douban.com/subject/36666949/",
        ))

        self.assertEqual(direct.scope, "season")
        self.assertEqual(direct.season_number, 3)
        self.assertEqual(direct.title, "龙之家族")
        self.assertEqual(direct.query, "House of the Dragon S03")
        self.assertEqual(direct.evidence["root_lookup_year"], "")

    @patch("telepiplex_search.direct_link.lookup_douban_subject")
    def test_douban_spanish_original_is_search_fallback_not_english(self, lookup):
        lookup.return_value = {
            "subject_id": "30482958",
            "title": "百年孤独",
            "chinese_title": "百年孤独",
            "douban_title_raw": "百年孤独 第一季",
            "english_title": "",
            "original_title": "",
            "source_original_title": "Cien años de soledad Season 1",
            "original_language": "es",
            "official_english_title": "",
            "year": "2024",
            "media_type": "series",
            "season_number": 1,
            "external_ids": {"douban_subject": "30482958"},
        }

        direct = resolve_direct_link(MetadataLink(
            provider="douban",
            media_type="",
            entity_id="30482958",
            scope="work",
            url="https://movie.douban.com/subject/30482958/",
        ))

        fact = direct.evidence["facts"][0]
        self.assertEqual(direct.query, "Cien años de soledad S01")
        self.assertEqual(direct.scope, "season")
        self.assertEqual(direct.season_number, 1)
        self.assertEqual(
            fact["source_original_title"],
            "Cien años de soledad",
        )
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["official_english_title"], "")

    @patch("telepiplex_search.direct_link.get_tvdb_episode")
    @patch("telepiplex_search.direct_link.get_tvdb_series")
    def test_tvdb_episode_locks_series_and_episode(self, series, episode):
        episode.return_value = {
            "tvdb_episode_id": "9481027",
            "tvdb_series_id": "411469",
            "season_number": 1,
            "episode_number": 3,
        }
        series.return_value = {
            "tvdb_series_id": "411469",
            "name": "The Glory",
            "english_title": "The Glory",
            "year": "2022",
            "episodes": [{
                "tvdb_episode_id": "9481027",
                "season_number": 1,
                "episode_number": 3,
                "aired": "2022-12-30",
            }],
        }

        direct = resolve_direct_link(MetadataLink(
            provider="tvdb",
            media_type="series",
            entity_id="9481027",
            scope="episode",
            url="https://thetvdb.com/episodes/9481027",
        ))

        self.assertEqual(direct.stable_identity, ("tvdb", "411469"))
        self.assertEqual((direct.season_number, direct.episode_number), (1, 3))
        self.assertIn("S01E03", direct.query)

    @patch("telepiplex_search.direct_link.get_tvdb_episode")
    @patch("telepiplex_search.direct_link.get_tvdb_series")
    def test_tvdb_s00_episode_link_is_rejected(self, series, episode):
        episode.return_value = {
            "tvdb_episode_id": "1",
            "tvdb_series_id": "2",
            "season_number": 0,
            "episode_number": 1,
        }
        series.return_value = {
            "tvdb_series_id": "2",
            "english_title": "Series",
            "episodes": [],
        }

        with self.assertRaisesRegex(
            DirectLinkError,
            "unsupported_special_scope",
        ):
            resolve_direct_link(MetadataLink(
                provider="tvdb",
                media_type="series",
                entity_id="1",
                scope="episode",
                url="https://thetvdb.com/episodes/1",
            ))

    @patch("telepiplex_search.direct_link.get_tvdb_season")
    @patch("telepiplex_search.direct_link.get_tvdb_series")
    def test_tvdb_s00_season_link_is_rejected(self, series, season):
        season.return_value = {
            "tvdb_season_id": "1",
            "tvdb_series_id": "2",
            "season_number": 0,
        }
        series.return_value = {
            "tvdb_series_id": "2",
            "english_title": "Series",
            "episodes": [],
        }

        with self.assertRaisesRegex(
            DirectLinkError,
            "unsupported_special_scope",
        ):
            resolve_direct_link(MetadataLink(
                provider="tvdb",
                media_type="series",
                entity_id="1",
                scope="season",
                url="https://thetvdb.com/seasons/1",
            ))

    @patch(
        "telepiplex_search.direct_link.lookup_douban_subject",
        return_value=None,
    )
    def test_failed_direct_lookup_never_becomes_site_brand_text(self, _lookup):
        with self.assertRaisesRegex(DirectLinkError, "direct_link_not_found"):
            resolve_direct_link(MetadataLink(
                provider="douban",
                media_type="",
                entity_id="1",
                scope="work",
                url="https://movie.douban.com/subject/1/",
            ))

    @patch("telepiplex_search.direct_link.get_tvdb_movie")
    def test_tvdb_configuration_failure_is_a_fixed_link_error(self, movie):
        movie.side_effect = TvdbConfigError(
            "metadata.tvdb.api_key 未配置",
            "credential_missing",
        )

        with self.assertRaises(DirectLinkError) as failed:
            resolve_direct_link(MetadataLink(
                provider="tvdb",
                media_type="movie",
                entity_id="1",
                scope="movie",
                url="https://thetvdb.com/movies/1",
            ))

        self.assertEqual(failed.exception.code, "fixed_link_read_failed")
        self.assertEqual(
            failed.exception.details,
            ("tvdb:credential_missing",),
        )

    @patch("telepiplex_search.direct_link.lookup_wikipedia_page")
    def test_wikipedia_source_failure_is_a_fixed_link_error(self, lookup):
        lookup.side_effect = WikipediaPageLookupError("rate_limited")

        with self.assertRaises(DirectLinkError) as failed:
            resolve_direct_link(MetadataLink(
                provider="wikipedia",
                media_type="",
                entity_id="en:Dune",
                scope="work",
                url="https://en.wikipedia.org/wiki/Dune",
            ))

        self.assertEqual(failed.exception.code, "fixed_link_read_failed")
        self.assertEqual(
            failed.exception.details,
            ("wikipedia:rate_limited",),
        )

    @patch("telepiplex_search.direct_link.lookup_douban_subject")
    def test_douban_source_failure_is_a_fixed_link_error(self, lookup):
        lookup.side_effect = DoubanSubjectLookupError("blocked")

        with self.assertRaises(DirectLinkError) as failed:
            resolve_direct_link(MetadataLink(
                provider="douban",
                media_type="",
                entity_id="1",
                scope="work",
                url="https://movie.douban.com/subject/1/",
            ))

        self.assertEqual(failed.exception.code, "fixed_link_read_failed")
        self.assertEqual(
            failed.exception.details,
            ("douban:blocked",),
        )

    @patch("telepiplex_search.direct_link.get_tmdb_entity")
    def test_tmdb_movie_locks_tmdb_identity(self, get_entity):
        get_entity.return_value = {
            "tmdb_id": "438631",
            "external_ids": {"tmdb": "438631", "imdb": "tt1160419"},
            "title": "Dune",
            "official_english_title": "Dune",
            "year": "2021",
            "media_type": "movie",
            "url": "https://www.themoviedb.org/movie/438631",
        }

        direct = resolve_direct_link(MetadataLink(
            provider="tmdb",
            media_type="movie",
            entity_id="438631",
            scope="work",
            url="https://www.themoviedb.org/movie/438631-dune",
        ))

        self.assertEqual(direct.stable_identity, ("tmdb", "438631"))
        self.assertEqual(direct.media_type, "movie")
        self.assertEqual(direct.query, "Dune")

    @patch("telepiplex_search.direct_link.get_anilist_media")
    def test_anilist_link_locks_anilist_identity(self, get_media):
        get_media.return_value = {
            "anilist_id": "1142",
            "external_ids": {"anilist": "1142"},
            "title": "Hachimitsu to Clover II",
            "romanized_original_title": "Hachimitsu to Clover II",
            "official_english_title": "Honey and Clover II",
            "year": "2005",
            "media_type": "series",
            "url": "https://anilist.co/anime/1142",
        }

        direct = resolve_direct_link(MetadataLink(
            provider="anilist",
            media_type="",
            entity_id="1142",
            scope="work",
            url="https://anilist.co/anime/1142/Honey-and-Clover-II/",
        ))

        self.assertEqual(direct.stable_identity, ("anilist", "1142"))
        self.assertEqual(direct.title, "Hachimitsu to Clover II")

    @patch("telepiplex_search.direct_link.get_tmdb_entity")
    def test_tmdb_configuration_failure_is_a_fixed_link_error(self, get_entity):
        get_entity.side_effect = TmdbConfigError("missing", "credential_missing")

        with self.assertRaises(DirectLinkError) as failed:
            resolve_direct_link(MetadataLink(
                provider="tmdb",
                media_type="movie",
                entity_id="1",
                scope="work",
                url="https://www.themoviedb.org/movie/1",
            ))

        self.assertEqual(failed.exception.details, ("tmdb:credential_missing",))

    @patch("telepiplex_search.direct_link.get_anilist_media")
    def test_anilist_failure_is_a_fixed_link_error(self, get_media):
        get_media.side_effect = AniListRequestError("rate limited", "rate_limited")

        with self.assertRaises(DirectLinkError) as failed:
            resolve_direct_link(MetadataLink(
                provider="anilist",
                media_type="",
                entity_id="1",
                scope="work",
                url="https://anilist.co/anime/1",
            ))

        self.assertEqual(failed.exception.details, ("anilist:rate_limited",))


if __name__ == "__main__":
    unittest.main()
