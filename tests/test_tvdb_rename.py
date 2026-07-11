import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.tvdb_rename import (
    build_confirmed_rename_plan,
    build_tvdb_rename_plan,
    enrich_media_metadata_with_rename_plan,
)


class TvdbRenamePlanTest(unittest.TestCase):
    def _confirmed_media_metadata(self):
        return {
            "schema_version": 1,
            "metadata_id": "metadata-a",
            "confirmed": True,
            "identity": {
                "chinese_title": "想见你",
                "english_title": "Someday or One Day The Movie",
                "year": "2022",
                "content_kind": "extension_movie",
                "external_ids": {},
            },
            "relation": {
                "type": "sequel",
                "target_series": {
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day",
                    "year": "2019",
                    "external_ids": {},
                },
                "source": "wikipedia",
            },
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": 0,
                "episode_number": 100,
                "mapping_kind": "temporary_related_special",
                "mapping_source": "local_allocator",
                "tvdb_episode_id": "",
            },
            "source_entry": {
                "title": "想见你 (电影)",
                "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
            },
            "items": [],
            "evidence": {},
            "warnings": [],
        }

    def test_temporary_special_uses_locked_target_and_enriches_final_file(self):
        media_metadata = self._confirmed_media_metadata()
        rename_plan = build_confirmed_rename_plan(
            final_path="/真人剧集/Raw.Release",
            selected_path="/真人剧集",
            metadata={},
            media_metadata=media_metadata,
            ai_plan={
                "episode_map": [
                    {
                        "source_file": "Movie.mkv",
                        "season_number": 0,
                        "episode_number": 100,
                    }
                ]
            },
            file_tree=[
                {"name": "Movie.mkv", "relative_path": "Movie.mkv", "is_dir": False}
            ],
        )

        enriched = enrich_media_metadata_with_rename_plan(media_metadata, rename_plan)

        self.assertEqual(
            rename_plan["operations"][0]["rename_to"],
            "Someday or One Day S00E100.mkv",
        )
        self.assertEqual(enriched["items"][0]["season_number"], 0)
        self.assertTrue(
            enriched["items"][0]["final_path"].endswith(
                "Someday or One Day S00E100.mkv"
            )
        )

    def test_missing_ai_season_does_not_default_to_locked_season_zero(self):
        media_metadata = self._confirmed_media_metadata()
        plan = build_confirmed_rename_plan(
            final_path="/真人剧集/Raw.Release",
            selected_path="/真人剧集",
            metadata={},
            media_metadata=media_metadata,
            ai_plan={
                "episode_map": [
                    {"source_file": "Movie.mkv", "episode_number": 100}
                ]
            },
            file_tree=[
                {"name": "Movie.mkv", "relative_path": "Movie.mkv", "is_dir": False}
            ],
        )

        self.assertIsNone(plan)

    def test_confirmed_metadata_allows_partial_mapping_and_reports_unmatched(self):
        media_metadata = self._confirmed_media_metadata()
        media_metadata.update({
            "identity": {
                "chinese_title": "测试剧",
                "english_title": "Test Show",
                "year": "2026",
                "content_kind": "series",
                "external_ids": {},
            },
            "relation": {"type": "primary", "target_series": {}, "source": "user"},
            "placement": {
                "library_type": "series",
                "category_kind": "live_action_series",
                "season_number": None,
                "episode_number": None,
                "mapping_kind": "standalone",
                "mapping_source": "user",
                "tvdb_episode_id": "",
            },
            "source_entry": {},
            "items": [
                {
                    "content_role": "main_episode",
                    "season_number": 1,
                    "episode_number": 1,
                },
                {
                    "content_role": "ova",
                    "season_number": 0,
                    "episode_number": 3,
                },
            ],
        })
        plan = build_confirmed_rename_plan(
            final_path="/真人剧集/Raw.Release",
            selected_path="/真人剧集",
            metadata={},
            media_metadata=media_metadata,
            ai_plan={
                "episode_map": [
                    {
                        "source_file": "Main.mkv",
                        "season_number": 1,
                        "episode_number": 1,
                    },
                    {
                        "source_file": "OVA.mkv",
                        "season_number": 0,
                        "episode_number": 3,
                    },
                ]
            },
            file_tree=[
                {"name": "Main.mkv", "relative_path": "Main.mkv", "is_dir": False},
                {"name": "OVA.mkv", "relative_path": "OVA.mkv", "is_dir": False},
                {
                    "name": "Unknown.mkv",
                    "relative_path": "Unknown.mkv",
                    "is_dir": False,
                },
            ],
        )
        self.assertEqual(len(plan["operations"]), 2)
        self.assertEqual(
            {operation["rename_to"] for operation in plan["operations"]},
            {"Test Show S01E01.mkv", "Test Show S00E03.mkv"},
        )
        self.assertEqual(plan["unmatched_sources"], ["Unknown.mkv"])

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

    def test_build_plan_normalizes_chinese_punctuation_in_final_target_root(self):
        plan = build_tvdb_rename_plan(
            final_path="/真人剧集/Release.Name",
            selected_path="/真人剧集",
            metadata={
                "chinese_title": "嗜血法医：源罪（前传）——第一季",
                "english_title": "Dexter Original Sin",
            },
            ai_plan={
                "tvdb_series_id": "454",
                "series_name": "Dexter Original Sin",
                "episode_map": [
                    {
                        "source_file": "Dexter.Original.Sin.S01E01.mkv",
                        "season_number": 1,
                        "episode_number": 1,
                    }
                ],
            },
            file_tree=[
                {
                    "name": "Dexter.Original.Sin.S01E01.mkv",
                    "relative_path": "Dexter.Original.Sin.S01E01.mkv",
                    "is_dir": False,
                }
            ],
            tvdb_candidates=[{"tvdb_series_id": "454", "name": "Dexter Original Sin"}],
            tvdb_episodes=[],
        )

        self.assertEqual(
            plan["target_root"],
            "/真人剧集/嗜血法医: 源罪(前传) - 第一季 (Dexter Original Sin)",
        )

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

    def test_build_plan_rejects_partial_video_mapping(self):
        plan = build_tvdb_rename_plan(
            final_path="/真人剧集/Release.Name",
            selected_path="/真人剧集",
            metadata={"chinese_title": "测试剧", "english_title": "Test Show"},
            ai_plan={
                "tvdb_series_id": "100",
                "series_name": "Test Show",
                "episode_map": [
                    {
                        "source_file": "Test.Show.S01E01.mkv",
                        "tvdb_episode_id": "101",
                        "season_number": 1,
                        "episode_number": 1,
                    }
                ],
            },
            file_tree=[
                {"name": "Test.Show.S01E01.mkv", "relative_path": "Test.Show.S01E01.mkv", "is_dir": False},
                {"name": "Test.Show.S01E02.mkv", "relative_path": "Test.Show.S01E02.mkv", "is_dir": False},
            ],
            tvdb_candidates=[{"tvdb_series_id": "100", "name": "Test Show"}],
            tvdb_episodes=[
                {"tvdb_episode_id": "101", "season_number": 1, "episode_number": 1},
                {"tvdb_episode_id": "102", "season_number": 1, "episode_number": 2},
            ],
        )

        self.assertIsNone(plan)

    def test_build_plan_rejects_duplicate_source_mapping(self):
        plan = build_tvdb_rename_plan(
            final_path="/真人剧集/Release.Name",
            selected_path="/真人剧集",
            metadata={"chinese_title": "测试剧", "english_title": "Test Show"},
            ai_plan={
                "tvdb_series_id": "100",
                "series_name": "Test Show",
                "episode_map": [
                    {
                        "source_file": "Test.Show.S01E01.mkv",
                        "tvdb_episode_id": "101",
                        "season_number": 1,
                        "episode_number": 1,
                    },
                    {
                        "source_file": "Test.Show.S01E01.mkv",
                        "tvdb_episode_id": "102",
                        "season_number": 1,
                        "episode_number": 2,
                    },
                ],
            },
            file_tree=[
                {"name": "Test.Show.S01E01.mkv", "relative_path": "Test.Show.S01E01.mkv", "is_dir": False}
            ],
            tvdb_candidates=[{"tvdb_series_id": "100", "name": "Test Show"}],
            tvdb_episodes=[
                {"tvdb_episode_id": "101", "season_number": 1, "episode_number": 1},
                {"tvdb_episode_id": "102", "season_number": 1, "episode_number": 2},
            ],
        )

        self.assertIsNone(plan)


if __name__ == "__main__":
    unittest.main()
