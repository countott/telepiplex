import unittest
from unittest.mock import Mock, patch

import requests

from telepiplex_search.adapters import douban
from telepiplex_search.service import SearchFeature


def response(*, text="", payload=None):
    item = Mock()
    item.text = text
    item.raise_for_status.return_value = None
    item.json.return_value = payload or {}
    return item


class DoubanAdapterTest(unittest.TestCase):
    def setUp(self):
        douban._QUERY_CACHE.clear()
        douban._SUBJECT_CACHE.clear()
        douban._CIRCUIT_STATE.update({
            "failures": 0,
            "open_until": 0.0,
        })

    def test_empty_expanded_queries_are_unavailable_not_not_found(self):
        result = douban.lookup_douban_evidence([])

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["error"], "source_queries_empty")

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_lookup_returns_normalized_subject_fact(self, get_mock):
        get_mock.side_effect = [
            response(
                text=(
                    '<a href="https://movie.douban.com/subject/35314632/">A</a>'
                    '<a href="https://movie.douban.com/subject/35314632/">B</a>'
                )
            ),
            response(payload={
                "subject": {
                    "id": "35314632",
                    "title": "黑暗荣耀",
                    "original_title": "The Glory",
                    "year": "2022",
                    "type": "tv",
                    "genres": ["剧情"],
                    "pic": {"large": "https://img.example/glory.jpg"},
                }
            }),
        ]

        result = douban.lookup_douban_evidence(["黑暗荣耀 2022"])

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["facts"]), 1)
        fact = result["facts"][0]
        self.assertEqual(fact["subject_id"], "35314632")
        self.assertEqual(fact["media_type"], "series")
        self.assertEqual(fact["chinese_title"], "黑暗荣耀")
        self.assertEqual(fact["english_title"], "The Glory")
        self.assertEqual(fact["original_title"], "The Glory")
        self.assertEqual(fact["official_english_title"], "The Glory")
        self.assertEqual(fact["year"], "2022")
        self.assertEqual(fact["genres"], ["剧情"])
        self.assertEqual(
            result["source_urls"],
            ["https://movie.douban.com/subject/35314632/"],
        )
        self.assertEqual(get_mock.call_count, 2)

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_subject_abstract_failure_falls_back_to_mobile_json(self, get_mock):
        get_mock.side_effect = [
            response(text='https://movie.douban.com/subject/1295644/'),
            OSError("abstract down"),
            response(payload={
                "id": "1295644",
                "title": "这个杀手不太冷",
                "original_title": "Léon",
                "year": "1994",
                "type": "movie",
            }),
        ]

        result = douban.lookup_douban_evidence(["这个杀手不太冷 1994"])

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["facts"][0]["english_title"], "Léon")

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_japanese_language_and_romaji_are_preserved_without_translation(self, get_mock):
        get_mock.side_effect = [
            response(text='https://movie.douban.com/subject/1/'),
            response(payload={
                "id": "1",
                "title": "进击的巨人",
                "original_title": "進撃の巨人",
                "original_language": "ja",
                "official_english_title": "Attack on Titan",
                "romanized_original_title": "Shingeki no Kyojin",
                "year": "2013",
                "type": "tv",
            }),
        ]

        fact = douban.lookup_douban_evidence(["进击的巨人"])["facts"][0]

        self.assertEqual(fact["original_language"], "ja")
        self.assertEqual(fact["original_title"], "進撃の巨人")
        self.assertEqual(fact["official_english_title"], "Attack on Titan")
        self.assertEqual(fact["romanized_original_title"], "Shingeki no Kyojin")

    def test_normalize_payload_splits_trailing_year_and_removes_format_controls(self):
        fact = douban._normalize_payload(
            {
                "id": "10001418",
                "title": "冰果 氷菓\u200e (2012)",
                "aka": ["冰果\u200e (2012)"],
                "type": "tv",
            },
            "https://movie.douban.com/subject/10001418/",
        )

        self.assertIsNotNone(fact)
        self.assertEqual(fact["title"], "冰果 氷菓")
        self.assertEqual(fact["chinese_title"], "冰果 氷菓")
        self.assertEqual(fact["year"], "2012")
        self.assertNotIn("(2012)", fact["title"])
        self.assertTrue(
            all("\u200e" not in value and "(2012)" not in value for value in fact["aliases"])
        )

    def test_mixed_chinese_and_english_title_does_not_pollute_chinese_title(self):
        fact = douban._normalize_payload(
            {
                "id": "1",
                "title": "后室 Backrooms",
                "original_title": "Backrooms",
                "original_language": "en",
                "official_english_title": "Backrooms",
                "year": "2022",
                "type": "movie",
            },
            "https://movie.douban.com/subject/1/",
        )

        self.assertEqual(fact["chinese_title"], "后室")
        self.assertEqual(fact["official_english_title"], "Backrooms")
        self.assertEqual(fact["original_title"], "Backrooms")

    def test_mixed_chinese_and_japanese_title_does_not_pollute_chinese_title(self):
        fact = douban._normalize_payload(
            {
                "id": "2",
                "title": "蜂蜜与四叶草 ハチミツとクローバー",
                "original_title": "ハチミツとクローバー",
                "original_language": "ja",
                "official_english_title": "Honey and Clover",
                "year": "2005",
                "type": "tv",
            },
            "https://movie.douban.com/subject/2/",
        )

        self.assertEqual(fact["chinese_title"], "蜂蜜与四叶草")
        self.assertEqual(fact["original_title"], "ハチミツとクローバー")
        self.assertEqual(fact["official_english_title"], "Honey and Clover")

    def test_original_title_suffix_is_removed_even_when_both_titles_use_han(self):
        fact = douban._normalize_payload(
            {
                "id": "3",
                "title": "冰果 氷菓",
                "original_title": "氷菓",
                "original_language": "ja",
                "official_english_title": "Hyouka",
                "year": "2012",
                "type": "tv",
            },
            "https://movie.douban.com/subject/3/",
        )

        self.assertEqual(fact["chinese_title"], "冰果")
        self.assertEqual(fact["original_title"], "氷菓")

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_successful_empty_search_is_not_found(self, get_mock):
        get_mock.return_value = response(text="<html>没有影视条目</html>")

        result = douban.lookup_douban_evidence(["不存在的条目"])

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["facts"], [])

    @patch(
        "telepiplex_search.adapters.douban.requests.get",
        side_effect=OSError("dns failed"),
    )
    def test_total_network_failure_is_server_down(self, _get_mock):
        result = douban.lookup_douban_evidence(["任意条目"])

        self.assertEqual(result["status"], "server_down")
        self.assertEqual(result["facts"], [])
        self.assertIn("dns failed", result["error"])

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_rate_limit_opens_short_circuit(self, get_mock):
        rejected = response()
        rejected.status_code = 429
        rejected.raise_for_status.side_effect = requests.HTTPError(
            "rate limited",
            response=rejected,
        )
        get_mock.return_value = rejected

        first = douban.lookup_douban_evidence(
            ["蝙蝠侠"],
            circuit_breaker_failures=1,
            circuit_breaker_seconds=60,
        )
        second = douban.lookup_douban_evidence(
            ["蝙蝠侠"],
            circuit_breaker_failures=1,
            circuit_breaker_seconds=60,
        )

        self.assertEqual(first["status"], "rate_limited")
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(get_mock.call_count, 1)

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_forbidden_is_reported_as_blocked(self, get_mock):
        rejected = response()
        rejected.status_code = 403
        rejected.raise_for_status.side_effect = requests.HTTPError(
            "forbidden",
            response=rejected,
        )
        get_mock.return_value = rejected

        result = douban.lookup_douban_evidence(["蝙蝠侠"])

        self.assertEqual(result["status"], "blocked")

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_query_cache_avoids_duplicate_search_and_subject_requests(
        self,
        get_mock,
    ):
        get_mock.side_effect = [
            response(text="https://movie.douban.com/subject/1295644/"),
            response(payload={
                "id": "1295644",
                "title": "这个杀手不太冷",
                "original_title": "Léon",
                "year": "1994",
                "type": "movie",
            }),
        ]

        first = douban.lookup_douban_evidence(
            ["这个杀手不太冷"],
            cache_ttl=900,
        )
        second = douban.lookup_douban_evidence(
            ["这个杀手不太冷"],
            cache_ttl=900,
        )

        self.assertEqual(first, second)
        self.assertEqual(get_mock.call_count, 2)

    @patch("telepiplex_search.service.lookup_douban_evidence", create=True)
    def test_feature_provider_uses_rule_queries(self, lookup_mock):
        lookup_mock.return_value = {
            "source": "douban",
            "status": "not_found",
            "facts": [],
            "source_urls": [],
            "error": "",
        }
        feature = SearchFeature(config={}, host=Mock())

        result = feature._douban_provider({
            "source_queries": {"douban": ["黑暗荣耀 2022"]}
        })

        self.assertEqual(result["status"], "not_found")
        lookup_mock.assert_called_once_with(
            ["黑暗荣耀 2022"],
            timeout=10.0,
            cache_ttl=900.0,
            max_concurrency=2,
            circuit_breaker_failures=3,
            circuit_breaker_seconds=300.0,
        )


if __name__ == "__main__":
    unittest.main()
