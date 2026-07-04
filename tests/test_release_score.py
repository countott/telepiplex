import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.utils.release_score import rank_releases, score_release


class ReleaseScoreTest(unittest.TestCase):
    def setUp(self):
        init.bot_config = {
            "search": {
                "scoring": {
                    "prefer_resolution": ["2160p", "1080p"],
                    "prefer_source": ["WEB-DL", "BluRay", "Remux"],
                    "prefer_codec": ["HEVC", "H.265", "x265", "H.264", "x264"],
                    "prefer_audio": ["Atmos", "TrueHD", "DTS-HD", "EAC3"],
                    "reject_keywords": ["CAM", "TS", "TC", "枪版", "抢先", "HC", "HDTC", "HDCAM"],
                }
            }
        }

    def test_score_release_rewards_quality_terms_and_seeders(self):
        score, features = score_release(
            {
                "title": "The Grand Budapest Hotel 2014 2160p WEB-DL HEVC Atmos",
                "seeders": 42,
                "size": 15 * 1024**3,
            }
        )

        self.assertGreaterEqual(score, 100)
        self.assertEqual(features, ["2160p", "WEB-DL", "HEVC", "Atmos"])

    def test_score_release_penalizes_reject_keywords_low_seeders_and_small_size(self):
        score, features = score_release(
            {
                "title": "The Grand Budapest Hotel 2014 HDCAM HC 1080p",
                "seeders": 0,
                "size": 300 * 1024**2,
            }
        )

        self.assertLess(score, 0)
        self.assertIn("HDCAM", features)
        self.assertIn("HC", features)

    def test_rank_releases_sorts_by_score_and_filters_unselectable_items(self):
        items = [
            {"title": "low quality CAM", "seeders": 0, "size": 200 * 1024**2, "download_url": "https://example/1"},
            {"title": "1080p BluRay x264 DTS-HD", "seeders": 20, "size": 8 * 1024**3, "magnet_url": "magnet:?xt=urn:btih:ABC"},
            {"title": "missing link 2160p Remux", "seeders": 99, "size": 40 * 1024**3},
        ]

        ranked = rank_releases(items, limit=8)

        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["title"], "1080p BluRay x264 DTS-HD")
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])
        self.assertEqual(ranked[0]["features"], ["1080p", "BluRay", "x264", "DTS-HD"])


if __name__ == "__main__":
    unittest.main()
