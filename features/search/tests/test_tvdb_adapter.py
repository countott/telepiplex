import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from telepiplex_search.adapters import tvdb
from telepiplex_search.context import runtime_context
from telepiplex_search.service import SearchFeature


class TvdbAdapterTest(unittest.TestCase):
    def setUp(self):
        tvdb._token_cache.update({
            "token": "",
            "created_at": 0.0,
            "api_key": "",
            "subscriber_pin": "",
        })

    def test_disabled_and_missing_credentials_have_distinct_codes(self):
        runtime_context.configure({
            "metadata": {"tvdb": {"enable": False}},
        })
        with self.assertRaises(tvdb.TvdbConfigError) as disabled:
            tvdb._get_tvdb_config()
        self.assertEqual(disabled.exception.code, "disabled")

        runtime_context.configure({
            "metadata": {"tvdb": {"enable": True, "api_key": ""}},
        })
        with self.assertRaises(tvdb.TvdbConfigError) as missing:
            tvdb._get_tvdb_config()
        self.assertEqual(missing.exception.code, "credential_missing")

    @patch.object(tvdb.requests, "post")
    def test_login_unauthorized_is_authentication_failure(self, post):
        runtime_context.configure({
            "metadata": {
                "tvdb": {
                    "enable": True,
                    "api_key": "configured-secret",
                },
            },
        })
        rejected = Mock(status_code=401)
        rejected.raise_for_status.side_effect = requests.HTTPError(
            "unauthorized",
            response=rejected,
        )
        post.return_value = rejected

        with self.assertRaises(tvdb.TvdbAuthenticationError):
            tvdb._login_tvdb(tvdb._get_tvdb_config())

    def test_feature_provider_reports_missing_credentials(self):
        config = {
            "metadata": {
                "tvdb": {
                    "enable": True,
                    "api_key": "",
                },
            },
        }
        runtime_context.configure(config)
        feature = SearchFeature(config=config, host=Mock())

        result = feature._tvdb_provider({
            "hypotheses": [{"title": "The Glory", "year": "2022"}],
        })

        self.assertEqual(result["status"], "credential_missing")

    @patch(
        "telepiplex_search.service.search_tvdb_movies",
        side_effect=tvdb.TvdbAuthenticationError("rejected"),
    )
    def test_feature_provider_reports_authentication_failure(self, _search):
        config = {
            "metadata": {
                "tvdb": {
                    "enable": True,
                    "api_key": "configured-secret",
                },
            },
        }
        runtime_context.configure(config)
        feature = SearchFeature(config=config, host=Mock())

        result = feature._tvdb_provider({
            "hypotheses": [{"title": "The Glory", "year": "2022"}],
        })

        self.assertEqual(result["status"], "authentication_failed")

    def test_korean_primary_name_uses_latin_alias_and_search_image(self):
        item = tvdb._normalize_search_item(
            {
                "tvdb_id": "411469",
                "name": "더 글로리",
                "aliases": ["The Glory (2022)", "The Glory (KR)"],
                "image_url": "https://art.example/glory.jpg",
                "year": "2022",
            },
            "series",
        )

        self.assertEqual(item["english_title"], "The Glory")
        self.assertEqual(item["cover_url"], "https://art.example/glory.jpg")
        self.assertEqual(item["tvdb_series_id"], "411469")

    def test_search_image_falls_back_to_poster_thumbnail_then_image(self):
        self.assertEqual(
            tvdb._normalize_search_item({"tvdb_id": "1", "poster": "poster.jpg"}, "movie")["cover_url"],
            "poster.jpg",
        )
        self.assertEqual(
            tvdb._normalize_search_item({"tvdb_id": "1", "posters": ["", "posters.jpg"]}, "movie")["cover_url"],
            "posters.jpg",
        )
        self.assertEqual(
            tvdb._normalize_search_item({"tvdb_id": "1", "thumbnail": "thumb.jpg"}, "movie")["cover_url"],
            "thumb.jpg",
        )
        self.assertEqual(
            tvdb._normalize_search_item({"tvdb_id": "1", "image": "image.jpg"}, "movie")["cover_url"],
            "image.jpg",
        )

    def test_non_english_translations_are_preserved_as_match_aliases(self):
        item = tvdb._normalize_search_item(
            {
                "tvdb_id": "411469",
                "name": "더 글로리",
                "name_translated": "黑暗荣耀",
                "translations": {"eng": "The Glory", "zho": "黑暗荣耀"},
                "aliases": ["The Glory (2022)"],
                "year": "2022",
            },
            "series",
        )

        self.assertEqual(item["english_title"], "The Glory")
        self.assertIn("黑暗荣耀", item["aliases"])

    def test_structured_japanese_titles_are_preserved(self):
        item = tvdb._normalize_search_item(
            {
                "tvdb_id": "267440",
                "name": "進撃の巨人",
                "original_language": "ja",
                "official_english_title": "Attack on Titan",
                "romanized_original_title": "Shingeki no Kyojin",
            },
            "series",
        )

        self.assertEqual(item["original_title"], "進撃の巨人")
        self.assertEqual(item["original_language"], "ja")
        self.assertEqual(item["official_english_title"], "Attack on Titan")
        self.assertEqual(item["romanized_original_title"], "Shingeki no Kyojin")

    @patch.object(tvdb, "_tvdb_get")
    def test_movie_search_uses_translation_endpoint_only_without_latin_title(self, get_mock):
        get_mock.side_effect = [
            {"data": [{"tvdb_id": "123", "name": "中文片名", "year": "2024"}]},
            {"data": {"name": "English Movie", "language": "eng"}},
        ]

        result = tvdb.search_tvdb_movies("中文片名", "2024")

        self.assertEqual(result[0]["english_title"], "English Movie")
        self.assertEqual(result[0]["tvdb_movie_id"], "123")
        self.assertEqual(get_mock.call_args_list[1].args[0], "/movies/123/translations/eng")

    @patch.object(tvdb, "_tvdb_get")
    def test_latin_alias_avoids_translation_request(self, get_mock):
        get_mock.return_value = {
            "data": [
                {
                    "tvdb_id": "411469",
                    "name": "더 글로리",
                    "aliases": ["The Glory (2022)"],
                    "year": "2022",
                }
            ]
        }

        result = tvdb.search_tvdb_series("黑暗荣耀", "2022")

        self.assertEqual(result[0]["english_title"], "The Glory")
        get_mock.assert_called_once_with("/search", params={"query": "黑暗荣耀", "type": "series", "year": "2022"})

    @patch.object(tvdb, "_tvdb_get")
    def test_movie_artwork_uses_extended_image(self, get_mock):
        get_mock.return_value = {"data": {"image": "https://art.example/movie.jpg"}}

        self.assertEqual(
            tvdb.get_tvdb_movie_artwork_url("123"),
            "https://art.example/movie.jpg",
        )
        get_mock.assert_called_once_with("/movies/123/extended", params={"short": True})

    @patch.object(tvdb, "_tvdb_get")
    def test_series_episodes_preserve_special_season_zero(self, get_mock):
        get_mock.return_value = {
            "data": {
                "episodes": [{
                    "id": 100,
                    "name": "The Movie",
                    "seasonNumber": 0,
                    "number": 5,
                }]
            }
        }

        result = tvdb.get_tvdb_series_episodes("series-1")

        self.assertEqual(result[0]["season_number"], 0)
        self.assertEqual(result[0]["episode_number"], 5)


if __name__ == "__main__":
    unittest.main()
