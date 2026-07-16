import unittest
from unittest.mock import Mock, patch

from telepiplex_media_search.adapters.douban import lookup_douban_evidence
from telepiplex_media_search.service import MediaSearchFeature


def response(*, text="", payload=None):
    item = Mock()
    item.text = text
    item.raise_for_status.return_value = None
    item.json.return_value = payload or {}
    return item


class DoubanAdapterTest(unittest.TestCase):
    @patch("telepiplex_media_search.adapters.douban.requests.get")
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

        result = lookup_douban_evidence(["黑暗荣耀 2022"])

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

    @patch("telepiplex_media_search.adapters.douban.requests.get")
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

        result = lookup_douban_evidence(["这个杀手不太冷 1994"])

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["facts"][0]["english_title"], "Léon")

    @patch("telepiplex_media_search.adapters.douban.requests.get")
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

        fact = lookup_douban_evidence(["进击的巨人"])["facts"][0]

        self.assertEqual(fact["original_language"], "ja")
        self.assertEqual(fact["original_title"], "進撃の巨人")
        self.assertEqual(fact["official_english_title"], "Attack on Titan")
        self.assertEqual(fact["romanized_original_title"], "Shingeki no Kyojin")

    @patch("telepiplex_media_search.adapters.douban.requests.get")
    def test_successful_empty_search_is_not_found(self, get_mock):
        get_mock.return_value = response(text="<html>没有影视条目</html>")

        result = lookup_douban_evidence(["不存在的条目"])

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["facts"], [])

    @patch(
        "telepiplex_media_search.adapters.douban.requests.get",
        side_effect=OSError("dns failed"),
    )
    def test_total_network_failure_is_server_down(self, _get_mock):
        result = lookup_douban_evidence(["任意条目"])

        self.assertEqual(result["status"], "server_down")
        self.assertEqual(result["facts"], [])
        self.assertIn("dns failed", result["error"])

    @patch("telepiplex_media_search.service.lookup_douban_evidence", create=True)
    def test_feature_provider_uses_rule_queries(self, lookup_mock):
        lookup_mock.return_value = {
            "source": "douban",
            "status": "not_found",
            "facts": [],
            "source_urls": [],
            "error": "",
        }
        feature = MediaSearchFeature(config={}, core=Mock())

        result = feature._douban_provider({
            "source_queries": {"douban": ["黑暗荣耀 2022"]}
        })

        self.assertEqual(result["status"], "not_found")
        lookup_mock.assert_called_once_with(["黑暗荣耀 2022"], timeout=10.0)


if __name__ == "__main__":
    unittest.main()
