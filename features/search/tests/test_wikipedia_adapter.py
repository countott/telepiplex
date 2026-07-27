import unittest
from unittest.mock import Mock, patch

import requests

from telepiplex_search.adapters.wikipedia import (
    _classification,
    lookup_wikipedia_evidence,
)


class WikipediaAdapterTest(unittest.TestCase):
    def test_empty_expanded_queries_are_unavailable_not_not_found(self):
        result = lookup_wikipedia_evidence([], languages=("zh",))

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["error"], "source_queries_empty")

    @patch("telepiplex_search.adapters.wikipedia.requests.get")
    def test_returns_extract_and_findable_page_url(self, get_mock):
        zh_response = Mock()
        zh_response.raise_for_status.return_value = None
        zh_response.json.return_value = {
            "query": {
                "pages": {
                    "1": {
                        "title": "想見你 (電影)",
                        "extract": "2022年上映，為電視劇《想見你》的同名續篇電影。",
                        "pageprops": {"wikibase_item": "Q115000000"},
                        "fullurl": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    }
                }
            }
        }
        en_response = Mock()
        en_response.raise_for_status.return_value = None
        en_response.json.return_value = {
            "query": {
                "pages": [{
                    "title": "Someday or One Day",
                    "extract": "Someday or One Day is a 2022 Taiwanese film.",
                    "pageprops": {"wikibase_item": "Q115000000"},
                    "fullurl": "https://en.wikipedia.org/wiki/Someday_or_One_Day",
                }]
            }
        }
        get_mock.side_effect = [zh_response, en_response]

        result = lookup_wikipedia_evidence(
            ["想见你 电影 2022"], languages=("zh", "en")
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["facts"][0]["wikibase_item"], "Q115000000")
        self.assertIn("續篇電影", result["facts"][0]["extract"])
        self.assertEqual(result["facts"][0]["year"], "2022")
        self.assertEqual(result["facts"][0]["media_type"], "movie")
        self.assertEqual(result["facts"][1]["language"], "en")
        self.assertEqual(
            result["source_urls"],
            [
                "https://zh.wikipedia.org/wiki/想見你_(電影)",
                "https://en.wikipedia.org/wiki/Someday_or_One_Day",
            ],
        )

    @patch("telepiplex_search.adapters.wikipedia.requests.get", side_effect=OSError("dns failed"))
    def test_server_failure_is_soft_evidence(self, _get_mock):
        result = lookup_wikipedia_evidence(["想见你"], languages=("zh",))
        self.assertEqual(result["status"], "server_down")
        self.assertEqual(result["facts"], [])
        self.assertIn("dns failed", result["error"])

    def test_numeric_work_title_is_not_used_as_release_year(self):
        self.assertEqual(
            _classification(
                "1917 (2019 film)",
                "1917 is a 2019 war film directed by Sam Mendes.",
            ),
            ("2019", "movie"),
        )

    def test_conflicting_animation_and_movie_signals_leave_type_unknown(self):
        self.assertEqual(
            _classification(
                "奇巧計程車",
                "日本原創電視動畫，另有動畫電影。",
            ),
            ("", ""),
        )

    @patch("telepiplex_search.adapters.wikipedia.requests.get")
    def test_http_429_is_rate_limited_not_server_down(self, get_mock):
        response = Mock()
        response.status_code = 429
        response.raise_for_status.side_effect = requests.HTTPError(
            "429 Client Error",
            response=response,
        )
        get_mock.return_value = response

        result = lookup_wikipedia_evidence(
            ["蜂蜜与四叶草"],
            languages=("zh",),
        )

        self.assertEqual(result["status"], "rate_limited")
        self.assertEqual(result["facts"], [])

    @patch("time.sleep")
    @patch("telepiplex_search.adapters.wikipedia.requests.get")
    def test_multiple_requests_use_shared_throttle(
        self, get_mock, sleep
    ):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {"query": {"pages": []}}
        get_mock.return_value = response

        lookup_wikipedia_evidence(
            ["Dune", "Joker"],
            languages=("en",),
            min_interval=1.5,
        )

        sleep.assert_called()

    @patch("telepiplex_search.adapters.wikipedia.requests.get")
    def test_retry_after_opens_shared_rate_limit_circuit(self, get_mock):
        import telepiplex_search.adapters.wikipedia as wikipedia

        previous = getattr(wikipedia, "_RATE_LIMIT_STATE", None)
        wikipedia._RATE_LIMIT_STATE = {
            "last_request_at": 0.0,
            "limited_until": 0.0,
        }
        limited = Mock()
        limited.status_code = 429
        limited.headers = {"Retry-After": "30"}
        limited.raise_for_status.side_effect = requests.HTTPError(
            "429 Client Error",
            response=limited,
        )
        success = Mock()
        success.status_code = 200
        success.raise_for_status.return_value = None
        success.json.return_value = {"query": {"pages": []}}
        get_mock.side_effect = [limited, success]
        try:
            first = lookup_wikipedia_evidence(
                ["Dune"],
                languages=("en",),
                min_interval=0,
                rate_limit_cooldown=10,
            )
            second = lookup_wikipedia_evidence(
                ["Joker"],
                languages=("en",),
                min_interval=0,
                rate_limit_cooldown=10,
            )
        finally:
            if previous is None:
                delattr(wikipedia, "_RATE_LIMIT_STATE")
            else:
                wikipedia._RATE_LIMIT_STATE = previous

        self.assertEqual(first["status"], "rate_limited")
        self.assertEqual(second["status"], "rate_limited")
        self.assertEqual(get_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
