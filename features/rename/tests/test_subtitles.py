import unittest

from telepiplex_rename.subtitles import (
    SUBTITLE_EXTENSIONS,
    build_movie_subtitle_plan,
    build_series_subtitle_plan,
    collect_subtitle_evidence,
)


class SubtitlePlanningTest(unittest.TestCase):
    def test_collects_all_supported_external_subtitle_formats(self):
        tree = [
            {"name": f"Show.S01E0{index}.CHS{suffix}",
             "relative_path": f"Show.S01E0{index}.CHS{suffix}",
             "is_dir": False}
            for index, suffix in enumerate((".srt", ".ass", ".sup", ".vtt"), 1)
        ]

        evidence = collect_subtitle_evidence(tree)

        self.assertEqual(SUBTITLE_EXTENSIONS, {".srt", ".ass", ".sup", ".vtt"})
        self.assertEqual([item["extension"] for item in evidence], [
            ".srt", ".ass", ".sup", ".vtt",
        ])
        self.assertEqual([item["episode_key"] for item in evidence], [
            (1, 1), (1, 2), (1, 3), (1, 4),
        ])

    def test_flat_subtitles_map_across_seasons_without_directory_coupling(self):
        tree = [{
            "name": "Veep.S01E02.CHS.ass",
            "relative_path": "Veep.S01E02.CHS.ass",
            "path": "/未整理/Veep/Veep.S01E02.CHS.ass",
            "is_dir": False,
        }, {
            "name": "Veep.S04E07.CHS.srt",
            "relative_path": "Veep.S04E07.CHS.srt",
            "path": "/未整理/Veep/Veep.S04E07.CHS.srt",
            "is_dir": False,
        }]

        plan = build_series_subtitle_plan(
            final_path="/未整理/Veep",
            target_root="/真人剧集/副人之仁 (Veep)",
            series_name="Veep",
            file_tree=tree,
            allowed_targets={(1, 2), (4, 7)},
        )

        self.assertEqual([item["rename_to"] for item in plan["operations"]], [
            "Veep S01E02.chi.ass",
            "Veep S04E07.chi.srt",
        ])
        self.assertEqual([item["target_dir"] for item in plan["operations"]], [
            "/真人剧集/副人之仁 (Veep)/Veep Season 01",
            "/真人剧集/副人之仁 (Veep)/Veep Season 04",
        ])
        self.assertEqual(plan["unresolved_sources"], [])

    def test_season_directory_scopes_bare_episode_subtitle(self):
        tree = [{
            "name": "02.CHS.vtt",
            "relative_path": "Season 03/02.CHS.vtt",
            "path": "/未整理/Show/Season 03/02.CHS.vtt",
            "is_dir": False,
        }]

        plan = build_series_subtitle_plan(
            final_path="/未整理/Show",
            target_root="/真人剧集/剧名 (Show)",
            series_name="Show",
            file_tree=tree,
            allowed_targets={(3, 2)},
        )

        self.assertEqual(plan["operations"][0]["rename_to"], "Show S03E02.chi.vtt")

    def test_partial_subtitle_seasons_do_not_require_full_video_inventory(self):
        tree = [{
            "name": "Show.S02E03.CHS.srt",
            "relative_path": "Show.S02E03.CHS.srt",
            "is_dir": False,
        }]

        plan = build_series_subtitle_plan(
            final_path="/未整理/Show.Subtitles",
            target_root="/真人剧集/剧名 (Show)",
            series_name="Show",
            file_tree=tree,
            allowed_targets={(1, 1), (2, 3), (5, 8)},
        )

        self.assertEqual(len(plan["operations"]), 1)
        self.assertEqual(plan["operations"][0]["episode_key"], (2, 3))

    def test_same_language_subtitles_are_all_preserved_per_format(self):
        tree = [{
            "name": "Show.S01E01.CHS.srt",
            "relative_path": "Show.S01E01.CHS.srt",
            "is_dir": False,
        }, {
            "name": "Show.S01E01.CHS&ENG.srt",
            "relative_path": "Show.S01E01.CHS&ENG.srt",
            "is_dir": False,
        }, {
            "name": "Show.S01E01.CHS.ass",
            "relative_path": "Show.S01E01.CHS.ass",
            "is_dir": False,
        }]

        plan = build_series_subtitle_plan(
            final_path="/未整理/Show",
            target_root="/真人剧集/剧名 (Show)",
            series_name="Show",
            file_tree=tree,
            allowed_targets={(1, 1)},
        )

        self.assertEqual(
            {
                item["source_relative_path"]: item["rename_to"]
                for item in plan["operations"]
            },
            {
                "Show.S01E01.CHS&ENG.srt": "Show S01E01.chi.srt",
                "Show.S01E01.CHS.srt": (
                    "Show S01E01.variant-02.chi.srt"
                ),
                "Show.S01E01.CHS.ass": "Show S01E01.chi.ass",
            },
        )
        self.assertEqual(plan["discard_sources"], [])

    def test_all_original_language_markers_use_fixed_chi_suffix(self):
        tree = [{
            "name": "Show.S01E01.CHT.ass",
            "relative_path": "Show.S01E01.CHT.ass",
            "is_dir": False,
        }, {
            "name": "Show.S01E01.ENG.srt",
            "relative_path": "Show.S01E01.ENG.srt",
            "is_dir": False,
        }, {
            "name": "Show.S01E01.JPN.sup",
            "relative_path": "Show.S01E01.JPN.sup",
            "is_dir": False,
        }]

        plan = build_series_subtitle_plan(
            final_path="/未整理/Show",
            target_root="/真人剧集/剧名 (Show)",
            series_name="Show",
            file_tree=tree,
            allowed_targets={(1, 1)},
        )

        self.assertEqual(
            {item["rename_to"] for item in plan["operations"]},
            {
                "Show S01E01.chi.ass",
                "Show S01E01.chi.srt",
                "Show S01E01.chi.sup",
            },
        )
        self.assertEqual(plan["discard_sources"], [])
        self.assertEqual(plan["unresolved_sources"], [])

    def test_unmarked_language_is_renamed_but_ambiguous_episode_stays(self):
        tree = [{
            "name": "Show.S01E01.srt",
            "relative_path": "Show.S01E01.srt",
            "is_dir": False,
        }, {
            "name": "02.CHS.ass",
            "relative_path": "02.CHS.ass",
            "is_dir": False,
        }]

        plan = build_series_subtitle_plan(
            final_path="/未整理/Show",
            target_root="/真人剧集/剧名 (Show)",
            series_name="Show",
            file_tree=tree,
            allowed_targets={(1, 1), (1, 2), (2, 2)},
        )

        self.assertEqual(
            [item["rename_to"] for item in plan["operations"]],
            ["Show S01E01.chi.srt"],
        )
        self.assertEqual(plan["discard_sources"], [])
        self.assertEqual(plan["kept_sources"], ["02.CHS.ass"])
        self.assertEqual(plan["unresolved_sources"], [])

    def test_movie_subtitles_share_the_confirmed_movie_stem(self):
        tree = [{
            "name": "Raw.Movie.2026.CHS&ENG.sup",
            "relative_path": "Subtitles/Raw.Movie.2026.CHS&ENG.sup",
            "path": "/未整理/Raw/Subtitles/Raw.Movie.2026.CHS&ENG.sup",
            "is_dir": False,
        }]

        plan = build_movie_subtitle_plan(
            final_path="/未整理/Raw",
            target_dir="/真人电影/中文名 (English Movie)",
            target_stem="English Movie",
            file_tree=tree,
        )

        self.assertEqual(plan["operations"][0]["rename_to"], "English Movie.chi.sup")
        self.assertEqual(plan["unresolved_sources"], [])


if __name__ == "__main__":
    unittest.main()
