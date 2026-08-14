import unittest
from unittest.mock import Mock, patch

import requests

from telepiplex_search.adapters.wikipedia import (
    _classification,
    lookup_wikipedia_evidence,
    lookup_wikipedia_episode_page,
    lookup_wikipedia_page,
)


class WikipediaAdapterTest(unittest.TestCase):
    @patch("telepiplex_search.adapters.wikipedia.requests.get")
    def test_episode_inventory_uses_one_exact_action_parse_request(self, get_mock):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "parse": {
                "title": "One Hundred Years of Solitude (TV series)",
                "pageid": 123,
                "revid": 1367933110,
                "displaytitle": "One Hundred Years of Solitude",
                "text": (
                    '<h3>Season 2 (2026)</h3>'
                    '<table class="wikiepisodetable">'
                    '<tr><th>No. overall</th><th>No. in season</th>'
                    '<th>Original release date</th></tr>'
                    '<tr class="module-episode-list-row">'
                    '<th id="ep9">9</th><td>1</td>'
                    '<td><span class="bday">2026-08-05</span></td>'
                    '</tr></table>'
                ),
            }
        }
        get_mock.return_value = response

        result = lookup_wikipedia_episode_page(
            "en",
            "One Hundred Years of Solitude (TV series)",
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["revision_id"], 1367933110)
        self.assertEqual(result["items"][0]["season_number"], 2)
        params = get_mock.call_args.kwargs["params"]
        self.assertEqual(params["action"], "parse")
        self.assertEqual(
            params["page"],
            "One Hundred Years of Solitude (TV series)",
        )
        self.assertEqual(params["prop"], "text|revid|displaytitle")
        self.assertNotIn("generator", params)
        self.assertEqual(get_mock.call_count, 1)

    @patch("telepiplex_search.adapters.wikipedia.requests.get")
    def test_preserves_mediawiki_rank_and_marks_disambiguation(self, get_mock):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "query": {
                "pages": [
                    {
                        "pageid": 20,
                        "index": 2,
                        "title": "副总统 (消歧义)",
                        "extract": "副总统可以指不同作品。",
                        "pageprops": {
                            "wikibase_item": "Q117437549",
                            "disambiguation": "",
                        },
                    },
                    {
                        "pageid": 10,
                        "index": 1,
                        "title": "副人之仁",
                        "extract": "2012年开播美国电视剧。",
                        "pageprops": {"wikibase_item": "Q74801"},
                    },
                ]
            }
        }
        get_mock.return_value = response

        result = lookup_wikipedia_evidence(
            ["副总统 电视剧"], languages=("zh",)
        )

        self.assertEqual(
            [fact["wikibase_item"] for fact in result["facts"]],
            ["Q74801", "Q117437549"],
        )
        self.assertEqual(result["facts"][0]["search_rank"], 1)
        self.assertEqual(result["facts"][0]["page_id"], 10)
        self.assertFalse(result["facts"][0]["is_disambiguation"])
        self.assertTrue(result["facts"][1]["is_disambiguation"])

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
                        "varianttitles": {
                            "zh-cn": "想见你 (电影)",
                            "zh-tw": "想見你 (電影)",
                        },
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
        self.assertEqual(result["facts"][0]["title"], "想见你 (电影)")
        self.assertEqual(
            result["facts"][0]["canonical_title"],
            "想見你 (電影)",
        )
        self.assertEqual(
            result["facts"][0]["chinese_title"],
            "想见你 (电影)",
        )
        self.assertIn("續篇電影", result["facts"][0]["extract"])
        self.assertEqual(result["facts"][0]["year"], "2022")
        self.assertEqual(result["facts"][0]["media_type"], "movie")
        self.assertEqual(result["facts"][1]["language"], "en")
        zh_params = get_mock.call_args_list[0].kwargs["params"]
        self.assertEqual(zh_params["variant"], "zh-cn")
        self.assertEqual(zh_params["converttitles"], 1)
        self.assertIn("varianttitles", zh_params["inprop"])
        self.assertEqual(
            result["source_urls"],
            [
                "https://zh.wikipedia.org/wiki/想見你_(電影)",
                "https://en.wikipedia.org/wiki/Someday_or_One_Day",
            ],
        )

    @patch("telepiplex_search.adapters.wikipedia.requests.get")
    def test_zh_page_uses_same_entity_english_langlink_as_official_title(
        self,
        get_mock,
    ):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "query": {
                "pages": [{
                    "title": "后室 (电影)",
                    "extract": "《后室》是一部2026年美国恐怖电影。",
                    "pageprops": {"wikibase_item": "Q125131076"},
                    "fullurl": "https://zh.wikipedia.org/wiki/后室_(电影)",
                    "langlinks": [{
                        "lang": "en",
                        "title": "Backrooms (film)",
                    }],
                }]
            }
        }
        get_mock.return_value = response

        result = lookup_wikipedia_evidence(
            ["后室 电影 2026"],
            languages=("zh",),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["facts"][0]["wikibase_item"],
            "Q125131076",
        )
        self.assertEqual(
            result["facts"][0]["official_english_title"],
            "Backrooms",
        )
        params = get_mock.call_args.kwargs["params"]
        self.assertIn("langlinks", params["prop"])
        self.assertEqual(params["lllang"], "en")
        self.assertEqual(params["lllimit"], 1)

    @patch("telepiplex_search.adapters.wikipedia.requests.get")
    def test_exact_zh_page_exposes_same_entity_english_work_title(
        self,
        get_mock,
    ):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "query": {
                "pages": [{
                    "title": "后室 (电影)",
                    "extract": "《后室》是一部2026年美国恐怖电影。",
                    "pageprops": {"wikibase_item": "Q125131076"},
                    "fullurl": "https://zh.wikipedia.org/wiki/后室_(电影)",
                    "langlinks": [{
                        "lang": "en",
                        "title": "Backrooms (2026 film)",
                    }],
                }]
            }
        }
        get_mock.return_value = response

        fact = lookup_wikipedia_page("zh", "后室 (电影)")

        self.assertEqual(fact["official_english_title"], "Backrooms")
        self.assertEqual(fact["english_title"], "Backrooms")

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
