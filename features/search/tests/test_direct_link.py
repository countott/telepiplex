import unittest
from unittest.mock import patch

from telepiplex_search.adapters.tvdb import TvdbConfigError
from telepiplex_search.adapters.douban import DoubanSubjectLookupError
from telepiplex_search.adapters.wikipedia import WikipediaPageLookupError
from telepiplex_search.direct_link import (
    DirectLinkError,
    resolve_direct_link,
    resolve_shared_metadata_link,
)
from telepiplex_search.input_contract import MetadataLink, ParsedInput


class DirectLinkTest(unittest.TestCase):
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

    @patch("telepiplex_search.direct_link.lookup_wikipedia_page")
    def test_wikipedia_article_locks_wikibase_identity(self, lookup):
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


if __name__ == "__main__":
    unittest.main()
