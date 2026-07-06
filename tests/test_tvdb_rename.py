import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.tvdb_rename import build_tvdb_rename_plan


class TvdbRenamePlanTest(unittest.TestCase):
    def test_build_plan_uses_tvdb_season_folder_and_chinese_parent(self):
        plan = build_tvdb_rename_plan(
            final_path="/真人剧集/Release.Name",
            selected_path="/真人剧集",
            metadata={
                "chinese_title": "嗜血法医",
                "english_title": "Dexter",
                "year": "2006",
            },
            ai_plan={
                "tvdb_series_id": "79349",
                "series_name": "Dexter",
                "episode_map": [
                    {
                        "source_file": "Season 1/Dexter.S01E01.mkv",
                        "target_relative_path": "Season 01/Dexter - S01E01 - Dexter.mkv",
                        "tvdb_episode_id": 349232,
                        "season_number": 1,
                        "episode_number": 1,
                    }
                ],
                "warnings": [],
            },
            file_tree=[
                {
                    "name": "Dexter.S01E01.mkv",
                    "relative_path": "Season 1/Dexter.S01E01.mkv",
                    "is_dir": False,
                }
            ],
            tvdb_candidates=[{"tvdb_series_id": "79349", "name": "Dexter", "year": "2006"}],
            tvdb_episodes=[
                {
                    "tvdb_episode_id": 349232,
                    "season_number": 1,
                    "episode_number": 1,
                }
            ],
        )

        self.assertEqual(plan["target_root"], "/真人剧集/嗜血法医 (Dexter)")
        self.assertEqual(
            plan["operations"],
            [
                {
                    "source_relative_path": "Season 1/Dexter.S01E01.mkv",
                    "source_path": "/真人剧集/Release.Name/Season 1/Dexter.S01E01.mkv",
                    "rename_to": "Dexter S01E01.mkv",
                    "renamed_source_path": "/真人剧集/Release.Name/Season 1/Dexter S01E01.mkv",
                    "target_dir": "/真人剧集/嗜血法医 (Dexter)/Dexter Season 01",
                    "target_relative_path": "Dexter Season 01/Dexter S01E01.mkv",
                }
            ],
        )

    def test_build_plan_formats_specials_and_three_digit_episode_width(self):
        plan = build_tvdb_rename_plan(
            final_path="/真人剧集/Release.Name",
            selected_path="/真人剧集",
            metadata={
                "chinese_title": "海贼王",
                "english_title": "One Piece",
            },
            ai_plan={
                "tvdb_series_id": "81797",
                "series_name": "One Piece",
                "episode_map": [
                    {
                        "source_file": "special.mkv",
                        "tvdb_episode_id": 1,
                        "season_number": 0,
                        "episode_number": 7,
                    },
                    {
                        "source_file": "episode-100.mkv",
                        "tvdb_episode_id": 2,
                        "season_number": 1,
                        "episode_number": 100,
                    },
                ],
            },
            file_tree=[
                {"name": "special.mkv", "relative_path": "special.mkv", "is_dir": False},
                {"name": "episode-100.mkv", "relative_path": "episode-100.mkv", "is_dir": False},
            ],
            tvdb_candidates=[{"tvdb_series_id": "81797", "name": "One Piece"}],
            tvdb_episodes=[
                {"tvdb_episode_id": 1, "season_number": 0, "episode_number": 7},
                {"tvdb_episode_id": 2, "season_number": 1, "episode_number": 100},
            ],
        )

        self.assertEqual(plan["operations"][0]["target_relative_path"], "One Piece Season 00/One Piece S00E07.mkv")
        self.assertEqual(plan["operations"][1]["target_relative_path"], "One Piece Season 01/One Piece S01E100.mkv")

    def test_build_plan_rejects_invented_source_file(self):
        plan = build_tvdb_rename_plan(
            final_path="/真人剧集/Release.Name",
            selected_path="/真人剧集",
            metadata={"english_title": "Dexter", "year": "2006"},
            ai_plan={
                "tvdb_series_id": "79349",
                "series_name": "Dexter",
                "episode_map": [{"source_file": "Invented.mkv", "target_name": "Dexter - S01E01.mkv"}],
            },
            file_tree=[
                {"name": "Dexter.S01E01.mkv", "relative_path": "Dexter.S01E01.mkv", "is_dir": False}
            ],
            tvdb_candidates=[{"tvdb_series_id": "79349", "name": "Dexter"}],
            tvdb_episodes=[],
        )

        self.assertIsNone(plan)

    def test_build_plan_rejects_non_numeric_episode_fields_without_raising(self):
        plan = build_tvdb_rename_plan(
            final_path="/真人剧集/Release.Name",
            selected_path="/真人剧集",
            metadata={"english_title": "Dexter", "year": "2006"},
            ai_plan={
                "tvdb_series_id": "79349",
                "series_name": "Dexter",
                "episode_map": [
                    {
                        "source_file": "Dexter.S01E01.mkv",
                        "target_name": "Dexter - S01E01.mkv",
                        "season_number": "one",
                        "episode_number": 1,
                    }
                ],
            },
            file_tree=[
                {"name": "Dexter.S01E01.mkv", "relative_path": "Dexter.S01E01.mkv", "is_dir": False}
            ],
            tvdb_candidates=[{"tvdb_series_id": "79349", "name": "Dexter"}],
            tvdb_episodes=[],
        )

        self.assertIsNone(plan)


if __name__ == "__main__":
    unittest.main()
