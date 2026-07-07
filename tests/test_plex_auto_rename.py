import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.plex_naming import build_plex_naming_plan, infer_english_title_from_release, parse_episode_marker


class PlexAutoRenameTest(unittest.TestCase):
    def test_build_movie_plan_uses_douban_chinese_and_english_titles(self):
        plan = build_plex_naming_plan(
            {
                "source": "douban",
                "chinese_title": "布达佩斯大饭店",
                "english_title": "The Grand Budapest Hotel",
                "year": "2014",
            },
            "The.Grand.Budapest.Hotel.2014.1080p.BluRay.x265-GROUP",
            "movie.mkv",
        )

        self.assertEqual(plan.target_relative_dir, "布达佩斯大饭店 ◈ The Grand Budapest Hotel")
        self.assertEqual(plan.file_name, "The Grand Budapest Hotel.mkv")

    def test_build_movie_plan_uses_collection_parent_without_suffixes(self):
        plan = build_plex_naming_plan(
            {
                "source": "douban",
                "chinese_title": "碟中谍7：致命清算（上）",
                "english_title": "Mission Impossible Dead Reckoning Part One",
                "collection_chinese_title": "碟中谍系列",
                "collection_english_title": "Mission Impossible Collection",
            },
            "Mission.Impossible.Dead.Reckoning.Part.One.2023.1080p",
            "movie.mkv",
        )

        self.assertEqual(
            plan.target_relative_dir,
            "碟中谍 ◈ Mission Impossible/碟中谍7：致命清算（上） ◈ Mission Impossible Dead Reckoning Part One",
        )
        self.assertEqual(plan.file_name, "Mission Impossible Dead Reckoning Part One.mkv")

    def test_build_episode_plan_formats_sxxexx_from_release_title(self):
        plan = build_plex_naming_plan(
            {
                "source": "douban",
                "chinese_title": "绝命毒师",
                "english_title": "Breaking Bad",
                "year": "2008",
            },
            "Breaking.Bad.1x02.1080p.WEB-DL",
            "episode.mp4",
        )

        self.assertEqual(plan.target_relative_dir, "绝命毒师 ◈ Breaking Bad/Breaking Bad Season 01")
        self.assertEqual(plan.file_name, "Breaking Bad S01E02.mp4")

    def test_build_episode_plan_uses_specials_and_three_digit_episode_width(self):
        special = build_plex_naming_plan(
            {
                "source": "douban",
                "chinese_title": "神秘博士",
                "english_title": "Doctor Who",
            },
            "Doctor.Who.S00E07.Special.1080p",
            "special.mkv",
        )
        long_season = build_plex_naming_plan(
            {
                "source": "douban",
                "chinese_title": "海贼王",
                "english_title": "One Piece",
            },
            "One.Piece.S01E100.1080p",
            "episode.mkv",
        )

        self.assertEqual(special.target_relative_dir, "神秘博士 ◈ Doctor Who/Doctor Who Season 00")
        self.assertEqual(special.file_name, "Doctor Who S00E07.mkv")
        self.assertEqual(long_season.file_name, "One Piece S01E100.mkv")

    def test_parse_episode_marker_supports_common_patterns(self):
        self.assertEqual(parse_episode_marker("Show.S02E03.1080p"), (2, 3))
        self.assertEqual(parse_episode_marker("Show 2x04 WEB-DL"), (2, 4))
        self.assertEqual(parse_episode_marker("剧名 第3季 第5集"), (3, 5))
        self.assertIsNone(parse_episode_marker("Movie 2014 1080p"))

    def test_build_plan_returns_none_without_douban_titles(self):
        self.assertIsNone(
            build_plex_naming_plan(
                {"source": "manual", "chinese_title": "影"},
                "Shadow.2018.1080p",
                "shadow.mkv",
            )
        )

    def test_build_movie_plan_infers_english_title_for_plain_search(self):
        plan = build_plex_naming_plan(
            {
                "source": "search_query",
                "chinese_title": "布达佩斯大饭店",
            },
            "The.Grand.Budapest.Hotel.2014.1080p.BluRay.x265-GROUP",
            "movie.mkv",
        )

        self.assertEqual(plan.target_relative_dir, "布达佩斯大饭店 ◈ The Grand Budapest Hotel")
        self.assertEqual(plan.file_name, "The Grand Budapest Hotel.mkv")

    def test_build_episode_plan_infers_show_title_for_plain_search(self):
        plan = build_plex_naming_plan(
            {
                "source": "search_query",
                "chinese_title": "绝命毒师",
            },
            "Breaking.Bad.S02E03.1080p.WEB-DL.H264-GROUP",
            "episode.mp4",
        )

        self.assertEqual(plan.target_relative_dir, "绝命毒师 ◈ Breaking Bad/Breaking Bad Season 02")
        self.assertEqual(plan.file_name, "Breaking Bad S02E03.mp4")

    def test_build_plan_removes_forbidden_path_symbols(self):
        plan = build_plex_naming_plan(
            {
                "source": "douban",
                "chinese_title": '异形/契约:导演剪辑版',
                "english_title": 'Alien: Covenant "Director Cut"',
            },
            "Alien.Covenant.2017.1080p",
            "movie.mkv",
        )

        self.assertEqual(plan.target_relative_dir, "异形契约导演剪辑版 ◈ Alien Covenant Director Cut")
        self.assertEqual(plan.file_name, "Alien Covenant Director Cut.mkv")

    def test_infer_english_title_keeps_clean_release_title_last_word(self):
        self.assertEqual(
            infer_english_title_from_release("The Grand Budapest Hotel"),
            "The Grand Budapest Hotel",
        )

    def test_infer_english_title_drops_year_parenthesis_cleanly(self):
        self.assertEqual(
            infer_english_title_from_release(
                "Vivre sa Vie.Film en Douze Tableaux (1962).BDRip.1080p.10bit.HEVC.PlamenNik"
            ),
            "Vivre sa Vie Film en Douze Tableaux",
        )


if __name__ == "__main__":
    unittest.main()
