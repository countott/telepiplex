import unittest


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
        self.assertIn("2021｜地区未知｜电影｜电影", result["text"])
        self.assertIn("来源：豆瓣", result["text"])


if __name__ == "__main__":
    unittest.main()
