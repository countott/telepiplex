import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))
sys.modules.setdefault("yaml", types.SimpleNamespace(safe_load=lambda stream: {}))

from app.utils.release_score import rank_releases
from app.utils.search_query import parse_douban_page_title
from app.utils.search_resolution import candidate_to_prowlarr_query, parse_search_intent


class MediaSearchUtilsTest(unittest.TestCase):
    def test_candidate_to_prowlarr_query_preserves_episode_scope(self):
        query = candidate_to_prowlarr_query(
            {
                "media_type": "series",
                "english_title": "Rick and Morty",
                "season_number": 9,
                "episode_number": 7,
                "scope": "episode",
            }
        )
        self.assertEqual(query, "Rick and Morty S09E07")

    def test_candidate_to_prowlarr_query_adds_year_for_whole_series(self):
        query = candidate_to_prowlarr_query(
            {
                "media_type": "series",
                "scope": "whole_series",
                "english_title": "Someday or One Day",
                "year": "2019",
            }
        )

        self.assertEqual(query, "Someday or One Day 2019")

    def test_parse_search_intent_recognizes_chinese_episode(self):
        intent = parse_search_intent("瑞克和莫蒂 第九季第七集")
        self.assertEqual(intent["season_number"], 9)
        self.assertEqual(intent["episode_number"], 7)

    def test_parse_douban_page_title_rejects_site_brand_only(self):
        self.assertEqual(parse_douban_page_title("<html><title>豆瓣</title></html>"), "")

    def test_rank_releases_prefers_usable_quality(self):
        ranked = rank_releases(
            [
                {"title": "Movie CAM", "seeders": 100, "size": 10, "magnet_url": "magnet:?xt=urn:btih:CAM"},
                {
                    "title": "Movie 1080p WEB-DL",
                    "seeders": 2,
                    "size": 10 * 1024**3,
                    "magnet_url": "magnet:?xt=urn:btih:WEB",
                },
            ],
            limit=2,
        )
        self.assertEqual(ranked[0]["title"], "Movie 1080p WEB-DL")


if __name__ == "__main__":
    unittest.main()
