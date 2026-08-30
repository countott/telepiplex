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
                    "official_english_title": "The Glory",
                    "year": "2022",
                    "type": "tv",
                    "genres": ["剧情"],
                    "pic": {"large": "https://img.example/glory.jpg"},
                }
            }),
            response(payload={
                "id": "35314632",
                "title": "黑暗荣耀",
                "original_title": "The Glory",
                "official_english_title": "The Glory",
                "year": "2022",
                "type": "tv",
            }),
        ]

        result = douban.lookup_douban_evidence(["黑暗荣耀 2022"])

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["facts"]), 1)
        fact = result["facts"][0]
        self.assertEqual(fact["subject_id"], "35314632")
        self.assertEqual(fact["media_type"], "series")
        self.assertEqual(fact["chinese_title"], "黑暗荣耀")
        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(fact["source_original_title"], "The Glory")
        self.assertEqual(fact["official_english_title"], "")
        self.assertIn("The Glory", fact["aliases"])
        self.assertEqual(fact["year"], "2022")
        self.assertEqual(fact["genres"], ["剧情"])
        self.assertEqual(
            result["source_urls"],
            ["https://movie.douban.com/subject/35314632/"],
        )
        self.assertEqual(get_mock.call_count, 3)

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_subject_abstract_failure_falls_back_to_mobile_json(self, get_mock):
        get_mock.side_effect = [
            response(text='https://movie.douban.com/subject/1295644/'),
            OSError("abstract down"),
            response(payload={
                "id": "1295644",
                "title": "这个杀手不太冷",
                "original_title": "Léon",
                "official_english_title": "Léon",
                "year": "1994",
                "type": "movie",
            }),
        ]

        result = douban.lookup_douban_evidence(["这个杀手不太冷 1994"])

        self.assertEqual(result["status"], "ok")
        fact = result["facts"][0]
        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["official_english_title"], "")
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(fact["source_original_title"], "Léon")
        self.assertIn("Léon", fact["aliases"])

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_subject_lookup_merges_rich_country_poster_and_aliases(self, get_mock):
        get_mock.side_effect = [
            response(payload={
                "subject": {
                    "id": "35981510",
                    "title": "繁花",
                    "year": "2023",
                    "type": "tv",
                }
            }),
            response(payload={
                "id": "35981510",
                "title": "繁花",
                "original_title": "Blossoms Shanghai",
                "official_english_title": "Blossoms Shanghai",
                "year": "2023",
                "type": "tv",
                "countries": ["中国大陆", "中国大陆"],
                "aka": ["繁花(剧版)", "Blossoms Shanghai"],
                "pic": {"large": "https://img.example/blossoms.jpg"},
            }),
        ]

        fact = douban.lookup_douban_subject("35981510", cache_ttl=0)

        self.assertEqual(fact["subject_id"], "35981510")
        self.assertEqual(fact["chinese_title"], "繁花")
        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["official_english_title"], "")
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(fact["source_original_title"], "Blossoms Shanghai")
        self.assertEqual(fact["countries"], ["中国大陆"])
        self.assertEqual(fact["cover_url"], "https://img.example/blossoms.jpg")
        self.assertEqual(
            fact["aliases"],
            ["Blossoms Shanghai", "繁花(剧版)"],
        )
        self.assertEqual(get_mock.call_count, 2)

    @patch("telepiplex_search.adapters.douban.requests.get")
    def test_merged_title_is_reconciled_after_detail_adds_original_title(
        self,
        get_mock,
    ):
        get_mock.side_effect = [
            response(payload={
                "subject": {
                    "id": "30468961",
                    "title": "想见你 想見你",
                    "year": "2019",
                    "type": "tv",
                }
            }),
            response(payload={
                "id": "30468961",
                "title": "想见你 想見你",
                "original_title": "想見你",
                "official_english_title": "Someday or One Day",
                "aka": ["Someday or One Day"],
                "year": "2019",
                "type": "tv",
            }),
        ]

        fact = douban.lookup_douban_subject("30468961", cache_ttl=0)

        self.assertEqual(fact["title"], "想见你")
        self.assertEqual(fact["chinese_title"], "想见你")
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(fact["source_original_title"], "想見你")
        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["official_english_title"], "")
        self.assertIn("Someday or One Day", fact["aliases"])

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
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(fact["source_original_title"], "進撃の巨人")
        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["official_english_title"], "")
        self.assertIn("Attack on Titan", fact["aliases"])
        self.assertEqual(fact["romanized_original_title"], "Shingeki no Kyojin")

    def test_explicit_provider_english_is_noncanonical_alias_only(self):
        fact = douban._normalize_payload(
            {
                "id": "30482958",
                "title": "百年孤独 第一季",
                "original_title": "Cien años de soledad Season 1",
                "original_language": "es",
                "english_title": "100 Years of Solitude",
                "official_english_title": "100 Years of Solitude",
                "year": "2024",
                "type": "tv",
            },
            "https://movie.douban.com/subject/30482958/",
        )

        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["official_english_title"], "")
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(
            fact["source_original_title"],
            "Cien años de soledad Season 1",
        )
        self.assertIn("100 Years of Solitude", fact["aliases"])

    def test_spanish_original_and_alias_do_not_become_english_titles(self):
        fact = douban._normalize_payload(
            {
                "id": "30482958",
                "title": "百年孤独 第一季",
                "original_title": "Cien años de soledad Season 1",
                "original_language": "es",
                "aka": ["Cien años de soledad"],
                "year": "2024",
                "type": "tv",
            },
            "https://movie.douban.com/subject/30482958/",
        )

        self.assertEqual(fact["chinese_title"], "百年孤独")
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(
            fact["source_original_title"],
            "Cien años de soledad Season 1",
        )
        self.assertIn("Cien años de soledad", fact["aliases"])
        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["official_english_title"], "")

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

    def test_series_title_separates_explicit_season_suffix_and_imdb_id(self):
        fact = douban._normalize_payload(
            {
                "id": "5379824",
                "title": "副总统 第一季",
                "original_title": "Veep",
                "year": "2012",
                "type": "tv",
                "info": "IMDb: tt1759761",
            },
            "https://movie.douban.com/subject/5379824/",
        )

        self.assertEqual(fact["douban_title_raw"], "副总统 第一季")
        self.assertEqual(fact["chinese_title"], "副总统")
        self.assertEqual(fact["season_number"], 1)
        self.assertEqual(fact["external_ids"]["imdb"], "tt1759761")

    def test_series_title_cleanup_is_conservative_for_parts_and_sequels(self):
        self.assertEqual(
            douban.clean_douban_series_title("黑暗荣耀 第 2 季", "series"),
            ("黑暗荣耀", 2),
        )
        self.assertEqual(
            douban.clean_douban_series_title("副总统 Season 03", "series"),
            ("副总统", 3),
        )
        self.assertEqual(
            douban.clean_douban_series_title("副总统 S04", "series"),
            ("副总统", 4),
        )
        self.assertEqual(
            douban.clean_douban_series_title("庆余年2", "series"),
            ("庆余年2", None),
        )
        self.assertEqual(
            douban.clean_douban_series_title("百年孤独 第二部", "series"),
            ("百年孤独 第二部", None),
        )
        self.assertEqual(
            douban.clean_douban_series_title("百年孤独 Part 2", "series"),
            ("百年孤独 Part 2", None),
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
        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["official_english_title"], "")
        self.assertIn("Backrooms", fact["aliases"])
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(fact["source_original_title"], "Backrooms")

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
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(
            fact["source_original_title"],
            "ハチミツとクローバー",
        )
        self.assertEqual(fact["english_title"], "")
        self.assertEqual(fact["official_english_title"], "")
        self.assertIn("Honey and Clover", fact["aliases"])

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
        self.assertEqual(fact["original_title"], "")
        self.assertEqual(fact["source_original_title"], "氷菓")

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
        self.assertEqual(get_mock.call_count, 3)

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
