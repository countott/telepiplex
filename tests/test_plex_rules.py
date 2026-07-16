import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class PlexRuleTest(unittest.TestCase):
    def test_external_ids_match_normalizes_plex_guids(self):
        from telepiplex_plex.rules import external_ids_match

        self.assertTrue(
            external_ids_match(
                {"imdb": "tt0245429", "tmdb": "129"},
                ["imdb://tt0245429", "tmdb://129"],
            )
        )
        self.assertFalse(
            external_ids_match(
                {"tmdb": "129"},
                ["tmdb://999"],
            )
        )

    def test_choose_exact_match_requires_one_unique_candidate(self):
        from telepiplex_plex.rules import choose_exact_match

        candidates = [
            {"rating_key": "1", "guids": ["tmdb://10"]},
            {"rating_key": "2", "guids": ["tmdb://20"]},
        ]

        self.assertEqual(
            choose_exact_match({"tmdb": "20"}, candidates)["rating_key"],
            "2",
        )
        self.assertIsNone(
            choose_exact_match(
                {"tmdb": "20"},
                candidates + [{"rating_key": "3", "guids": ["tmdb://20"]}],
            )
        )

    def test_choose_textless_poster_prefers_ranked_tmdb_then_fanart(self):
        from telepiplex_plex.rules import (
            choose_textless_poster,
            rank_textless_posters,
        )

        tmdb = [
            {"file_path": "/text.jpg", "iso_639_1": "en", "vote_count": 100},
            {"file_path": "/low.jpg", "iso_639_1": None, "vote_count": 2, "vote_average": 9, "width": 1000, "height": 1500},
            {"file_path": "/best.jpg", "url": "https://top", "iso_639_1": None, "vote_count": 8, "vote_average": 8, "width": 1000, "height": 1500},
        ]
        fanart = [{"url": "https://fanart/best.jpg", "lang": "00", "likes": "99"}]

        selected = choose_textless_poster(tmdb, fanart)

        self.assertEqual(selected["source"], "tmdb")
        self.assertEqual(selected["file_path"], "/best.jpg")
        self.assertEqual(selected["url"], "https://top")
        self.assertEqual(
            [item["source"] for item in rank_textless_posters(tmdb, fanart)],
            ["tmdb", "tmdb", "fanart"],
        )
        self.assertEqual(
            choose_textless_poster([], fanart)["source"],
            "fanart",
        )

    def test_choose_textless_poster_requires_unique_business_score(self):
        from telepiplex_plex.rules import choose_textless_poster

        tied = [
            {
                "url": "https://first",
                "iso_639_1": None,
                "vote_count": 8,
                "vote_average": 8,
                "width": 1000,
                "height": 1500,
            },
            {
                "url": "https://second",
                "iso_639_1": None,
                "vote_count": 8,
                "vote_average": 8,
                "width": 1000,
                "height": 1500,
            },
        ]

        self.assertIsNone(choose_textless_poster(tied, []))

    def test_audio_prefers_original_language_lossless_track(self):
        from telepiplex_plex.rules import (
            choose_original_audio,
            rank_original_audio,
        )

        streams = [
            {"id": 1, "language_code": "jpn", "codec": "eac3", "channels": 8, "bitrate": 1536},
            {"id": 2, "language_code": "jpn", "codec": "truehd", "channels": 6, "bitrate": 4000},
            {"id": 3, "language_code": "eng", "codec": "truehd", "channels": 8, "bitrate": 5000},
        ]

        selected = choose_original_audio(streams, "ja")

        self.assertEqual(selected["id"], 2)
        self.assertEqual(
            [item["id"] for item in rank_original_audio(streams, "ja")],
            [2, 1],
        )

    def test_audio_keeps_current_state_when_best_tracks_are_tied(self):
        from telepiplex_plex.rules import (
            choose_original_audio,
            rank_original_audio,
        )

        streams = [
            {"id": 1, "language_code": "eng", "codec": "truehd", "channels": 8, "bitrate": 4000},
            {"id": 2, "language_code": "eng", "codec": "truehd", "channels": 8, "bitrate": 4000},
        ]

        self.assertEqual(
            [item["id"] for item in rank_original_audio(streams, "en")],
            [1, 2],
        )
        self.assertIsNone(choose_original_audio(streams, "en"))

    def test_subtitle_prefers_external_chi_then_embedded_chi(self):
        from telepiplex_plex.rules import (
            choose_chi_subtitle,
            rank_chi_subtitles,
        )

        streams = [
            {"id": 1, "language_code": "chi", "external": False},
            {"id": 2, "language_code": "chi", "external": True, "transient": False},
            {"id": 3, "language_code": "eng", "external": True, "transient": False},
        ]

        self.assertEqual(choose_chi_subtitle(streams)["id"], 2)
        self.assertEqual(choose_chi_subtitle(streams[:1])["id"], 1)
        self.assertIsNone(choose_chi_subtitle(streams[2:]))
        self.assertEqual(
            [item["id"] for item in rank_chi_subtitles(streams)],
            [2, 1],
        )

    def test_chinese_subtitle_requires_unique_best_tier(self):
        from telepiplex_plex.rules import choose_chi_subtitle

        streams = [
            {
                "id": 31,
                "language_code": "chi",
                "external": True,
                "transient": False,
            },
            {
                "id": 32,
                "language_code": "chi",
                "external": True,
                "transient": False,
            },
        ]

        self.assertIsNone(choose_chi_subtitle(streams))


if __name__ == "__main__":
    unittest.main()
