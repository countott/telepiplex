import unittest
from unittest.mock import Mock, patch

import requests

from telepiplex_search.adapters import tmdb
from telepiplex_search.context import runtime_context


class TmdbAdapterTest(unittest.TestCase):
    def test_disabled_and_missing_credentials_have_distinct_codes(self):
        runtime_context.configure({"metadata": {"tmdb": {"enable": False}}})
        with self.assertRaises(tmdb.TmdbConfigError) as disabled:
            tmdb._get_tmdb_config()
        self.assertEqual(disabled.exception.code, "disabled")

        runtime_context.configure({
            "metadata": {"tmdb": {"enable": True, "api_key": ""}},
        })
        with self.assertRaises(tmdb.TmdbConfigError) as missing:
            tmdb._get_tmdb_config()
        self.assertEqual(missing.exception.code, "credential_missing")

    @patch.object(tmdb.requests, "get")
    def test_unauthorized_request_is_authentication_failure(self, get):
        runtime_context.configure({
            "metadata": {
                "tmdb": {"enable": True, "api_key": "read-token"},
            },
        })
        response = Mock(status_code=401)
        response.raise_for_status.side_effect = requests.HTTPError(
            "unauthorized",
            response=response,
        )
        get.return_value = response

        with self.assertRaises(tmdb.TmdbAuthenticationError):
            tmdb.search_tmdb("Dune", "movie", "2021")

    @patch.object(tmdb, "_tmdb_get")
    def test_movie_detail_normalizes_cross_ids_and_descriptive_metadata(self, get):
        get.return_value = {
            "id": 438631,
            "title": "Dune",
            "original_title": "Dune",
            "original_language": "en",
            "release_date": "2021-09-15",
            "runtime": 155,
            "status": "Released",
            "overview": "A desert world.",
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "genres": [{"id": 878, "name": "Science Fiction"}],
            "production_countries": [{"iso_3166_1": "US", "name": "United States"}],
            "production_companies": [{"id": 1, "name": "Legendary Pictures"}],
            "external_ids": {
                "imdb_id": "tt1160419",
                "wikidata_id": "Q17174930",
            },
            "translations": {
                "translations": [{
                    "iso_639_1": "zh",
                    "iso_3166_1": "CN",
                    "data": {"title": "沙丘", "overview": "沙丘世界"},
                }],
            },
            "alternative_titles": {"titles": [{"title": "Dune: Part One"}]},
            "credits": {
                "cast": [{"id": 2, "name": "Timothée Chalamet", "character": "Paul"}],
                "crew": [{"id": 3, "name": "Denis Villeneuve", "job": "Director"}],
            },
            "release_dates": {
                "results": [{
                    "iso_3166_1": "US",
                    "release_dates": [{"certification": "PG-13", "type": 3}],
                }],
            },
        }

        fact = tmdb.get_tmdb_entity("movie", "438631")

        self.assertEqual(fact["external_ids"]["tmdb"], "438631")
        self.assertEqual(fact["external_ids"]["imdb"], "tt1160419")
        self.assertEqual(fact["external_ids"]["wikidata"], "Q17174930")
        self.assertEqual(fact["chinese_title"], "沙丘")
        self.assertEqual(fact["runtime_minutes"], 155)
        self.assertEqual(fact["studios"], ["Legendary Pictures"])
        self.assertEqual(fact["cast"][0]["name"], "Timothée Chalamet")
        self.assertEqual(fact["certifications"], ["US:PG-13"])
        self.assertEqual(
            get.call_args.kwargs["params"]["append_to_response"],
            "external_ids,translations,alternative_titles,credits,release_dates,images",
        )

    @patch.object(tmdb, "_tmdb_get")
    def test_series_search_uses_first_air_year_and_media_type(self, get):
        get.return_value = {
            "results": [{
                "id": 71912,
                "name": "The Witcher",
                "original_name": "The Witcher",
                "original_language": "en",
                "first_air_date": "2019-12-20",
            }],
        }

        result = tmdb.search_tmdb("The Witcher", "series", "2019")

        self.assertEqual(result[0]["tmdb_id"], "71912")
        self.assertEqual(result[0]["media_type"], "series")
        self.assertEqual(result[0]["year"], "2019")
        self.assertEqual(get.call_args.args[0], "/search/tv")
        self.assertEqual(get.call_args.kwargs["params"]["first_air_date_year"], "2019")

    @patch.object(tmdb, "_tmdb_get")
    def test_external_tvdb_id_finds_exact_series_before_title_search(self, get):
        get.return_value = {
            "movie_results": [],
            "tv_results": [{
                "id": 71912,
                "name": "The Witcher",
                "original_name": "The Witcher",
                "original_language": "en",
                "first_air_date": "2019-12-20",
            }],
        }

        result = tmdb.find_tmdb_by_external_id(
            "tvdb",
            "362696",
            "series",
        )

        self.assertEqual(result[0]["tmdb_id"], "71912")
        self.assertEqual(get.call_args.args[0], "/find/362696")
        self.assertEqual(
            get.call_args.kwargs["params"]["external_source"],
            "tvdb_id",
        )


if __name__ == "__main__":
    unittest.main()
