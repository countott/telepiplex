import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from telepiplex_search.release_score import (
    rank_releases,
    score_release_details,
)
from telepiplex_search.context import runtime_context
from telepiplex_search.prowlarr_query import build_prowlarr_query
from telepiplex_search.search_query import parse_douban_page_title
from telepiplex_search.search_resolution import candidate_to_prowlarr_query, parse_search_intent


class SearchUtilsTest(unittest.TestCase):
    def test_canonical_queries_are_minimal(self):
        self.assertEqual(
            build_prowlarr_query("Kill Bill Vol. 1", "movie"),
            "Kill Bill Vol 1",
        )
        self.assertEqual(
            build_prowlarr_query("The Office US", "whole_series"),
            "The Office US",
        )
        self.assertEqual(
            build_prowlarr_query(
                "The Office US",
                "season",
                season_number=1,
            ),
            "The Office US S01",
        )
        self.assertEqual(
            build_prowlarr_query(
                "The Office US",
                "episode",
                season_number=1,
                episode_number=2,
            ),
            "The Office US S01E02",
        )

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

    def test_candidate_to_prowlarr_query_omits_year_for_whole_series(self):
        query = candidate_to_prowlarr_query(
            {
                "media_type": "series",
                "scope": "whole_series",
                "english_title": "Someday or One Day",
                "year": "2019",
            }
        )

        self.assertEqual(query, "Someday or One Day")

    def test_parse_search_intent_recognizes_chinese_episode(self):
        intent = parse_search_intent("瑞克和莫蒂 第九季第七集")
        self.assertEqual(intent["title"], "瑞克和莫蒂")
        self.assertEqual(intent["scope"], "episode")
        self.assertEqual(intent["season_number"], 9)
        self.assertEqual(intent["episode_number"], 7)

    def test_parse_search_intent_keeps_base_title_for_english_episode(self):
        intent = parse_search_intent("Rick and Morty S09E08")

        self.assertEqual(intent["title"], "Rick and Morty")
        self.assertEqual(intent["scope"], "episode")
        self.assertEqual(intent["season_number"], 9)
        self.assertEqual(intent["episode_number"], 8)

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

    def test_ranked_release_keeps_weighted_score_details(self):
        original = runtime_context.config
        runtime_context.config = {
            "search": {
                "scoring": {
                    "indexer_scores": {"M-Team": 30},
                },
            },
        }
        try:
            item = {
                "title": "Title.2160p.WEB-DL",
                "magnet_url": "magnet:?x",
                "indexer": "M-Team",
                "seeders": 20,
                "size": 50 * 1024 ** 3,
            }
            ranked = rank_releases([item], 12)
            recomputed = score_release_details(item)
        finally:
            runtime_context.config = original

        details = ranked[0]["score_details"]
        self.assertIn(
            {"kind": "keyword", "label": "2160p", "score": 35},
            details,
        )
        self.assertIn(
            {"kind": "indexer", "label": "M-Team", "score": 30},
            details,
        )
        self.assertTrue(any(
            item["kind"] == "seeders" for item in details
        ))
        self.assertTrue(any(item["kind"] == "size" for item in details))
        self.assertEqual(
            ranked[0]["score"],
            sum(item["score"] for item in details),
        )
        self.assertEqual(
            recomputed,
            (ranked[0]["score"], details),
        )

if __name__ == "__main__":
    unittest.main()
