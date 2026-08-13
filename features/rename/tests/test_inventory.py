from pathlib import Path
import tempfile
import unittest

from telepiplex_rename.inventory import (
    contains_video,
    inventory_job_id,
    looks_organized_release,
)
from telepiplex_rename.jobs import RenameJobStore


class InventoryClassificationTest(unittest.TestCase):
    def test_job_identity_prefers_stable_115_file_id(self):
        self.assertEqual(
            inventory_job_id({"file_id": "3493771893368948098"}, "/未整理/Veep"),
            "inventory:3493771893368948098",
        )

    def test_job_identity_falls_back_to_stable_path_digest(self):
        first = inventory_job_id({}, "/未整理/Movie.Release")
        second = inventory_job_id({}, "/未整理/Movie.Release")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("inventory:path:"))

    def test_video_detection_ignores_non_video_files(self):
        self.assertFalse(contains_video([
            {"name": "poster.jpg", "relative_path": "poster.jpg", "is_dir": False},
            {"name": "subtitle.srt", "relative_path": "subtitle.srt", "is_dir": False},
        ]))
        self.assertTrue(contains_video([
            {"name": "Movie.mkv", "relative_path": "Movie.mkv", "is_dir": False},
        ]))

    def test_normalized_series_folder_is_recognized(self):
        tree = [{
            "name": "The Residence Season 01",
            "relative_path": "The Residence Season 01",
            "is_dir": True,
        }, {
            "name": "The Residence S01E01.mkv",
            "relative_path": (
                "The Residence Season 01/The Residence S01E01.mkv"
            ),
            "is_dir": False,
        }, {
            "name": "The Residence S01E02.mkv",
            "relative_path": (
                "The Residence Season 01/The Residence S01E02.mkv"
            ),
            "is_dir": False,
        }]

        self.assertTrue(looks_organized_release(
            "白宫杀人事件 (The Residence)",
            tree,
        ))

    def test_normalized_series_accepts_all_supported_external_subtitles(self):
        tree = [{
            "name": "The Residence Season 01",
            "relative_path": "The Residence Season 01",
            "is_dir": True,
        }, {
            "name": "The Residence S01E01.mkv",
            "relative_path": (
                "The Residence Season 01/The Residence S01E01.mkv"
            ),
            "is_dir": False,
        }]
        for extension in ("srt", "ass", "sup", "vtt"):
            tree.append({
                "name": f"The Residence S01E01.chi.{extension}",
                "relative_path": (
                    "The Residence Season 01/"
                    f"The Residence S01E01.chi.{extension}"
                ),
                "is_dir": False,
            })

        self.assertTrue(looks_organized_release(
            "白宫杀人事件 (The Residence)",
            tree,
        ))

    def test_normalized_series_subtitle_only_folder_is_recognized(self):
        self.assertTrue(looks_organized_release(
            "白宫杀人事件 (The Residence)",
            [{
                "name": "The Residence Season 03",
                "relative_path": "The Residence Season 03",
                "is_dir": True,
            }, {
                "name": "The Residence S03E02.chi.vtt",
                "relative_path": (
                    "The Residence Season 03/"
                    "The Residence S03E02.chi.vtt"
                ),
                "is_dir": False,
            }],
        ))

    def test_raw_or_traditional_series_subtitle_is_not_complete(self):
        for subtitle in (
            "The Residence S01E01.CHS.srt",
            "The Residence S01E01.cht.srt",
            "The Residence S01E01.srt",
        ):
            with self.subTest(subtitle=subtitle):
                self.assertFalse(looks_organized_release(
                    "白宫杀人事件 (The Residence)",
                    [{
                        "name": "The Residence Season 01",
                        "relative_path": "The Residence Season 01",
                        "is_dir": True,
                    }, {
                        "name": subtitle,
                        "relative_path": f"The Residence Season 01/{subtitle}",
                        "is_dir": False,
                    }],
                ))

    def test_series_folder_rejects_extra_nesting(self):
        self.assertFalse(looks_organized_release(
            "白宫杀人事件 (The Residence)",
            [{
                "name": "Extras",
                "relative_path": "Extras",
                "is_dir": True,
            }, {
                "name": "The Residence Season 01",
                "relative_path": "Extras/The Residence Season 01",
                "is_dir": True,
            }, {
                "name": "The Residence S01E01.mkv",
                "relative_path": (
                    "Extras/The Residence Season 01/"
                    "The Residence S01E01.mkv"
                ),
                "is_dir": False,
            }],
        ))

    def test_series_folder_requires_directory_and_file_season_to_match(self):
        self.assertFalse(looks_organized_release(
            "白宫杀人事件 (The Residence)",
            [{
                "name": "The Residence Season 01",
                "relative_path": "The Residence Season 01",
                "is_dir": True,
            }, {
                "name": "The Residence S02E01.mkv",
                "relative_path": (
                    "The Residence Season 01/The Residence S02E01.mkv"
                ),
                "is_dir": False,
            }],
        ))

    def test_normalized_movie_folder_is_recognized(self):
        self.assertTrue(looks_organized_release(
            "布达佩斯大饭店 (The Grand Budapest Hotel)",
            [{
                "name": "The Grand Budapest Hotel.mkv",
                "relative_path": "The Grand Budapest Hotel.mkv",
                "is_dir": False,
            }],
        ))

    def test_normalized_movie_with_subtitle_is_recognized(self):
        self.assertTrue(looks_organized_release(
            "布达佩斯大饭店 (The Grand Budapest Hotel)",
            [{
                "name": "The Grand Budapest Hotel.mkv",
                "relative_path": "The Grand Budapest Hotel.mkv",
                "is_dir": False,
            }, {
                "name": "The Grand Budapest Hotel.chi.sup",
                "relative_path": "The Grand Budapest Hotel.chi.sup",
                "is_dir": False,
            }],
        ))

    def test_normalized_movie_subtitle_only_folder_is_recognized(self):
        self.assertTrue(looks_organized_release(
            "布达佩斯大饭店 (The Grand Budapest Hotel)",
            [{
                "name": "The Grand Budapest Hotel.chi.ass",
                "relative_path": "The Grand Budapest Hotel.chi.ass",
                "is_dir": False,
            }],
        ))

    def test_video_directly_under_category_root_is_not_a_complete_release(self):
        self.assertFalse(looks_organized_release(
            "English Movie.mkv",
            [{
                "name": "English Movie.mkv",
                "relative_path": "English Movie.mkv",
                "is_dir": False,
            }],
        ))

    def test_generated_target_with_colon_is_not_recognized_as_organized(self):
        self.assertFalse(looks_organized_release(
            "星际迷航 (Star Trek: Picard)",
            [{
                "name": "Star Trek: Picard.mkv",
                "relative_path": "Star Trek: Picard.mkv",
                "is_dir": False,
            }],
        ))

    def test_normalized_movie_with_leftover_attachment_is_incomplete(self):
        self.assertFalse(looks_organized_release(
            "布达佩斯大饭店 (The Grand Budapest Hotel)",
            [{
                "name": "The Grand Budapest Hotel.mkv",
                "relative_path": "The Grand Budapest Hotel.mkv",
                "is_dir": False,
            }, {
                "name": "poster.jpg",
                "relative_path": "poster.jpg",
                "is_dir": False,
            }],
        ))

    def test_normalized_movie_collection_container_is_recognized(self):
        self.assertTrue(looks_organized_release(
            "碟中谍 (Mission Impossible)",
            [{
                "name": "碟中谍 (Mission Impossible)",
                "relative_path": "碟中谍 (Mission Impossible)",
                "is_dir": True,
            }, {
                "name": "Mission Impossible.mkv",
                "relative_path": (
                    "碟中谍 (Mission Impossible)/Mission Impossible.mkv"
                ),
                "is_dir": False,
            }, {
                "name": "碟中谍2 (Mission Impossible 2)",
                "relative_path": "碟中谍2 (Mission Impossible 2)",
                "is_dir": True,
            }, {
                "name": "Mission Impossible 2.mkv",
                "relative_path": (
                    "碟中谍2 (Mission Impossible 2)/"
                    "Mission Impossible 2.mkv"
                ),
                "is_dir": False,
            }],
        ))

    def test_raw_release_is_not_recognized_as_organized(self):
        self.assertFalse(looks_organized_release(
            "Veep (2012) Season 1-7 S01-S07 1080p",
            [{
                "name": "Veep (2012) - S01E01 - Fundraiser.mkv",
                "relative_path": (
                    "Season 1/Veep (2012) - S01E01 - Fundraiser.mkv"
                ),
                "is_dir": False,
            }],
        ))

    def test_raw_release_is_not_completed_only_because_file_matches_folder(self):
        self.assertFalse(looks_organized_release(
            "Movie.2024.1080p.WEB-DL",
            [{
                "name": "Movie.2024.1080p.WEB-DL.mkv",
                "relative_path": "Movie.2024.1080p.WEB-DL.mkv",
                "is_dir": False,
            }],
        ))

    def test_numeric_movie_title_remains_a_valid_english_only_target(self):
        self.assertTrue(looks_organized_release(
            "1917",
            [{
                "name": "1917.mkv",
                "relative_path": "1917.mkv",
                "is_dir": False,
            }],
        ))

    def test_completed_inventory_job_can_be_reopened_for_live_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            self.assertTrue(jobs.claim("inventory:raw-1"))
            jobs.update(
                "inventory:raw-1",
                "completed",
                {"organized": True},
            )

            self.assertTrue(jobs.claim_retryable(
                "inventory:raw-1",
                reopen_completed=True,
            ))
            self.assertEqual(
                jobs.get("inventory:raw-1")["state"],
                "processing",
            )


if __name__ == "__main__":
    unittest.main()
