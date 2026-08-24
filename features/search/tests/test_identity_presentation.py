import unittest
from copy import deepcopy


class IdentityPresentationTest(unittest.TestCase):
    def test_builds_stable_standalone_series_identity_message(self):
        from telepiplex_search.identity_presentation import (
            build_identity_presentation,
        )

        contract = {
            "identity": {
                "chinese_title": "繁花",
                "official_english_title": "Blossoms Shanghai",
                "year": "2023",
                "countries": ["中国大陆"],
                "content_kind": "series",
                "poster_url": "https://img.example/blossoms.jpg",
                "external_ids": {"douban_subject": "35981510"},
            },
            "retrieval": {
                "media_type": "series",
                "scope": "whole_series",
            },
            "placement": {
                "library_type": "series",
                "season_number": None,
                "episode_number": None,
            },
            "evidence": {
                "source_links": [
                    {"provider": "douban"},
                    {"provider": "tvdb"},
                ],
            },
        }

        first = build_identity_presentation(contract)
        second = build_identity_presentation(contract)

        self.assertEqual(first, second)
        self.assertEqual(first["title"], "繁花 (Blossoms Shanghai)")
        self.assertEqual(first["photo_url"], "https://img.example/blossoms.jpg")
        self.assertEqual(
            first["text"],
            "🎬 繁花 (Blossoms Shanghai)\n"
            "2023｜中国大陆｜剧集｜全剧\n"
            "来源：豆瓣、TVDB",
        )
        self.assertRegex(first["milestone_id"], r"^media-[0-9a-f]{24}$")

    def test_title_and_scope_have_safe_fallbacks(self):
        from telepiplex_search.identity_presentation import (
            build_identity_presentation,
        )

        result = build_identity_presentation({
            "identity": {
                "english_title": "Dune",
                "year": "2021",
                "content_kind": "movie",
                "external_ids": {"douban_subject": "3001114"},
            },
            "retrieval": {"media_type": "movie", "scope": "work"},
            "placement": {"library_type": "movie"},
            "source_entry": {"provider": "douban"},
        })

        self.assertEqual(result["title"], "Dune")
        self.assertEqual(result["title_status"], "latin_fallback")
        self.assertIn("2021｜地区未知｜电影｜电影", result["text"])
        self.assertIn("来源：豆瓣", result["text"])

    def test_verified_chinese_title_is_marked_separately_from_display_fallback(self):
        from telepiplex_search.identity_presentation import (
            build_identity_presentation,
        )

        result = build_identity_presentation({
            "identity": {
                "chinese_title": "蜂蜜与四叶草",
                "english_title": "Honey and Clover",
                "year": "2005",
                "content_kind": "series",
            },
            "retrieval": {"media_type": "series", "scope": "whole_series"},
            "placement": {"library_type": "series"},
        })

        self.assertEqual(result["title"], "蜂蜜与四叶草 (Honey and Clover)")
        self.assertEqual(result["title_status"], "verified_chinese")

    def test_scope_uses_confirmed_retrieval_and_evidence_decision(self):
        from telepiplex_search.identity_presentation import (
            build_identity_presentation,
        )

        whole_series = {
            "identity": {
                "chinese_title": "示例剧集",
                "year": "2026",
                "content_kind": "series",
                "external_ids": {"tvdb": "123"},
            },
            "retrieval": {"media_type": "series", "scope": "whole_series"},
            "placement": {"library_type": "series"},
            "evidence": {"decision": {}},
        }
        season = deepcopy(whole_series)
        season["retrieval"]["scope"] = "season"
        season["placement"] = {"library_type": "series"}
        season["evidence"]["decision"] = {"season_number": 5}
        episode = deepcopy(whole_series)
        episode["retrieval"]["scope"] = "episode"
        episode["placement"] = {
            "library_type": "series",
            "season_number": 1,
            "episode_number": 1,
        }
        episode["evidence"]["decision"] = {
            "season_number": 5,
            "episode_number": 3,
        }

        whole_result = build_identity_presentation(whole_series)
        season_result = build_identity_presentation(season)
        episode_result = build_identity_presentation(episode)

        self.assertIn("｜全剧", whole_result["text"])
        self.assertIn("｜第 5 季", season_result["text"])
        self.assertIn("｜S05E03", episode_result["text"])
        self.assertNotEqual(season_result["milestone_id"], whole_result["milestone_id"])
        self.assertNotEqual(season_result["milestone_id"], episode_result["milestone_id"])
        self.assertNotEqual(episode_result["milestone_id"], whole_result["milestone_id"])

    def test_bounded_scope_uses_legacy_placement_only_for_missing_decision_coordinate(self):
        from telepiplex_search.identity_presentation import (
            build_identity_presentation,
        )

        result = build_identity_presentation({
            "identity": {"chinese_title": "示例剧集", "content_kind": "series"},
            "retrieval": {"media_type": "series", "scope": "season"},
            "placement": {"library_type": "series", "season_number": 5},
            "evidence": {"decision": {}},
        })

        self.assertIn("｜第 5 季", result["text"])

    def test_episode_uses_legacy_placement_for_missing_episode_coordinate(self):
        from telepiplex_search.identity_presentation import (
            build_identity_presentation,
        )

        result = build_identity_presentation({
            "identity": {"chinese_title": "示例剧集", "content_kind": "series"},
            "retrieval": {"media_type": "series", "scope": "episode"},
            "placement": {
                "library_type": "series",
                "season_number": 1,
                "episode_number": 3,
            },
            "evidence": {"decision": {"season_number": 5}},
        })

        self.assertIn("｜S05E03", result["text"])

    def test_invalid_present_decision_coordinate_never_uses_stale_placement(self):
        from telepiplex_search.identity_presentation import (
            build_identity_presentation,
        )

        invalid_season = build_identity_presentation({
            "identity": {"chinese_title": "示例剧集", "content_kind": "series"},
            "retrieval": {"media_type": "series", "scope": "season"},
            "placement": {"library_type": "series", "season_number": 1},
            "evidence": {"decision": {"season_number": "invalid"}},
        })
        invalid_episode = build_identity_presentation({
            "identity": {"chinese_title": "示例剧集", "content_kind": "series"},
            "retrieval": {"media_type": "series", "scope": "episode"},
            "placement": {
                "library_type": "series",
                "season_number": 1,
                "episode_number": 1,
            },
            "evidence": {
                "decision": {"season_number": 5, "episode_number": "invalid"},
            },
        })

        self.assertIn("｜全剧", invalid_season["text"])
        self.assertNotIn("｜第 1 季", invalid_season["text"])
        self.assertIn("｜全剧", invalid_episode["text"])
        self.assertNotIn("｜S05E01", invalid_episode["text"])

    def test_whole_series_ignores_placement_coordinates(self):
        from telepiplex_search.identity_presentation import (
            build_identity_presentation,
        )

        result = build_identity_presentation({
            "identity": {"chinese_title": "示例剧集", "content_kind": "series"},
            "retrieval": {"media_type": "series", "scope": "whole_series"},
            "placement": {
                "library_type": "series",
                "season_number": 5,
                "episode_number": 3,
            },
            "evidence": {"decision": {}},
        })

        self.assertIn("｜全剧", result["text"])
        self.assertNotIn("S05E03", result["text"])


if __name__ == "__main__":
    unittest.main()
