import ast
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from telepiplex_plugin_sdk.media_metadata import attach_media_metadata

from telepiplex_rename.content_probe import build_metadata_probe
from telepiplex_rename.ai import recover_query_with_ai
from telepiplex_rename.models import DownloadCompletedEvent
from telepiplex_rename.query_recovery import recover_metadata_probe
from telepiplex_rename.processor import (
    _deterministic_episode_plan,
    process_generic_media,
    process_tvdb_episode,
)
from telepiplex_rename.service import RenameFeature


ROOT = Path(__file__).resolve().parents[1]


class FakeStorage:
    def __init__(self, items):
        self.items = items
        self.renamed = []
        self.moved = []
        self.deleted = []
        self.created = []

    def get_file_info(self, path):
        if path in {"/Downloads/Release", "/Downloads/Series.Release"}:
            return {"file_id": "root", "file_category": "0"}
        return None

    def get_file_list(self, params):
        return self.items if params.get("cid") == "root" else []

    def create_dir_recursive(self, path):
        self.created.append(path)
        return {"file_id": "target"}

    def rename(self, path, name):
        self.renamed.append((path, name))
        return True

    def move_file(self, source, target):
        self.moved.append((source, target))
        return True

    def move_file_detailed(self, source, target):
        moved = self.move_file(source, target)
        return {"state": "moved" if moved else "copy_failed", "copied": moved,
                "source_deleted": moved, "source_path": source, "target_path": target}

    def delete_single_file(self, path):
        self.deleted.append(path)
        return True


class CopiedSourceRetainedStorage(FakeStorage):
    def move_file_detailed(self, source, target):
        self.moved.append((source, target))
        return {
            "state": "copied_source_retained",
            "copied": True,
            "source_deleted": False,
            "source_path": source,
            "target_path": f"{target}/{source.rsplit('/', 1)[-1]}",
        }


class CleanupFailureStorage(FakeStorage):
    def delete_single_file(self, path):
        self.deleted.append(path)
        return path != "/Downloads/Release"


class SecondMoveFailureStorage(FakeStorage):
    def move_file(self, source, target):
        self.moved.append((source, target))
        return len(self.moved) < 2


class ExtraVideoDeleteFailureStorage(FakeStorage):
    def delete_single_file(self, path):
        self.deleted.append(path)
        return not path.endswith("sample.mp4")


class TargetConflictStorage(FakeStorage):
    def get_file_info(self, path):
        if path.endswith("/中文电影 (English Movie)/English Movie.mkv"):
            return {"file_id": "existing", "file_category": "1"}
        return super().get_file_info(path)


class SeriesTargetConflictStorage(FakeStorage):
    def get_file_info(self, path):
        if path.endswith("/English Series Season 01/English Series S01E01.mkv"):
            return {"file_id": "existing", "file_category": "1"}
        return super().get_file_info(path)


class SubtitleTargetConflictStorage(FakeStorage):
    def get_file_info(self, path):
        if path.endswith("/English Movie.chi.srt"):
            return {"file_id": "existing-subtitle", "file_category": "1"}
        return super().get_file_info(path)


class IdenticalSubtitleTargetStorage(FakeStorage):
    def get_file_info(self, path):
        if path.endswith("/English Movie.chi.srt"):
            return {"file_id": "existing-subtitle", "sha1": "same"}
        if path.endswith("/Movie.2024.CHS.srt"):
            return {"file_id": "source-subtitle", "sha1": "same"}
        return super().get_file_info(path)


def movie_contract():
    return {
        "schema_version": 1,
        "metadata_id": "movie-1",
        "confirmed": True,
        "identity": {
            "chinese_title": "中文电影",
            "english_title": "English Movie",
            "year": "2024",
            "content_kind": "movie",
            "external_ids": {},
        },
        "relation": {"target_series": None, "source": "confirmed"},
        "placement": {
            "category_kind": "live_action_movie",
            "library_type": "movie",
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "evidence": {}, "warnings": [], "items": [],
    }


def series_contract():
    return {
        "schema_version": 1,
        "metadata_id": "series-1",
        "confirmed": True,
        "identity": {
            "chinese_title": "中文剧集",
            "english_title": "English Series",
            "year": "2024",
            "content_kind": "main_episode",
            "external_ids": {},
        },
        "relation": {"target_series": None, "source": "confirmed"},
        "placement": {
            "category_kind": "live_action_series",
            "library_type": "series",
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "evidence": {}, "warnings": [],
        "items": [{
            "item_id": "e1", "content_role": "main_episode",
            "season_number": 1, "episode_number": 1,
        }],
    }


def wikipedia_bounded_season_contract():
    value = series_contract()
    value["identity"]["season_count"] = 7
    value["retrieval"] = {
        "media_type": "series",
        "scope": "season",
        "query": "English Series S01",
        "queries": ["English Series S01"],
    }
    value["items"] = []
    value["evidence"] = {"decision": {
        "scope": "season",
        "season_number": 1,
        "episode_number": None,
    }}
    value["warnings"] = ["warning:episode_inventory_unavailable"]
    return value


class RenamingProcessorTest(unittest.TestCase):
    def setUp(self):
        from telepiplex_rename.context import runtime_context

        runtime_context.configure({
            "media": {"unorganized_path": "/Unorganized"},
            "selection": {
                "movie_size_fallback_ratio": 1.5,
                "unmatched_large_ratio": 0.25,
                "unmatched_large_min_bytes": 300_000_000,
            },
            "ai": {},
            "metadata": {},
        })

    def test_wikipedia_bounded_season_derives_only_same_season_filename_markers(self):
        plan = _deterministic_episode_plan(
            wikipedia_bounded_season_contract(),
            [
                {"relative_path": "Show.S01E01.mkv", "is_dir": False},
                {"relative_path": "Show.S01E02.mkv", "is_dir": False},
            ],
        )
        self.assertEqual(
            [
                (item["season_number"], item["episode_number"])
                for item in plan["episode_map"]
            ],
            [(1, 1), (1, 2)],
        )
        self.assertIsNone(_deterministic_episode_plan(
            wikipedia_bounded_season_contract(),
            [{"relative_path": "Show.S02E01.mkv", "is_dir": False}],
        ))

    def test_probe_uses_root_identity_and_separates_content_shape(self):
        probe = build_metadata_probe({
            "download_root": "/Downloads/The.Office.US",
            "resource_name": "The.Office.US",
            "release": {"title": "The.Office.US.S01-S09.1080p"},
            "file_tree": [{
                "relative_path": "S01/The.Office.S01E01.mkv",
                "is_dir": False,
            }, {
                "relative_path": "S09/The.Office.S09E23.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "The Office US")
        self.assertEqual(probe["content_shape"], "multi_season_pack")
        self.assertEqual(probe["observed_seasons"], [1, 9])
        self.assertNotIn("S09E23", probe["identity_query"])

    def test_probe_uses_subtitle_paths_when_tree_has_no_video(self):
        probe = build_metadata_probe({
            "resource_name": "Raw.Subtitles",
            "file_tree": [{
                "relative_path": "Veep.S03E01.CHS&ENG.ass",
                "is_dir": False,
            }, {
                "relative_path": "Veep.S03E02.CHS.vtt",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Veep")
        self.assertEqual(probe["content_shape"], "season_pack")
        self.assertEqual(probe["observed_seasons"], [3])
        self.assertEqual(probe["subtitle_count"], 2)
        self.assertEqual(probe["video_count"], 0)

    def test_probe_video_identity_remains_authoritative_over_subtitle_name(self):
        probe = build_metadata_probe({
            "resource_name": "Raw.Release",
            "file_tree": [{
                "relative_path": "Season 01/Veep.S01E01.mkv",
                "is_dir": False,
            }, {
                "relative_path": "Wrong.Show.S01E01.CHS.srt",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Veep")
        self.assertEqual(probe["video_count"], 1)
        self.assertEqual(probe["subtitle_count"], 1)

    def test_probe_keeps_more_specific_related_root_for_single_episode(self):
        probe = build_metadata_probe({
            "resource_name": "The.Office.US",
            "file_tree": [{
                "relative_path": "The.Office.S01E01.1080p.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "The Office US")
        self.assertEqual(probe["content_shape"], "single_episode")

    def test_probe_strips_scope_and_quality_but_keeps_movie_year(self):
        probe = build_metadata_probe({
            "resource_name": "Movie.2024.1080p.WEB-DL.mkv",
            "file_tree": [{
                "relative_path": "Movie.2024.1080p.WEB-DL.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Movie 2024")
        self.assertEqual(probe["year_hint"], "2024")
        self.assertEqual(probe["content_shape"], "movie")

    def test_probe_extracts_year_before_truncating_season_markers(self):
        probe = build_metadata_probe({
            "resource_name": "The.Office.US.S01.2005.1080p.WEB-DL",
            "file_tree": [{
                "relative_path": "The.Office.US.S01E01.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "The Office US")
        self.assertEqual(probe["year_hint"], "2005")
        self.assertEqual(probe["content_shape"], "single_episode")

    def test_probe_preserves_bare_episode_pack_without_inventing_season(self):
        probe = build_metadata_probe({
            "resource_name": "Honey.and.Clover",
            "file_tree": [
                {
                    "relative_path": f"Honey.and.Clover.E{number:02d}.mkv",
                    "is_dir": False,
                }
                for number in range(1, 14)
            ] + [{
                "relative_path": "Honey.and.Clover.Special.mp4",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Honey and Clover")
        self.assertEqual(probe["content_shape"], "episode_pack_unscoped")
        self.assertEqual(probe["observed_seasons"], [])
        self.assertEqual(
            probe["observed_episodes"],
            [
                {"season_number": None, "episode_number": number}
                for number in range(1, 14)
            ],
        )
        self.assertEqual(probe["video_count"], 14)

    def test_probe_uses_video_filename_consensus_when_root_has_no_identity(self):
        probe = build_metadata_probe({
            "resource_name": "Raw.Release",
            "file_tree": [{
                "relative_path": (
                    "Season 01/The.Residence.S01E01.1080p.WEB-DL.mkv"
                ),
                "is_dir": False,
            }, {
                "relative_path": (
                    "Season 01/The.Residence.S01E02.1080p.WEB-DL.mkv"
                ),
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "The Residence")
        self.assertEqual(probe["content_shape"], "season_pack")
        self.assertEqual(probe["observed_seasons"], [1])

    def test_probe_uses_single_webm_filename_when_root_is_numeric(self):
        probe = build_metadata_probe({
            "resource_name": "958271604",
            "file_tree": [{
                "relative_path": (
                    "The.Grand.Budapest.Hotel.2014.1080p.WEB-DL.webm"
                ),
                "is_dir": False,
            }],
        })

        self.assertEqual(
            probe["identity_query"],
            "The Grand Budapest Hotel 2014",
        )
        self.assertEqual(probe["year_hint"], "2014")
        self.assertEqual(probe["content_shape"], "movie")
        self.assertEqual(probe["video_count"], 1)

    def test_probe_extracts_chinese_season_and_episode_markers(self):
        probe = build_metadata_probe({
            "resource_name": "庆余年 第二季",
            "file_tree": [{
                "relative_path": "庆余年 第二季/庆余年 第二季 第1集.mkv",
                "is_dir": False,
            }, {
                "relative_path": "庆余年 第二季/庆余年 第二季 第十二集.webm",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "庆余年")
        self.assertEqual(probe["content_shape"], "season_pack")
        self.assertEqual(probe["observed_seasons"], [2])
        self.assertEqual(probe["observed_episodes"], [{
            "season_number": 2,
            "episode_number": 1,
        }, {
            "season_number": 2,
            "episode_number": 12,
        }])
        self.assertEqual(probe["video_count"], 2)

    def test_probe_treats_anime_dash_numbers_as_unscoped_episodes(self):
        probe = build_metadata_probe({
            "resource_name": "[SubsPlease] Sousou no Frieren",
            "file_tree": [{
                "relative_path": (
                    "[SubsPlease] Sousou no Frieren - 01 (1080p) [A1].mkv"
                ),
                "is_dir": False,
            }, {
                "relative_path": (
                    "[SubsPlease] Sousou no Frieren - 02 (1080p) [A2].mkv"
                ),
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Sousou no Frieren")
        self.assertEqual(
            probe["content_shape"],
            "episode_pack_unscoped",
        )
        self.assertEqual(probe["observed_seasons"], [])
        self.assertEqual(probe["observed_episodes"], [{
            "season_number": None,
            "episode_number": 1,
        }, {
            "season_number": None,
            "episode_number": 2,
        }])

    def test_probe_returns_empty_query_when_tree_has_only_scope_markers(self):
        probe = build_metadata_probe({
            "resource_name": "Season 01",
            "file_tree": [{
                "relative_path": "Season 01/E01.mkv",
                "is_dir": False,
            }, {
                "relative_path": "Season 01/E02.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "")
        self.assertEqual(probe["content_shape"], "season_pack")
        self.assertEqual(probe["observed_seasons"], [1])
        self.assertEqual(probe["observed_episodes"], [{
            "season_number": 1,
            "episode_number": 1,
        }, {
            "season_number": 1,
            "episode_number": 2,
        }])

    def test_probe_uses_nested_title_and_scoped_bare_episode_numbers(self):
        probe = build_metadata_probe({
            "resource_name": "958271604",
            "file_tree": [{
                "relative_path": "The Residence/Season 01/01.mkv",
                "is_dir": False,
            }, {
                "relative_path": "The Residence/Season 01/02.webm",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "The Residence")
        self.assertEqual(probe["content_shape"], "season_pack")
        self.assertEqual(probe["observed_seasons"], [1])
        self.assertEqual(probe["observed_episodes"], [{
            "season_number": 1,
            "episode_number": 1,
        }, {
            "season_number": 1,
            "episode_number": 2,
        }])

    def test_probe_prefers_repeated_file_identity_over_unrelated_root(self):
        probe = build_metadata_probe({
            "resource_name": "Mislabeled Folder",
            "file_tree": [{
                "relative_path": "The.Residence.S01E01.1080p.mkv",
                "is_dir": False,
            }, {
                "relative_path": "The.Residence.S01E02.1080p.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "The Residence")

    def test_probe_prefers_strong_single_file_identity_over_unrelated_root(self):
        probe = build_metadata_probe({
            "resource_name": "Mislabeled Folder",
            "file_tree": [{
                "relative_path": "The.Residence.S01E01.1080p.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "The Residence")
        self.assertEqual(probe["content_shape"], "single_episode")

    def test_probe_does_not_guess_when_file_identities_conflict(self):
        probe = build_metadata_probe({
            "resource_name": "Mislabeled Folder",
            "file_tree": [{
                "relative_path": "First.Show.S01E01.1080p.mkv",
                "is_dir": False,
            }, {
                "relative_path": "Second.Show.S01E01.1080p.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "")

    def test_probe_does_not_treat_prefix_titles_as_the_same_identity(self):
        probe = build_metadata_probe({
            "resource_name": "Aliens",
            "file_tree": [{
                "relative_path": f"Alien.S01E{episode:02d}.1080p.mkv",
                "is_dir": False,
            } for episode in (1, 2)],
        })

        self.assertEqual(probe["identity_query"], "Alien")

    def test_probe_treats_hyphen_and_space_as_the_same_file_identity(self):
        probe = build_metadata_probe({
            "resource_name": "Raw.Release",
            "file_tree": [{
                "relative_path": "Spider-Man.S01E01.1080p.mkv",
                "is_dir": False,
            }, {
                "relative_path": "Spider.Man.S01E02.1080p.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Spider-Man")

    def test_probe_uses_nested_title_for_one_scoped_bare_episode(self):
        probe = build_metadata_probe({
            "resource_name": "958271604",
            "file_tree": [{
                "relative_path": "The Residence/Season 01/01.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "The Residence")
        self.assertEqual(probe["content_shape"], "single_episode")
        self.assertEqual(probe["observed_episodes"], [{
            "season_number": 1,
            "episode_number": 1,
        }])

    def test_probe_preserves_title_hyphens_in_identity_query(self):
        probe = build_metadata_probe({
            "resource_name": "Spider-Man.2024.1080p.WEB-DL.mkv",
            "file_tree": [{
                "relative_path": "Spider-Man.2024.1080p.WEB-DL.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Spider-Man 2024")

    def test_probe_excludes_sample_video_from_identity_and_shape(self):
        probe = build_metadata_probe({
            "resource_name": "Raw.Release",
            "file_tree": [{
                "relative_path": "Movie.2024.1080p.WEB-DL.mkv",
                "is_dir": False,
            }, {
                "relative_path": "Sample/sample.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Movie 2024")
        self.assertEqual(probe["content_shape"], "movie")
        self.assertEqual(probe["video_count"], 1)

    def test_probe_excludes_release_named_sample_video(self):
        probe = build_metadata_probe({
            "resource_name": "Movie.2024",
            "file_tree": [{
                "relative_path": "Movie.2024.1080p.WEB-DL.mkv",
                "is_dir": False,
            }, {
                "relative_path": "Movie.2024.sample.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Movie 2024")
        self.assertEqual(probe["content_shape"], "movie")
        self.assertEqual(probe["video_count"], 1)

    def test_probe_exposes_bounded_high_confidence_identity_evidence(self):
        probe = build_metadata_probe({
            "resource_name": "The.Residence.S01.1080p.WEB-DL",
            "file_tree": [{
                "relative_path": (
                    f"Season 01/The.Residence.S01E{episode:02d}.1080p.mkv"
                ),
                "is_dir": False,
            } for episode in range(1, 9)],
        })

        self.assertEqual(probe["identity_candidates"], ["The Residence"])
        self.assertEqual(probe["query_confidence"], "high")
        self.assertFalse(probe["requires_recovery"])
        self.assertEqual(probe["recovery_reasons"], [])
        self.assertLessEqual(len(probe["query_evidence"]), 12)
        self.assertTrue(any(
            item["source"] == "filename_consensus"
            and item["candidate"] == "The Residence"
            for item in probe["query_evidence"]
        ))

    def test_probe_accepts_numeric_title_with_episode_evidence(self):
        probe = build_metadata_probe({
            "resource_name": "958271604",
            "file_tree": [{
                "relative_path": "24.S01E01.1080p.WEB-DL.mkv",
                "is_dir": False,
            }, {
                "relative_path": "24.S01E02.1080p.WEB-DL.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "24")
        self.assertEqual(probe["identity_candidates"], ["24"])
        self.assertEqual(probe["query_confidence"], "medium")
        self.assertFalse(probe["requires_recovery"])
        self.assertIn("numeric_title", probe["recovery_reasons"])

    def test_probe_strips_site_and_repeated_fansub_prefixes(self):
        site = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": (
                    "www.Torrenting.com - The.Residence.S01E01.1080p.mkv"
                ),
                "is_dir": False,
            }],
        })
        grouped = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": (
                    "[Jumonji-Giri]_[F-B]_Series_Title_Ep04_"
                    "(0b0e2c10).mkv"
                ),
                "is_dir": False,
            }],
        })

        self.assertEqual(site["identity_query"], "The Residence")
        self.assertEqual(grouped["identity_query"], "Series Title")
        self.assertEqual(grouped["content_shape"], "single_episode_unscoped")
        self.assertEqual(grouped["observed_episodes"], [{
            "season_number": None,
            "episode_number": 4,
        }])

    def test_probe_skips_compact_group_in_bracket_only_release(self):
        probe = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": (
                    "[BeanSub][Anime_Series_Title][01][GB][1080P]"
                    "[x264_AAC].mkv"
                ),
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Anime Series Title")
        self.assertNotIn("BeanSub", probe["identity_candidates"])

    def test_probe_uses_title_directory_before_language_only_path_tail(self):
        probe = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": (
                    "Босх: Спадок (S2E1) /Series: Legacy (S2E1) "
                    "(2023) WEB-DL 1080p Ukr/Eng | sub Eng.mkv"
                ),
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "Series: Legacy")
        self.assertEqual(probe["content_shape"], "single_episode")

    def test_probe_rejects_scope_only_filename_without_identity(self):
        for raw in (
            "S2009E09 [SDTV].avi",
            "2009x09 [SDTV].avi",
            "2x04x05.720p.BluRay-FUTV.mkv",
        ):
            with self.subTest(raw=raw):
                probe = build_metadata_probe({
                    "resource_name": "3493771893368948098",
                    "file_tree": [{
                        "relative_path": raw,
                        "is_dir": False,
                    }],
                })
                self.assertEqual(probe["identity_query"], "")
                self.assertTrue(probe["requires_recovery"])

    def test_probe_strips_generic_site_prefix_and_leading_movie_year(self):
        site = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": (
                    "www.5MovieRulz.tc - Movie (2000) Malayalam HQ "
                    "HDRip - x264 - AAC - 700MB.mkv"
                ),
                "is_dir": False,
            }],
        })
        year_first = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": "(1995) Movie Name.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(site["identity_query"], "Movie (2000) Malayalam HQ")
        self.assertEqual(site["year_hint"], "2000")
        self.assertEqual(year_first["identity_query"], "Movie Name")
        self.assertEqual(year_first["year_hint"], "1995")

    def test_probe_extracts_chinese_bracket_absolute_episode(self):
        probe = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": (
                    "[风车字幕组][名侦探柯南][857][简体][720P][MP4].mp4"
                ),
                "is_dir": False,
            }, {
                "relative_path": (
                    "[风车字幕组][名侦探柯南][858][简体][720P][MP4].mp4"
                ),
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["identity_query"], "名侦探柯南")
        self.assertEqual(probe["content_shape"], "episode_pack_unscoped")
        self.assertEqual(probe["observed_episodes"], [{
            "season_number": None,
            "episode_number": 857,
        }, {
            "season_number": None,
            "episode_number": 858,
        }])

    def test_probe_preserves_four_digit_absolute_episode_numbers(self):
        bracketed = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": (
                    "[Skymoon-Raws][One Piece][1008][1080p][MKV].mkv"
                ),
                "is_dir": False,
            }],
        })
        scoped = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": "One.Piece.S01E1008.1080p.WEB-DL.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(bracketed["identity_query"], "One Piece")
        self.assertEqual(bracketed["observed_episodes"], [{
            "season_number": None,
            "episode_number": 1008,
        }])
        self.assertEqual(scoped["identity_query"], "One Piece")
        self.assertEqual(scoped["observed_episodes"], [{
            "season_number": 1,
            "episode_number": 1008,
        }])

    def test_probe_does_not_treat_episode_resolution_as_absolute_number(self):
        probe = build_metadata_probe({
            "resource_name": "The.Residence.S01.1080p.WEB-DL",
            "file_tree": [{
                "relative_path": (
                    "The.Residence.S01E01.Episode.1080p.WEB-DL.mkv"
                ),
                "is_dir": False,
            }],
        })

        self.assertEqual(probe["observed_episodes"], [{
            "season_number": 1,
            "episode_number": 1,
        }])

    def test_probe_expands_chained_and_ranged_episodes(self):
        chained = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": (
                    "Series.Title.S07E22E23.720p.HDTV.mkv"
                ),
                "is_dir": False,
            }],
        })
        ranged = build_metadata_probe({
            "resource_name": "3493771893368948098",
            "file_tree": [{
                "relative_path": "Series.Title.S03E01-06.BDRip.mkv",
                "is_dir": False,
            }],
        })

        self.assertEqual(chained["identity_query"], "Series Title")
        self.assertEqual(chained["content_shape"], "season_pack")
        self.assertEqual(
            [item["episode_number"] for item in chained["observed_episodes"]],
            [22, 23],
        )
        self.assertEqual(ranged["identity_query"], "Series Title")
        self.assertEqual(ranged["content_shape"], "season_pack")
        self.assertEqual(
            [item["episode_number"] for item in ranged["observed_episodes"]],
            [1, 2, 3, 4, 5, 6],
        )

    @patch("telepiplex_rename.query_recovery.recover_query_with_ai")
    def test_probe_recovery_accepts_only_evidence_bound_ai_query(self, ai_mock):
        ai_mock.return_value = {
            "status": "ok",
            "identity_query": "名侦探柯南",
            "evidence_candidates": ["名侦探柯南"],
            "year_hint": "",
        }
        probe = {
            "identity_query": "",
            "identity_candidates": ["名侦探柯南"],
            "query_confidence": "low",
            "query_evidence": [{
                "source": "bracket_identity",
                "candidate": "名侦探柯南",
                "relative_path": "x" * 2000 + ".mp4",
            }],
            "requires_recovery": True,
            "recovery_reasons": ["unsupported_release_syntax"],
            "year_hint": "",
            "content_shape": "single_episode_unscoped",
            "observed_seasons": [],
            "observed_episodes": [{
                "season_number": None,
                "episode_number": 857,
            }],
            "video_count": 1,
        }

        recovered = recover_metadata_probe(probe)

        self.assertEqual(recovered["identity_query"], "名侦探柯南")
        self.assertEqual(recovered["query_confidence"], "medium")
        self.assertFalse(recovered["requires_recovery"])
        self.assertEqual(recovered["recovery_source"], "ai_evidence_bound")
        context = ai_mock.call_args.args[0]
        self.assertNotIn("file_tree", context)
        self.assertLessEqual(len(context["representative_paths"]), 8)
        self.assertLessEqual(
            max(map(len, context["representative_paths"]), default=0),
            512,
        )
        self.assertLessEqual(
            max(
                (
                    len(item.get("relative_path", ""))
                    for item in context["query_evidence"]
                ),
                default=0,
            ),
            512,
        )

    @patch("telepiplex_rename.ai.chat_completion")
    def test_query_recovery_respects_disabled_ai_config(self, chat_mock):
        from telepiplex_rename.context import runtime_context

        runtime_context.configure({
            "ai": {
                "enable": False,
                "api_url": "https://ai.example/v1",
                "api_key": "secret",
                "model": "model",
            },
        })

        result = recover_query_with_ai({
            "identity_candidates": ["The Residence"],
        })

        self.assertIsNone(result)
        chat_mock.assert_not_called()

    @patch("telepiplex_rename.query_recovery.recover_query_with_ai")
    def test_probe_recovery_rejects_ai_title_outside_evidence(self, ai_mock):
        ai_mock.return_value = {
            "status": "ok",
            "identity_query": "One Piece",
            "evidence_candidates": ["One Piece"],
            "year_hint": "",
        }
        probe = {
            "identity_query": "",
            "identity_candidates": ["名侦探柯南"],
            "query_confidence": "low",
            "query_evidence": [{
                "source": "bracket_identity",
                "candidate": "名侦探柯南",
                "relative_path": "[字幕组][名侦探柯南][857].mp4",
            }],
            "requires_recovery": True,
            "recovery_reasons": ["unsupported_release_syntax"],
            "year_hint": "",
            "content_shape": "single_episode_unscoped",
            "observed_seasons": [],
            "observed_episodes": [],
            "video_count": 1,
        }

        recovered = recover_metadata_probe(probe)

        self.assertEqual(recovered["identity_query"], "")
        self.assertTrue(recovered["requires_recovery"])
        self.assertEqual(recovered["recovery_status"], "rejected")

    @patch("telepiplex_rename.query_recovery.recover_query_with_ai")
    def test_probe_recovery_rejects_generic_evidence_substring(self, ai_mock):
        ai_mock.return_value = {
            "status": "ok",
            "identity_query": "Series",
            "evidence_candidates": ["Anime Series Title"],
            "year_hint": "",
        }
        probe = {
            "identity_query": "Anime Series Title [CHT]",
            "identity_candidates": ["Anime Series Title"],
            "query_confidence": "low",
            "query_evidence": [{
                "source": "bracket_identity",
                "candidate": "Anime Series Title",
            }],
            "requires_recovery": True,
            "recovery_reasons": ["unsupported_release_syntax"],
            "year_hint": "",
            "content_shape": "single_episode_unscoped",
            "observed_seasons": [],
            "observed_episodes": [],
            "video_count": 1,
        }

        recovered = recover_metadata_probe(probe)

        self.assertEqual(recovered["identity_query"], probe["identity_query"])
        self.assertTrue(recovered["requires_recovery"])
        self.assertEqual(recovered["recovery_status"], "rejected")

    @patch("telepiplex_rename.query_recovery.recover_query_with_ai")
    def test_probe_recovery_never_overrides_identity_conflict(self, ai_mock):
        probe = build_metadata_probe({
            "resource_name": "958271604",
            "file_tree": [{
                "relative_path": "First.Show.S01E01.1080p.mkv",
                "is_dir": False,
            }, {
                "relative_path": "Second.Show.S01E02.1080p.mkv",
                "is_dir": False,
            }],
        })

        recovered = recover_metadata_probe(probe)

        ai_mock.assert_not_called()
        self.assertEqual(recovered["identity_query"], "")
        self.assertTrue(recovered["requires_recovery"])
        self.assertEqual(
            recovered["recovery_status"],
            "blocked_identity_conflict",
        )

    def test_ordinary_movie_keeps_largest_video_and_deletes_everything_else(self):
        storage = FakeStorage([
            {"fn": "Movie.2024.1080p.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
            {"fn": "sample.mp4", "fid": "2", "fc": "1", "fs": 1_000},
            {"fn": "subtitle.ENG.srt", "fid": "3", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024.1080p",
            naming_metadata=None,
            metadata=attach_media_metadata({}, movie_contract()),
            storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Movies/中文电影 (English Movie)")
        self.assertIn("/Downloads/Release", storage.deleted)
        self.assertNotIn("/Downloads/Release/Movie.2024.1080p.mkv", storage.deleted)
        self.assertEqual(storage.moved[-1][1], "/Movies/中文电影 (English Movie)")

    def test_movie_video_and_subtitle_share_one_preflighted_plan(self):
        storage = FakeStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
            {"fn": "Subs/Movie.2024.CHS&ENG.sup", "fid": "2", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()),
            file_tree=[{
                "name": "Movie.2024.mkv",
                "relative_path": "Movie.2024.mkv",
                "is_dir": False,
                "size": 1_000_000,
            }, {
                "name": "Movie.2024.CHS&ENG.sup",
                "relative_path": "Subs/Movie.2024.CHS&ENG.sup",
                "is_dir": False,
                "size": 100,
            }],
            storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertEqual(storage.renamed, [
            ("/Downloads/Release/Movie.2024.mkv", "English Movie.mkv"),
            (
                "/Downloads/Release/Subs/Movie.2024.CHS&ENG.sup",
                "English Movie.chi.sup",
            ),
        ])
        self.assertEqual(
            [target for _source, target in storage.moved],
            [
                "/Movies/中文电影 (English Movie)",
                "/Movies/中文电影 (English Movie)",
            ],
        )

    def test_movie_subtitle_only_input_merges_into_canonical_target(self):
        storage = FakeStorage([
            {"fn": "Movie.2024.CHS.vtt", "fid": "1", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024.Subtitles",
            metadata=attach_media_metadata({}, movie_contract()), storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Movies/中文电影 (English Movie)")
        self.assertEqual(storage.renamed, [(
            "/Downloads/Release/Movie.2024.CHS.vtt",
            "English Movie.chi.vtt",
        )])
        self.assertEqual(storage.moved, [(
            "/Downloads/Release/English Movie.chi.vtt",
            "/Movies/中文电影 (English Movie)",
        )])

    def test_unknown_movie_subtitle_blocks_before_video_write(self):
        storage = FakeStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
            {"fn": "Movie.2024.srt", "fid": "2", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()), storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Downloads/Release")
        self.assertIn("字幕语言或归属无法确定", result.message)
        self.assertEqual(storage.created, [])
        self.assertEqual(storage.renamed, [])
        self.assertEqual(storage.moved, [])
        self.assertEqual(storage.deleted, [])

    def test_existing_different_subtitle_blocks_whole_movie_plan(self):
        storage = SubtitleTargetConflictStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
            {"fn": "Movie.2024.CHS.srt", "fid": "2", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()), storage=storage,
        )

        result = process_generic_media(event)

        self.assertEqual(result.final_path, "/Downloads/Release")
        self.assertIn("字幕目标冲突", result.message)
        self.assertEqual(storage.renamed, [])
        self.assertEqual(storage.moved, [])

    def test_identical_existing_subtitle_is_idempotently_skipped(self):
        storage = IdenticalSubtitleTargetStorage([
            {"fn": "Movie.2024.CHS.srt", "fid": "1", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024.Subtitles",
            metadata=attach_media_metadata({}, movie_contract()), storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Movies/中文电影 (English Movie)")
        self.assertEqual(storage.renamed, [])
        self.assertEqual(storage.moved, [])
        self.assertIn("/Downloads/Release", storage.deleted)

    def test_source_cleanup_failure_is_reported_as_incomplete(self):
        storage = CleanupFailureStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()), storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertTrue(result.message.startswith("⚠️"))
        self.assertIn("源目录清理未完成", result.message)

    @patch(
        "telepiplex_rename.processor.infer_movie_cleanup_plan_with_ai",
        create=True,
    )
    def test_movie_release_filename_precedes_ai_and_size(self, ai_mock):
        storage = FakeStorage([
            {"fn": "Movie.2024.1080p.mkv", "fid": "1", "fc": "1", "fs": 2_000},
            {"fn": "Movie.2024.720p.mkv", "fid": "2", "fc": "1", "fs": 8_000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()),
            release={"title": "Movie.2024.1080p"}, storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        ai_mock.assert_not_called()
        self.assertEqual(storage.renamed[0][0], "/Downloads/Release/Movie.2024.1080p.mkv")

    @patch(
        "telepiplex_rename.processor.infer_movie_cleanup_plan_with_ai",
        create=True,
    )
    def test_ambiguous_large_movie_candidates_are_decided_by_ai(self, ai_mock):
        from telepiplex_rename.context import runtime_context

        runtime_context.config["ai"] = {
            "enable": True,
            "api_url": "https://ai.example/v1",
            "api_key": "key",
            "model": "model",
        }
        ai_mock.return_value = {
            "main_video": "Movie.2024.1080p.mkv",
            "discard_files": ["Movie.2024.720p.mkv"],
            "reason": "release and resolution evidence",
        }
        storage = FakeStorage([
            {"fn": "Movie.2024.1080p.mkv", "fid": "1", "fc": "1", "fs": 2_000},
            {"fn": "Movie.2024.720p.mkv", "fid": "2", "fc": "1", "fs": 1_500},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()),
            release={"title": "Movie.2024.MULTI"}, storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        ai_mock.assert_called_once()
        context = ai_mock.call_args.args[0]
        self.assertEqual(context["release"]["title"], "Movie.2024.MULTI")
        self.assertEqual(storage.renamed[0][0], "/Downloads/Release/Movie.2024.1080p.mkv")

    def test_movie_target_conflict_moves_whole_release_to_unorganized(self):
        storage = TargetConflictStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 2_000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Movies", user_id=1,
            final_path="/Downloads/Release", resource_name="Movie.2024",
            metadata=attach_media_metadata({}, movie_contract()), storage=storage,
        )

        result = process_generic_media(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Unorganized/Release")
        self.assertEqual(storage.renamed, [])
        self.assertEqual(storage.moved, [("/Downloads/Release", "/Unorganized")])
        self.assertIn("冲突", result.message)

    @patch("telepiplex_rename.processor.infer_tvdb_episode_plan_with_ai")
    def test_normal_series_filename_mapping_precedes_ai_and_deletes_extra_video(self, ai_mock):
        storage = FakeStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1_000_000},
            {"fn": "sample.S00E99.mp4", "fid": "2", "fc": "1", "fs": 1_000},
            {"fn": "English.Series.S01E01.ENG.srt", "fid": "3", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release", resource_name="English.Series.S01E01",
            naming_metadata={
                "source": "confirmed", "chinese_title": "中文剧集",
                "english_title": "English Series", "release_title": "English.Series.S01E01",
            },
            metadata=attach_media_metadata({}, series_contract()),
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Series/中文剧集 (English Series)")
        ai_mock.assert_not_called()
        self.assertIn("/Downloads/Series.Release/sample.S00E99.mp4", storage.deleted)
        self.assertIn("/Downloads/Series.Release", storage.deleted)
        self.assertTrue(storage.moved[-1][1].endswith("English Series Season 01"))

    @patch("telepiplex_rename.processor.infer_tvdb_episode_plan_with_ai")
    def test_series_video_and_flat_subtitle_share_one_plan(self, ai_mock):
        storage = FakeStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "English.Series.S01E01.CHS&ENG.ass", "fid": "2", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release",
            resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()), storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        ai_mock.assert_not_called()
        self.assertEqual(storage.renamed, [
            (
                "/Downloads/Series.Release/English.Series.S01E01.mkv",
                "English Series S01E01.mkv",
            ),
            (
                "/Downloads/Series.Release/English.Series.S01E01.CHS&ENG.ass",
                "English Series S01E01.chi.ass",
            ),
        ])
        self.assertEqual(len(storage.moved), 2)
        self.assertTrue(all(
            target.endswith("/English Series Season 01")
            for _source, target in storage.moved
        ))

    def test_series_subtitle_only_input_is_organized(self):
        contract = series_contract()
        storage = FakeStorage([
            {"fn": "English.Series.S01E01.CHS.vtt", "fid": "1", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release",
            resource_name="English.Series.S01E01.Subtitles",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, contract), storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Series/中文剧集 (English Series)")
        self.assertEqual(storage.moved, [(
            "/Downloads/Series.Release/English Series S01E01.chi.vtt",
            "/Series/中文剧集 (English Series)/English Series Season 01",
        )])

    def test_partial_series_rename_keeps_canonical_video_as_anchor(self):
        target_root = "/Series/中文剧集 (English Series)"
        storage = FakeStorage([])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path=target_root,
            resource_name="中文剧集 (English Series)",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()),
            file_tree=[{
                "name": "English Series Season 01",
                "relative_path": "English Series Season 01",
                "path": f"{target_root}/English Series Season 01",
                "is_dir": True,
            }, {
                "name": "English Series S01E01.mkv",
                "relative_path": (
                    "English Series Season 01/"
                    "English Series S01E01.mkv"
                ),
                "path": (
                    f"{target_root}/English Series Season 01/"
                    "English Series S01E01.mkv"
                ),
                "is_dir": False,
                "size": 1000,
            }, {
                "name": "English.Series.S01E01.CHS.srt",
                "relative_path": "English.Series.S01E01.CHS.srt",
                "path": f"{target_root}/English.Series.S01E01.CHS.srt",
                "is_dir": False,
                "size": 100,
            }],
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertEqual(storage.renamed, [(
            f"{target_root}/English.Series.S01E01.CHS.srt",
            "English Series S01E01.chi.srt",
        )])
        self.assertEqual(storage.moved, [(
            f"{target_root}/English Series S01E01.chi.srt",
            f"{target_root}/English Series Season 01",
        )])
        self.assertNotIn(target_root, storage.deleted)

    def test_unknown_series_subtitle_blocks_before_any_write(self):
        storage = FakeStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "English.Series.S01E01.srt", "fid": "2", "fc": "1", "fs": 100},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release",
            resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()), storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertEqual(result.final_path, "/Downloads/Series.Release")
        self.assertIn("字幕语言或归属无法确定", result.message)
        self.assertEqual(storage.created, [])
        self.assertEqual(storage.renamed, [])
        self.assertEqual(storage.moved, [])
        self.assertEqual(storage.deleted, [])

    @patch("telepiplex_rename.processor.infer_tvdb_episode_plan_with_ai")
    def test_unknown_unscoped_subtitle_never_asks_ai_to_guess_language(
        self, ai_mock
    ):
        from telepiplex_rename.context import runtime_context

        runtime_context.config["ai"] = {
            "enable": True,
            "api_url": "https://ai.example/v1",
            "api_key": "secret",
            "model": "test",
        }
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release",
            resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()),
            file_tree=[{
                "name": "English.Series.S01E01.mkv",
                "relative_path": "English.Series.S01E01.mkv",
                "is_dir": False,
                "size": 1000,
            }, {
                "name": "subtitle.srt",
                "relative_path": "Subs/subtitle.srt",
                "is_dir": False,
                "size": 100,
            }],
            storage=FakeStorage([]),
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertIn("字幕语言或归属无法确定", result.message)
        ai_mock.assert_not_called()

    @patch("telepiplex_rename.processor.infer_tvdb_episode_plan_with_ai")
    def test_ai_only_backfills_unscoped_subtitle_episode_mapping(self, ai_mock):
        from telepiplex_rename.context import runtime_context

        runtime_context.config["ai"] = {
            "enable": True,
            "api_url": "https://ai.example/v1",
            "api_key": "secret",
            "model": "test",
        }
        ai_mock.return_value = {
            "episode_map": [],
            "subtitle_map": [{
                "source_file": "Subs/01.CHS.ass",
                "season_number": 1,
                "episode_number": 1,
            }],
            "warnings": [],
        }
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release",
            resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()),
            file_tree=[{
                "name": "English.Series.S01E01.mkv",
                "relative_path": "English.Series.S01E01.mkv",
                "is_dir": False,
                "size": 1000,
            }, {
                "name": "01.CHS.ass",
                "relative_path": "Subs/01.CHS.ass",
                "is_dir": False,
                "size": 100,
            }],
            storage=FakeStorage([]),
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        ai_mock.assert_called_once()
        self.assertIn("Subs/01.CHS.ass", {
            item["relative_path"]
            for item in ai_mock.call_args.args[0]["file_tree"]
            if not item.get("is_dir")
        })
        self.assertIn("English Series S01E01.chi.ass", {
            name for _path, name in event.storage.renamed
        })

    @patch("telepiplex_rename.processor.infer_tvdb_episode_plan_with_ai")
    def test_missing_confirmed_metadata_never_runs_legacy_identity_fallback(
        self, ai_mock
    ):
        from telepiplex_rename.context import runtime_context
        runtime_context.config["ai"] = {
            "enable": True,
            "api_url": "https://ai.example",
            "api_key": "secret",
            "model": "test",
        }
        event = DownloadCompletedEvent(
            link="magnet:?x",
            selected_path="/Series",
            user_id=1,
            final_path="/Downloads/Unknown.Series",
            resource_name="Unknown.Series.S01E01",
            naming_metadata={"english_title": "Unknown Series"},
            metadata={},
            storage=FakeStorage([
                {"fn": "Unknown.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
            ]),
        )

        result = process_tvdb_episode(event)

        self.assertFalse(result.handled)
        ai_mock.assert_not_called()

    @patch("telepiplex_rename.processor.infer_tvdb_episode_plan_with_ai")
    def test_series_mid_batch_failure_becomes_partial_business_result(self, ai_mock):
        contract = series_contract()
        contract["items"].append({
            "item_id": "e2", "content_role": "main_episode",
            "season_number": 1, "episode_number": 2,
        })
        storage = SecondMoveFailureStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "English.Series.S01E02.mkv", "fid": "2", "fc": "1", "fs": 1000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release", resource_name="English.Series.S01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, contract), storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.handled)
        self.assertTrue(result.message.startswith("⚠️"))
        self.assertIn("部分完成（1/2）", result.message)
        ai_mock.assert_not_called()

    def test_series_extra_video_cleanup_failure_is_not_reported_as_success(self):
        storage = ExtraVideoDeleteFailureStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "sample.mp4", "fid": "2", "fc": "1", "fs": 10},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release", resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()), storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.message.startswith("⚠️"))
        self.assertIn("部分完成（1/2）", result.message)

    @patch("telepiplex_rename.processor.infer_tvdb_episode_plan_with_ai")
    def test_unmatched_large_series_video_requires_explicit_ai_discard(
        self, ai_mock
    ):
        from telepiplex_rename.context import runtime_context

        runtime_context.config["selection"].update({
            "unmatched_large_ratio": 0.25,
            "unmatched_large_min_bytes": 0,
        })
        ai_mock.return_value = {
            "episode_map": [{
                "source_file": "English.Series.S01E01.mkv",
                "season_number": 1,
                "episode_number": 1,
            }],
            "discard_files": ["English.Series.S01E01.720p.mkv"],
            "warnings": [],
        }
        storage = FakeStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "English.Series.S01E01.720p.mkv", "fid": "2", "fc": "1", "fs": 800},
        ])
        contract = series_contract()
        contract["identity"]["external_ids"] = {"tvdb": "100"}
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release",
            resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, contract),
            release={"title": "English.Series.S01E01.MULTI"},
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.message.startswith("✅"))
        ai_mock.assert_called_once()
        context = ai_mock.call_args.args[0]
        self.assertEqual(context["locked_episode_keys"], [[1, 1]])
        self.assertEqual(context["tvdb_candidates"][0]["tvdb_series_id"], "100")
        self.assertIn(
            "/Downloads/Series.Release/English.Series.S01E01.720p.mkv",
            storage.deleted,
        )

    def test_series_target_conflict_moves_whole_release_before_any_rename(self):
        storage = SeriesTargetConflictStorage([
            {"fn": "English.Series.S01E01.mkv", "fid": "1", "fc": "1", "fs": 1000},
        ])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/Series.Release",
            resource_name="English.Series.S01E01",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()),
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertEqual(result.final_path, "/Unorganized/Series.Release")
        self.assertEqual(storage.renamed, [])
        self.assertEqual(
            storage.moved,
            [("/Downloads/Series.Release", "/Unorganized")],
        )

    def test_single_file_download_root_uses_absolute_tree_path_without_false_cleanup_failure(self):
        storage = FakeStorage([])
        event = DownloadCompletedEvent(
            link="magnet:?x", selected_path="/Series", user_id=1,
            final_path="/Downloads/English.Series.S01E01.mkv",
            download_root="/Downloads/English.Series.S01E01.mkv",
            resource_name="English.Series.S01E01.mkv",
            naming_metadata={"english_title": "English Series"},
            metadata=attach_media_metadata({}, series_contract()),
            file_tree=[{
                "name": "English.Series.S01E01.mkv",
                "relative_path": "English.Series.S01E01.mkv",
                "path": "/Downloads/English.Series.S01E01.mkv",
                "is_dir": False,
                "size": 1000,
            }],
            storage=storage,
        )

        result = process_tvdb_episode(event)

        self.assertTrue(result.message.startswith("✅"))
        self.assertNotIn(
            "/Downloads/English.Series.S01E01.mkv",
            storage.deleted,
        )


class FakeHost:
    def __init__(self, storage=None):
        self.storage = storage or FakeStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1000},
            {"fn": "sample.mp4", "fid": "2", "fc": "1", "fs": 1},
        ])
        self.events = []
        self.notifications = []
        self.reports = []
        self.milestones = []
        self.timeline = []
        self.fail_notification = False

    async def call_capability(self, capability, method, payload, **_kwargs):
        self.assert_capability = capability
        if capability == "media.search":
            self.metadata_payload = payload
            self.metadata_query = payload["query"]
            return {
                "status": "resolved",
                "media_metadata": movie_contract(),
                "naming_metadata": {
                    "source": "search",
                    "media_type": "movie",
                    "chinese_title": "中文电影",
                    "english_title": "English Movie",
                    "year": "2024",
                },
                "presentation": {
                    "milestone_id": "media-movie-2024",
                    "text": "🎬 中文电影 (English Movie)",
                    "photo_url": "https://img.example/movie.jpg",
                },
            }
        value = getattr(self.storage, method)(*(payload.get("args") or []), **(payload.get("kwargs") or {}))
        return {"value": value}

    async def publish_event(self, event_type, payload, **kwargs):
        self.timeline.append(("event", event_type))
        self.events.append((event_type, payload, kwargs))
        return {"event_id": "organized-1"}

    async def notify_user(self, user_id, text, **kwargs):
        if self.fail_notification:
            raise RuntimeError("notification unavailable")
        self.notifications.append((user_id, text, kwargs))
        return {"accepted": True}

    async def report_operation(self, operation):
        self.timeline.append(
            ("report", operation["state"], operation["stage"])
        )
        self.reports.append(operation)
        return {"accepted": True, "revision": operation["revision"]}

    async def publish_operation_milestone(
        self,
        operation_id,
        milestone_id,
        text,
        *,
        photo_url="",
        mode="identity",
        deadline=10,
    ):
        self.timeline.append(("milestone", mode, milestone_id))
        self.milestones.append({
            "operation_id": operation_id,
            "milestone_id": milestone_id,
            "text": text,
            "photo_url": photo_url,
            "deadline": deadline,
            "mode": mode,
        })
        return {"accepted": True, "duplicate": False}

    async def seal_operation_stage(
        self,
        operation_id,
        milestone_id,
        text,
        *,
        deadline=10,
    ):
        return await self.publish_operation_milestone(
            operation_id,
            milestone_id,
            text,
            mode="stage",
            deadline=deadline,
        )


class FakeRuntime:
    def __init__(self):
        self.tasks = {}

    def spawn(self, awaitable, *, task_id):
        task = asyncio.create_task(awaitable, name=task_id)
        self.tasks[task_id] = task
        return task

    async def wait(self):
        tasks = list(self.tasks.values())
        self.tasks.clear()
        if tasks:
            await asyncio.gather(*tasks)


class RenameFeatureTest(unittest.IsolatedAsyncioTestCase):
    def test_confirmed_failure_already_in_unorganized_stays_in_place(self):
        from telepiplex_rename.context import runtime_context
        from telepiplex_rename.processor import (
            _move_confirmed_failure_to_unorganized,
        )

        runtime_context.configure({
            "media": {"unorganized_path": "/未整理"},
            "selection": {},
            "ai": {},
            "metadata": {},
        })
        storage = FakeStorage([])
        event = DownloadCompletedEvent(
            link="",
            selected_path="/真人电影",
            user_id=123,
            final_path="/未整理/Raw.Release",
            resource_name="Raw.Release",
            storage=storage,
        )

        self.assertEqual(
            _move_confirmed_failure_to_unorganized(event),
            "/未整理/Raw.Release",
        )
        self.assertEqual(storage.moved, [])

    async def test_inventory_confirm_routes_unorganized_movie_and_completes_batch(self):
        from telepiplex_rename.jobs import RenameJobStore

        class InventoryMovieStorage(FakeStorage):
            def __init__(self):
                super().__init__([])

            def get_file_list(self, params):
                if params.get("cid") == "root-unorganized":
                    return [{
                        "file_id": "raw-movie-1",
                        "name": "Movie.2024.1080p",
                        "is_dir": True,
                    }]
                if params.get("cid") == "raw-movie-1":
                    return [{
                        "name": "Movie.2024.mkv",
                        "file_id": "movie-video-1",
                        "is_dir": False,
                        "size": 1000,
                    }]
                return []

            def get_file_info(self, path):
                if path == "/未整理":
                    return {"file_id": "root-unorganized", "file_category": "0"}
                return super().get_file_info(path)

        config = {
            "category_folder": [{
                "kind": "live_action_movie",
                "name": "真人电影",
                "path": "/真人电影",
                "plex_library_id": "",
            }, {
                "kind": "animated_movie",
                "name": "动画电影",
                "path": "/动画电影",
                "plex_library_id": "",
            }, {
                "kind": "live_action_series",
                "name": "真人剧集",
                "path": "/真人剧集",
                "plex_library_id": "",
            }, {
                "kind": "animated_series",
                "name": "动画剧集",
                "path": "/动画剧集",
                "plex_library_id": "",
            }],
            "unorganized_path": "/未整理",
            "storage_timeout": 3,
            "metadata_timeout": 3,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            host = FakeHost(InventoryMovieStorage())
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            feature = RenameFeature(config=config, host=host, jobs=jobs)
            runtime = FakeRuntime()
            feature.bind_runtime(runtime)
            owner = {"chat_id": 10, "user_id": 123}

            await feature.command({**owner, "command": "rename", "args": []})
            await feature.callback({
                **owner,
                "payload": "inventory:root:4",
            })
            await runtime.wait()
            started = await feature.callback({
                **owner,
                "payload": "inventory:confirm",
            })
            self.assertEqual(started["operation"]["stage"], "inventory_batch")
            await runtime.wait()

            self.assertEqual(
                host.metadata_payload["probe"]["content_shape"],
                "movie",
            )
            self.assertEqual(
                host.storage.created[-1],
                "/真人电影/中文电影 (English Movie)",
            )
            self.assertEqual(len(host.events), 1)
            self.assertEqual(
                host.events[0][2]["idempotency_key"],
                "inventory:raw-movie-1:organized:"
                f"{started['operation']['operation_id']}",
            )
            self.assertEqual(
                host.events[0][1]["final_path"],
                "/真人电影/中文电影 (English Movie)",
            )
            self.assertEqual(host.reports[-1]["state"], "completed")
            self.assertIn("成功：1", host.reports[-1]["status_text"])
            self.assertTrue(
                jobs.get("inventory:raw-movie-1")["result"]["organized"]
            )

    async def test_inventory_wraps_root_video_and_uses_portable_target_names(self):
        from telepiplex_rename.jobs import RenameJobStore

        class LooseVideoStorage(FakeStorage):
            def __init__(self):
                super().__init__([])

            def get_file_info(self, path):
                if path == "/真人电影":
                    return {"file_id": "root-movies", "file_category": "0"}
                return super().get_file_info(path)

            def get_file_list(self, params):
                if params.get("cid") == "root-movies":
                    return [{
                        "file_id": "loose-video-1",
                        "name": "Loose:<Movie>?.mkv",
                        "is_dir": False,
                        "size": 1000,
                    }]
                return []

        class PortableNameHost(FakeHost):
            async def call_capability(
                self, capability, method, payload, **kwargs
            ):
                if capability == "media.search" and method == "resolve_metadata":
                    contract = movie_contract()
                    contract["identity"].update({
                        "chinese_title": "设<备>：名?",
                        "english_title": "CON",
                    })
                    self.metadata_payload = payload
                    return {
                        "status": "resolved",
                        "media_metadata": contract,
                        "naming_metadata": {
                            "source": "search",
                            "media_type": "movie",
                            "chinese_title": "设<备>：名?",
                            "english_title": "CON",
                            "year": "2024",
                        },
                    }
                return await super().call_capability(
                    capability, method, payload, **kwargs
                )

        config = {
            "category_folder": [{
                "kind": "live_action_movie",
                "name": "真人电影",
                "path": "/真人电影",
                "plex_library_id": "",
            }],
            "unorganized_path": "/未整理",
            "storage_timeout": 3,
            "metadata_timeout": 3,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LooseVideoStorage()
            host = PortableNameHost(storage)
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            feature = RenameFeature(config=config, host=host, jobs=jobs)
            runtime = FakeRuntime()
            feature.bind_runtime(runtime)
            owner = {"chat_id": 10, "user_id": 123}

            await feature.command({**owner, "command": "rename", "args": []})
            await feature.callback({**owner, "payload": "inventory:root:0"})
            await runtime.wait()

            preview = host.reports[-1]
            self.assertEqual(
                preview["details"]["counts"],
                {"pending": 1, "completed": 0},
            )

            started = await feature.callback({
                **owner,
                "payload": "inventory:confirm",
            })
            await runtime.wait()

            target_dir = "/真人电影/设备 名 (CON_)"
            self.assertEqual(storage.created[-1], target_dir)
            self.assertEqual(
                storage.renamed,
                [("/真人电影/Loose:<Movie>?.mkv", "CON_.mkv")],
            )
            self.assertEqual(
                storage.moved,
                [("/真人电影/CON_.mkv", target_dir)],
            )
            self.assertEqual(host.events[0][1]["final_path"], target_dir)
            self.assertEqual(host.reports[-1]["state"], "completed")
            self.assertTrue(
                jobs.get("inventory:loose-video-1")["result"]["organized"]
            )
            self.assertEqual(
                host.events[0][2]["idempotency_key"],
                "inventory:loose-video-1:organized:"
                f"{started['operation']['operation_id']}",
            )

    async def test_inventory_batch_pauses_for_metadata_and_resumes_serially(self):
        from telepiplex_rename.jobs import RenameJobStore

        class InventoryStorage(FakeStorage):
            def __init__(self):
                super().__init__([])

            def get_file_info(self, path):
                if path == "/真人电影":
                    return {"file_id": "root-movies", "file_category": "0"}
                return super().get_file_info(path)

            def get_file_list(self, params):
                cid = params.get("cid")
                if cid == "root-movies":
                    return [{
                        "file_id": "ambiguous-1",
                        "name": "Ambiguous.2024",
                        "is_dir": True,
                    }, {
                        "file_id": "resolved-2",
                        "name": "Resolved.2024",
                        "is_dir": True,
                    }]
                names = {
                    "ambiguous-1": "Ambiguous.2024",
                    "resolved-2": "Resolved.2024",
                }
                if cid in names:
                    name = names[cid]
                    return [{
                        "name": f"{name}.Source.1080p.mkv",
                        "file_id": f"video-{name}",
                        "is_dir": False,
                        "size": 1000,
                    }]
                return []

        class AmbiguousFirstHost(FakeHost):
            def __init__(self, storage):
                super().__init__(storage)
                self.resolve_calls = 0

            async def call_capability(
                self, capability, method, payload, **kwargs
            ):
                if capability == "media.search" and method == "resolve_metadata":
                    self.resolve_calls += 1
                    if self.resolve_calls == 1:
                        return {
                            "status": "confirmation_required",
                            "query": payload["query"],
                            "probe": payload["probe"],
                            "candidates": [{
                                "ref": "douban:inventory-1",
                                "title": "中文电影",
                                "original_title": "English Movie",
                                "year": "2024",
                                "countries": ["美国"],
                                "media_type": "movie",
                            }],
                        }
                if capability == "media.search" and method == "confirm_metadata":
                    return {
                        "status": "resolved",
                        "media_metadata": movie_contract(),
                        "naming_metadata": {
                            "source": "search",
                            "media_type": "movie",
                            "chinese_title": "中文电影",
                            "english_title": "English Movie",
                            "year": "2024",
                        },
                    }
                return await super().call_capability(
                    capability, method, payload, **kwargs
                )

        config = {
            "category_folder": [{
                "kind": "live_action_movie",
                "name": "真人电影",
                "path": "/真人电影",
                "plex_library_id": "",
            }, {
                "kind": "animated_movie",
                "name": "动画电影",
                "path": "/动画电影",
                "plex_library_id": "",
            }, {
                "kind": "live_action_series",
                "name": "真人剧集",
                "path": "/真人剧集",
                "plex_library_id": "",
            }, {
                "kind": "animated_series",
                "name": "动画剧集",
                "path": "/动画剧集",
                "plex_library_id": "",
            }],
            "unorganized_path": "/未整理",
            "storage_timeout": 3,
            "metadata_timeout": 3,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            host = AmbiguousFirstHost(InventoryStorage())
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            feature = RenameFeature(config=config, host=host, jobs=jobs)
            runtime = FakeRuntime()
            feature.bind_runtime(runtime)
            owner = {"chat_id": 10, "user_id": 123}

            await feature.command({**owner, "command": "rename", "args": []})
            await feature.callback({**owner, "payload": "inventory:root:0"})
            await runtime.wait()
            await feature.callback({**owner, "payload": "inventory:confirm"})
            await runtime.wait()

            waiting = host.reports[-1]
            self.assertEqual(waiting["state"], "awaiting_input")
            self.assertEqual(waiting["stage"], "metadata_confirmation")
            self.assertEqual(len(host.events), 0)
            callback_data = waiting["details"]["keyboard"][0][0][
                "callback_data"
            ]

            await feature.callback({
                **owner,
                "payload": callback_data.split("rename:", 1)[1],
            })
            await runtime.wait()

            self.assertEqual(host.resolve_calls, 2)
            self.assertEqual(len(host.events), 2)
            self.assertEqual(host.reports[-1]["state"], "completed")
            self.assertIn("成功：2", host.reports[-1]["status_text"])
            self.assertEqual(
                jobs.get("inventory:ambiguous-1")["state"], "completed"
            )
            self.assertEqual(
                jobs.get("inventory:resolved-2")["state"], "completed"
            )

    async def test_inventory_command_scans_one_selected_root_and_previews_direct_children(self):
        class InventoryStorage(FakeStorage):
            def __init__(self):
                super().__init__([])
                self.list_calls = []

            def get_file_info(self, path):
                if path == "/真人剧集":
                    return {"file_id": "root-series", "file_category": "0"}
                return None

            def get_file_list(self, params):
                self.list_calls.append(dict(params))
                cid = params.get("cid")
                if cid == "root-series":
                    return [{
                        "file_id": "organized-1",
                        "name": "白宫杀人事件 (The Residence)",
                        "is_dir": True,
                    }, {
                        "file_id": "raw-1",
                        "name": "Veep.2012.S01-S07.1080p",
                        "is_dir": True,
                    }, {
                        "file_id": "empty-1",
                        "name": "Empty.Release",
                        "is_dir": True,
                    }]
                if cid == "organized-1":
                    return [{
                        "name": "The Residence Season 01",
                        "is_dir": True,
                        "file_id": "organized-season-1",
                    }]
                if cid == "organized-season-1":
                    return [{
                        "name": "The Residence S01E01.mkv",
                        "is_dir": False,
                        "file_id": "video-organized",
                    }]
                if cid == "raw-1":
                    return [{
                        "name": "Season 1",
                        "is_dir": True,
                        "file_id": "raw-season-1",
                    }]
                if cid == "raw-season-1":
                    return [{
                        "name": "Veep.S01E01.mkv",
                        "is_dir": False,
                        "file_id": "video-raw",
                    }]
                if cid == "empty-1":
                    return [{
                        "name": "poster.jpg",
                        "is_dir": False,
                        "file_id": "poster",
                    }]
                return []

        host = FakeHost(InventoryStorage())
        feature = RenameFeature(
            config={
                "category_folder": [{
                    "kind": "live_action_movie",
                    "name": "真人电影",
                    "path": "/真人电影",
                    "plex_library_id": "",
                }, {
                    "kind": "animated_movie",
                    "name": "动画电影",
                    "path": "/动画电影",
                    "plex_library_id": "",
                }, {
                    "kind": "live_action_series",
                    "name": "真人剧集",
                    "path": "/真人剧集",
                    "plex_library_id": "",
                }, {
                    "kind": "animated_series",
                    "name": "动画剧集",
                    "path": "/动画剧集",
                    "plex_library_id": "",
                }],
                "unorganized_path": "/未整理",
                "storage_timeout": 3,
            },
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        owner = {"chat_id": 10, "user_id": 123}

        menu = await feature.command({
            **owner,
            "command": "rename",
            "args": [],
        })
        self.assertEqual(
            [row[0]["text"] for row in menu["actions"][0]["data"]["keyboard"]],
            ["真人电影", "动画电影", "真人剧集", "动画剧集", "未整理", "退出"],
        )

        scanning = await feature.callback({
            **owner,
            "payload": "inventory:root:2",
        })
        self.assertEqual(scanning["operation"]["stage"], "inventory_scan")
        await runtime.wait()

        preview = host.reports[-1]
        self.assertEqual(preview["state"], "awaiting_input")
        self.assertEqual(preview["stage"], "inventory_confirmation")
        self.assertIn("未完成：2", preview["status_text"])
        self.assertIn("已完成：1", preview["status_text"])
        self.assertEqual(
            preview["details"]["counts"],
            {"pending": 2, "completed": 1},
        )
        self.assertEqual(
            [call["cid"] for call in host.storage.list_calls],
            [
                "root-series",
                "organized-1",
                "organized-season-1",
                "raw-1",
                "raw-season-1",
                "empty-1",
            ],
        )
        self.assertTrue(all(
            call["offset"] == 0 and call["limit"] == 1000
            for call in host.storage.list_calls
        ))
        self.assertEqual(
            preview["details"]["keyboard"][0][0]["callback_data"],
            "rename:inventory:confirm",
        )

    async def test_inventory_classification_uses_live_structure_in_unorganized(self):
        from telepiplex_rename.jobs import RenameJobStore

        class InventoryStorage(FakeStorage):
            def __init__(self):
                super().__init__([])

            def get_file_info(self, path):
                if path == "/未整理":
                    return {"file_id": "root-unorganized", "file_category": "0"}
                return None

            def get_file_list(self, params):
                cid = params.get("cid")
                if cid == "root-unorganized":
                    return [{
                        "file_id": "normalized-1",
                        "name": "中文电影 (English Movie)",
                        "is_dir": True,
                    }, {
                        "file_id": "raw-1",
                        "name": "Raw.Movie.2024.1080p",
                        "is_dir": True,
                    }, {
                        "file_id": "empty-1",
                        "name": "Empty.Release",
                        "is_dir": True,
                    }]
                if cid == "normalized-1":
                    return [{
                        "name": "English Movie.mkv",
                        "is_dir": False,
                        "file_id": "normalized-video",
                    }]
                if cid == "raw-1":
                    return [{
                        "name": "Raw.Movie.2024.Source.mkv",
                        "is_dir": False,
                        "file_id": "raw-video",
                    }]
                if cid == "empty-1":
                    return [{
                        "name": "poster.jpg",
                        "is_dir": False,
                        "file_id": "poster",
                    }]
                return []

        with tempfile.TemporaryDirectory() as tmpdir:
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            self.assertTrue(jobs.claim("inventory:raw-1"))
            jobs.update(
                "inventory:raw-1",
                "completed",
                {"organized": True},
            )
            host = FakeHost(InventoryStorage())
            feature = RenameFeature(
                config={
                    "category_folder": [],
                    "unorganized_path": "/未整理",
                    "storage_timeout": 3,
                },
                host=host,
                jobs=jobs,
            )
            runtime = FakeRuntime()
            feature.bind_runtime(runtime)
            owner = {"chat_id": 10, "user_id": 123}

            await feature.command({**owner, "command": "rename", "args": []})
            await feature.callback({**owner, "payload": "inventory:root:0"})
            await runtime.wait()

            preview = host.reports[-1]
            self.assertEqual(
                preview["details"]["counts"],
                {"pending": 2, "completed": 1},
            )
            self.assertEqual(
                [
                    item["resource_name"]
                    for item in feature.inventory_sessions[(10, 123)]["pending"]
                ],
                ["Raw.Movie.2024.1080p", "Empty.Release"],
            )

    async def test_inventory_scan_paginates_past_one_thousand_tree_nodes(self):
        class GuardrailStorage(FakeStorage):
            def __init__(self):
                super().__init__([])
                self.offsets = []

            def get_file_info(self, path):
                return {"file_id": "root-series", "file_category": "0"}

            def get_file_list(self, params):
                cid = params.get("cid")
                if cid == "root-series":
                    return [{
                        "file_id": "large-1",
                        "name": "Large.Release",
                        "is_dir": True,
                    }]
                if cid != "large-1":
                    return []
                offset = int(params.get("offset") or 0)
                self.offsets.append(offset)
                if offset == 0:
                    return [{
                        "name": f"poster-{index}.jpg",
                        "is_dir": False,
                        "file_id": f"poster-{index}",
                    } for index in range(1000)]
                if offset == 1000:
                    return [{
                        "name": "Movie.Source.mkv",
                        "is_dir": False,
                        "file_id": "movie-video",
                    }]
                return []

        host = FakeHost(GuardrailStorage())
        feature = RenameFeature(
            config={
                "category_folder": [{
                    "kind": "live_action_series",
                    "name": "真人剧集",
                    "path": "/真人剧集",
                    "plex_library_id": "",
                }],
                "unorganized_path": "/未整理",
                "storage_timeout": 3,
            },
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        owner = {"chat_id": 10, "user_id": 123}

        await feature.command({**owner, "command": "rename", "args": []})
        await feature.callback({**owner, "payload": "inventory:root:0"})
        await runtime.wait()

        self.assertEqual(host.reports[-1]["state"], "awaiting_input")
        self.assertEqual(
            host.reports[-1]["details"]["counts"],
            {"pending": 1, "completed": 0},
        )
        self.assertEqual(host.storage.offsets, [0, 1000])

    async def test_ambiguous_magnet_with_colon_job_id_resumes_same_job(self):
        from telepiplex_rename.jobs import RenameJobStore

        class AmbiguousHost(FakeHost):
            async def call_capability(
                self, capability, method, payload, **kwargs
            ):
                if capability == "media.search" and method == "resolve_metadata":
                    return {
                        "status": "confirmation_required",
                        "query": payload["query"],
                        "probe": payload["probe"],
                        "candidates": [{
                            "ref": "douban:1",
                            "title": "中文电影甲",
                            "original_title": "English Movie A",
                            "year": "2024",
                            "countries": ["美国"],
                            "media_type": "movie",
                            "poster_url": "https://img.example/a.jpg",
                        }, {
                            "ref": "douban:2",
                            "title": "中文电影乙",
                            "original_title": "English Movie B",
                            "year": "2024",
                            "countries": ["英国"],
                            "media_type": "movie",
                            "poster_url": "https://img.example/b.jpg",
                        }],
                    }
                if capability == "media.search" and method == "confirm_metadata":
                    self.confirm_payload = payload
                    return {
                        "status": "resolved",
                        "media_metadata": movie_contract(),
                        "naming_metadata": {
                            "source": "search",
                            "media_type": "movie",
                            "chinese_title": "中文电影乙",
                            "english_title": "English Movie B",
                            "year": "2024",
                        },
                        "presentation": {
                            "milestone_id": "media-confirmed-b",
                            "text": "🎬 中文电影乙 (English Movie B)",
                            "photo_url": "https://img.example/b.jpg",
                        },
                    }
                return await super().call_capability(
                    capability, method, payload, **kwargs
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            host = AmbiguousHost()
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            feature = RenameFeature(
                config={
                    "unorganized_path": "/Unorganized",
                    "storage_timeout": 3,
                },
                host=host,
                jobs=jobs,
            )
            runtime = FakeRuntime()
            feature.bind_runtime(runtime)

            await feature.download_completed({
                "event_id": "event-ambiguous",
                "payload": {
                    "job_id": "telegram:219358366",
                    "selected_path": "/Movies",
                    "user_id": 123,
                    "chat_id": 10,
                    "download_root": "/Downloads/Movie.2024.mkv",
                    "final_path": "/Downloads/Movie.2024.mkv",
                    "resource_name": "Movie.2024.mkv",
                    "operation_id": "op-ambiguous",
                    "operation_revision": 2,
                    "file_tree": [{
                        "name": "Movie.2024.mkv",
                        "relative_path": "Movie.2024.mkv",
                        "path": "/Downloads/Movie.2024.mkv",
                        "is_dir": False,
                        "size": 1000,
                    }],
                },
            })
            await runtime.wait()

            waiting = jobs.get("telegram:219358366")
            self.assertEqual(waiting["state"], "awaiting_metadata")
            self.assertEqual(host.storage.renamed, [])
            self.assertEqual(host.reports[-1]["state"], "awaiting_input")
            callback_data = host.reports[-1]["details"]["keyboard"][1][0][
                "callback_data"
            ]

            resumed = await feature.callback({
                "payload": callback_data.split("rename:", 1)[1],
                "chat_id": 10,
                "user_id": 123,
            })
            self.assertEqual(resumed["operation"]["state"], "running")
            await runtime.wait()

            self.assertEqual(host.confirm_payload["candidate_ref"], "douban:2")
            identity_milestones = [
                item
                for item in host.milestones
                if item["mode"] == "identity"
            ]
            self.assertEqual(len(identity_milestones), 1)
            self.assertEqual(
                host.storage.renamed[0][0],
                "/Downloads/Movie.2024.mkv",
            )
            self.assertEqual(
                jobs.get("telegram:219358366")["state"],
                "completed",
            )

    async def test_resume_durable_job_defers_transient_failure(self):
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized"},
            host=FakeHost(),
        )

        async def fail(*_args, **_kwargs):
            raise RuntimeError("handoff temporarily unavailable")

        feature._finish_operation = fail

        await feature._resume_durable_job({
            "job_id": "job-retry-later",
            "state": "processed",
            "result": {"event_payload": {}},
        })

    async def test_resume_durable_inventory_job_requires_a_fresh_scan(self):
        from telepiplex_rename.jobs import RenameJobStore

        with tempfile.TemporaryDirectory() as tmpdir:
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            jobs.claim("inventory:restart-1")
            jobs.update("inventory:restart-1", "processed", {
                "organized": True,
                "inventory_batch_id": "batch-before-restart",
                "event_payload": {
                    "job_id": "inventory:restart-1",
                    "final_path": "/真人电影/Movie",
                },
            })
            feature = RenameFeature(
                config={"unorganized_path": "/未整理"},
                host=FakeHost(),
                jobs=jobs,
            )

            await feature._resume_durable_job(
                jobs.get("inventory:restart-1")
            )

            stored = jobs.get("inventory:restart-1")
            self.assertEqual(stored["state"], "failed")
            self.assertIn("重新执行 /rename", stored["result"]["message"])

    async def test_unresolved_media_search_moves_release_to_unorganized(self):
        class UnresolvedHost(FakeHost):
            async def call_capability(self, capability, method, payload, **kwargs):
                if capability == "media.search":
                    self.metadata_query = payload["query"]
                    return {}
                return await super().call_capability(
                    capability, method, payload, **kwargs
                )

        host = UnresolvedHost()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-unresolved",
            "payload": {
                "job_id": "job-unresolved",
                "selected_path": "/Movies",
                "user_id": 123,
                "final_path": "/Downloads/Unknown.Release",
                "resource_name": "Unknown.Release.2024",
            },
        })
        await runtime.wait()

        self.assertEqual(host.storage.renamed, [])
        self.assertEqual(
            host.storage.moved,
            [("/Downloads/Unknown.Release", "/Unorganized")],
        )
        self.assertIn("无法确定整理规则", host.notifications[-1][1])

    async def test_media_search_failure_stops_before_storage_and_reports_envelope(self):
        from telepiplex_plugin_sdk import FeatureError

        class FailedSearchHost(FakeHost):
            async def call_capability(self, capability, method, payload, **kwargs):
                if capability == "media.search":
                    raise FeatureError(
                        "metadata_source_unavailable",
                        "metadata providers are temporarily unavailable",
                    )
                return await super().call_capability(
                    capability, method, payload, **kwargs
                )

        host = FailedSearchHost()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-metadata-failure",
            "payload": {
                "job_id": "job-metadata-failure",
                "selected_path": "/Series",
                "user_id": 123,
                "chat_id": 10,
                "final_path": "/Downloads/Honey.and.Clover",
                "resource_name": "Honey.and.Clover",
                "operation_id": "op-metadata-failure",
                "operation_revision": 2,
            },
        })
        await runtime.wait()

        self.assertEqual(host.storage.moved, [])
        self.assertEqual(host.storage.renamed, [])
        self.assertEqual(host.reports[-1]["state"], "failed")
        self.assertEqual(
            host.reports[-1]["details"],
            {
                "error_code": "metadata_source_unavailable",
                "error_stage": "metadata_resolution",
                "error_detail": "metadata providers are temporarily unavailable",
                "retryable": True,
                "stopped_at": "metadata_resolution",
            },
        )
        self.assertIn("元数据解析失败", host.notifications[-1][1])
        self.assertIn("metadata_source_unavailable", host.notifications[-1][1])

    async def test_unorganized_fallback_reports_copied_source_retained(self):
        host = FakeHost(CopiedSourceRetainedStorage([]))
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized"},
            host=host,
        )
        event = DownloadCompletedEvent(
            link="magnet:?x",
            selected_path="/Series",
            user_id=123,
            final_path="/Downloads/Unknown.Release",
            resource_name="Unknown.Release",
            metadata={},
            storage=host.storage,
        )

        result = feature._fallback_unorganized(event)

        self.assertTrue(result.handled)
        self.assertEqual(result.final_path, "/Unorganized/Unknown.Release")
        self.assertIn("复制到未整理", result.message)
        self.assertIn("源文件仍保留", result.message)

    async def test_rollback_is_reported_and_compensation_runs_once(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingJournal:
            can_rollback = True
            inverses = [SimpleNamespace(target_path="/Downloads/renamed.mkv")]
            calls = 0

            async def rollback(self, _host, *, deadline):
                self.calls += 1
                entered.set()
                await release.wait()
                return {
                    "state": "rolled_back",
                    "restored": ["/Downloads/original.mkv"],
                    "remaining": [],
                }

        host = FakeHost()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        journal = BlockingJournal()
        feature.operations["op-rollback"] = {
            "operation_id": "op-rollback",
            "chat_id": 10,
            "user_id": 123,
            "state": "running",
            "stage": "rename",
            "status_text": "正在重命名",
            "control": "rollback",
            "revision": 3,
            "details": {},
            "journal": journal,
            "cancel_event": SimpleNamespace(set=lambda: None),
        }

        first = await feature.operation_control({
            "operation_id": "op-rollback",
            "action": "rollback",
            "revision": 3,
        })
        await entered.wait()
        repeated = await feature.operation_control({
            "operation_id": "op-rollback",
            "action": "rollback",
            "revision": 4,
        })
        release.set()
        await runtime.wait()

        self.assertEqual(repeated["operation"]["state"], "rolling_back")
        self.assertEqual(first["operation"]["state"], "rolling_back")
        self.assertEqual(
            feature.operations["op-rollback"]["state"], "rolled_back"
        )
        self.assertEqual(journal.calls, 1)
        self.assertEqual(
            [report["state"] for report in host.reports],
            ["rolling_back", "rolled_back"],
        )

    async def test_rollback_waits_for_forward_task_safe_stop(self):
        forward_release = asyncio.Event()
        rollback_started = asyncio.Event()

        class Journal:
            can_rollback = True
            inverses = []

            async def rollback(self, _host, *, deadline):
                rollback_started.set()
                return {"state": "rolled_back", "restored": [], "remaining": []}

        async def forward():
            await forward_release.wait()

        host = FakeHost()
        runtime = FakeRuntime()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        feature.bind_runtime(runtime)
        forward_task = asyncio.create_task(forward())
        feature.operations["op-forward-stop"] = {
            "operation_id": "op-forward-stop",
            "chat_id": 10,
            "user_id": 123,
            "state": "running",
            "stage": "rename",
            "status_text": "正在重命名",
            "control": "rollback",
            "revision": 3,
            "details": {},
            "journal": Journal(),
            "task": forward_task,
            "cancel_event": SimpleNamespace(set=lambda: None),
        }

        accepted = await feature.operation_control({
            "operation_id": "op-forward-stop",
            "action": "rollback",
            "revision": 3,
        })
        await asyncio.sleep(0)

        self.assertEqual(accepted["operation"]["state"], "rolling_back")
        self.assertFalse(rollback_started.is_set())
        forward_release.set()
        await runtime.wait()
        self.assertTrue(rollback_started.is_set())
        self.assertEqual(feature.operations["op-forward-stop"]["state"], "rolled_back")

    async def test_runtime_shutdown_does_not_start_pending_compensation(self):
        forward_release = asyncio.Event()

        class Journal:
            can_rollback = True
            inverses = []
            calls = 0

            async def rollback(self, _host, *, deadline):
                self.calls += 1
                return {"state": "rolled_back", "restored": [], "remaining": []}

        async def forward():
            await forward_release.wait()

        host = FakeHost()
        runtime = FakeRuntime()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        feature.bind_runtime(runtime)
        journal = Journal()
        forward_task = asyncio.create_task(forward())
        feature.operations["op-shutdown"] = {
            "operation_id": "op-shutdown",
            "chat_id": 10,
            "user_id": 123,
            "state": "running",
            "stage": "rename",
            "status_text": "正在重命名",
            "control": "rollback",
            "revision": 3,
            "details": {},
            "journal": journal,
            "task": forward_task,
            "cancel_event": SimpleNamespace(set=lambda: None),
        }

        await feature.operation_control({
            "operation_id": "op-shutdown",
            "action": "rollback",
            "revision": 3,
        })
        rollback_task = runtime.tasks.pop("rename-rollback-op-shutdown")
        rollback_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await rollback_task

        self.assertEqual(journal.calls, 0)
        forward_release.set()
        await forward_task

    async def test_download_event_accepts_handoff_and_runs_in_background(self):
        host = FakeHost()
        runtime = FakeRuntime()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        feature.bind_runtime(runtime)

        accepted = await feature.download_completed({
            "event_id": "event-operation",
            "payload": {
                "job_id": "job-operation",
                "selected_path": "/Movies",
                "user_id": 123,
                "chat_id": 10,
                "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024",
                "media_metadata": movie_contract(),
                "operation_id": "op-chain",
                "operation_revision": 8,
            },
        })

        self.assertEqual(accepted["operation"]["state"], "running")
        self.assertEqual(host.storage.moved, [])
        await runtime.wait()

        self.assertEqual(host.reports[0]["operation_id"], "op-chain")
        self.assertEqual(host.reports[0]["revision"], 9)
        stages = {item["stage"] for item in host.reports}
        self.assertTrue({
            "organizing", "conflict_validation", "directory_preparation",
            "rename", "moving", "cleanup",
        }.issubset(stages))
        self.assertEqual(host.reports[-1]["state"], "handed_off")
        self.assertEqual(host.reports[-1]["next_plugin_id"], "sync")
        self.assertEqual(host.events[0][1]["operation_id"], "op-chain")
        self.assertEqual(
            host.events[0][1]["operation_revision"],
            host.reports[-1]["revision"],
        )

        cancelled = await feature.operation_control({
            "operation_id": "op-chain",
            "action": "cancel",
            "revision": host.reports[-1]["revision"],
        })
        self.assertEqual(cancelled["operation"]["state"], "cancelled")
        self.assertIn("后续 Plex", cancelled["operation"]["status_text"])

    async def test_upstream_identity_starts_new_rename_message_without_repeat(self):
        host = FakeHost()
        runtime = FakeRuntime()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-upstream-identity",
            "payload": {
                "job_id": "job-upstream-identity",
                "selected_path": "/Movies",
                "user_id": 123,
                "chat_id": 10,
                "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024",
                "media_metadata": movie_contract(),
                "operation_id": "op-upstream-identity",
                "operation_revision": 8,
            },
        })
        await runtime.wait()

        self.assertFalse(any(
            item[:2] == ("milestone", "identity")
            for item in host.timeline
        ))
        self.assertEqual(
            host.timeline[0],
            ("report", "running", "organizing"),
        )

    async def test_resolved_identity_seals_before_new_organization_message(self):
        host = FakeHost()
        runtime = FakeRuntime()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-local-identity",
            "payload": {
                "job_id": "job-local-identity",
                "selected_path": "/Movies",
                "user_id": 123,
                "chat_id": 10,
                "download_root": "/Downloads/Movie.2024.mkv",
                "final_path": "/Downloads/Movie.2024.mkv",
                "resource_name": "Movie.2024.mkv",
                "operation_id": "op-local-identity",
                "operation_revision": 2,
                "file_tree": [{
                    "name": "Movie.2024.mkv",
                    "relative_path": "Movie.2024.mkv",
                    "path": "/Downloads/Movie.2024.mkv",
                    "is_dir": False,
                    "size": 1000,
                }],
            },
        })
        await runtime.wait()

        identity_index = next(
            index for index, item in enumerate(host.timeline)
            if item[:2] == ("milestone", "identity")
        )
        organizing_index = host.timeline.index(
            ("report", "running", "organizing")
        )
        self.assertLess(identity_index, organizing_index)

    async def test_rename_stage_seals_before_plex_event_is_published(self):
        host = FakeHost()
        runtime = FakeRuntime()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-rename-seal",
            "payload": {
                "job_id": "job-rename-seal",
                "selected_path": "/Movies",
                "user_id": 123,
                "chat_id": 10,
                "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024",
                "media_metadata": movie_contract(),
                "operation_id": "op-rename-seal",
                "operation_revision": 8,
            },
        })
        await runtime.wait()

        handoff_index = host.timeline.index(
            ("report", "handed_off", "handoff_plex")
        )
        seal_index = next(
            index for index, item in enumerate(host.timeline)
            if item[:2] == ("milestone", "stage")
        )
        event_index = host.timeline.index(("event", "media.organized"))
        self.assertLess(handoff_index, seal_index)
        self.assertLess(seal_index, event_index)

    async def test_completed_rename_skips_plex_when_sync_is_inactive(self):
        host = FakeHost()

        async def reject_missing_target(operation):
            host.reports.append(operation)
            if (
                operation.get("state") == "handed_off"
                and operation.get("next_plugin_id") == "sync"
            ):
                return {
                    "accepted": False,
                    "revision": operation["revision"] - 1,
                    "error_code": "handoff_target_unavailable",
                    "target_plugin_id": "sync",
                }
            return {"accepted": True, "revision": operation["revision"]}

        host.report_operation = reject_missing_target
        runtime = FakeRuntime()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-no-sync",
            "payload": {
                "job_id": "job-no-sync",
                "selected_path": "/Movies",
                "user_id": 123,
                "chat_id": 10,
                "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024",
                "media_metadata": movie_contract(),
                "operation_id": "op-no-sync",
                "operation_revision": 8,
            },
        })
        await runtime.wait()

        self.assertEqual(host.events, [])
        self.assertIn("Plex 管理未安装", host.notifications[-1][1])
        self.assertEqual(host.reports[-1]["state"], "completed")
        self.assertNotIn("next_plugin_id", host.reports[-1])

    async def test_cancel_during_metadata_stops_later_pipeline(self):
        entered = asyncio.Event()

        class BlockingHost(FakeHost):
            async def call_capability(self, capability, method, payload, **kwargs):
                if capability == "media.search":
                    entered.set()
                    await asyncio.Event().wait()
                return await super().call_capability(
                    capability, method, payload, **kwargs
                )

        host = BlockingHost()
        runtime = FakeRuntime()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        feature.bind_runtime(runtime)
        accepted = await feature.download_completed({
            "event_id": "event-cancel",
            "payload": {
                "job_id": "job-cancel", "selected_path": "/Movies",
                "user_id": 123, "chat_id": 10,
                "final_path": "/Downloads/Release",
                "resource_name": "Unknown.Release",
                "operation_id": "op-cancel", "operation_revision": 2,
            },
        })
        await entered.wait()

        result = await feature.operation_control({
            "operation_id": "op-cancel",
            "action": "cancel",
            "revision": accepted["operation"]["revision"],
        })
        await runtime.wait()

        self.assertEqual(result["operation"]["state"], "cancelling")
        self.assertEqual(host.reports[-1]["state"], "cancelled")
        self.assertEqual(host.storage.moved, [])
        self.assertEqual(host.events, [])

    async def test_direct_magnet_sends_structured_probe_not_file_tree_sentence(self):
        host = FakeHost()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        await feature.download_completed({
            "event_id": "event-direct",
            "payload": {
                "job_id": "job-direct", "selected_path": "/Movies",
                "user_id": 123, "chat_id": 10,
                "operation_id": "op-direct",
                "operation_revision": 2,
                "download_root": "/Downloads/Movie.2024.mkv",
                "final_path": "/Downloads/Movie.2024.mkv",
                "resource_name": "Movie.2024.mkv",
                "release": {"title": "Movie.2024.1080p.WEB-DL"},
                "file_tree": [{
                    "name": "Movie.2024.mkv",
                    "relative_path": "Movie.2024.mkv",
                    "path": "/Downloads/Movie.2024.mkv",
                    "is_dir": False,
                    "size": 1000,
                }],
            },
        })
        await runtime.wait()

        self.assertEqual(host.metadata_payload["query"], "Movie 2024")
        self.assertEqual(
            host.metadata_payload["probe"]["content_shape"],
            "movie",
        )
        self.assertNotIn("|", host.metadata_payload["query"])
        self.assertNotIn("1080p", host.metadata_payload["query"])
        self.assertEqual(
            len([
                item
                for item in host.milestones
                if item["mode"] == "identity"
            ]),
            1,
        )
        self.assertIn("中文电影 (English Movie)", host.milestones[0]["text"])
        self.assertEqual(
            host.storage.renamed[0][0],
            "/Downloads/Movie.2024.mkv",
        )

    @patch("telepiplex_rename.query_recovery.recover_query_with_ai")
    async def test_low_confidence_probe_recovers_before_search_metadata(
        self, ai_mock
    ):
        ai_mock.return_value = {
            "status": "ok",
            "identity_query": "Anime Series Title",
            "evidence_candidates": ["Anime Series Title"],
            "year_hint": "",
        }
        host = FakeHost()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-query-recovery",
            "payload": {
                "job_id": "job-query-recovery",
                "selected_path": "/Series",
                "user_id": 123,
                "chat_id": 10,
                "operation_id": "op-query-recovery",
                "operation_revision": 2,
                "download_root": "/Downloads/958271604",
                "final_path": "/Downloads/958271604",
                "resource_name": "958271604",
                "file_tree": [{
                    "relative_path": (
                        "【DHR百合組】[天使降臨到我身邊！_Anime Series "
                        "Title][05][繁體][1080P10][WebRip][HEVC][MP4].mp4"
                    ),
                    "is_dir": False,
                }],
            },
        })
        await runtime.wait()

        self.assertEqual(host.metadata_payload["query"], "Anime Series Title")
        self.assertEqual(
            host.metadata_payload["probe"]["recovery_source"],
            "ai_evidence_bound",
        )
        self.assertFalse(
            host.metadata_payload["probe"]["requires_recovery"]
        )

    @patch("telepiplex_rename.query_recovery.recover_query_with_ai")
    async def test_invalid_query_recovery_blocks_before_search_or_storage(
        self, ai_mock
    ):
        ai_mock.return_value = {
            "status": "ok",
            "identity_query": "Fabricated Show",
            "evidence_candidates": ["Fabricated Show"],
            "year_hint": "",
        }

        class NoCallHost(FakeHost):
            def __init__(self):
                super().__init__()
                self.capability_calls = []

            async def call_capability(
                self, capability, method, payload, **kwargs
            ):
                self.capability_calls.append((capability, method))
                return await super().call_capability(
                    capability, method, payload, **kwargs
                )

        host = NoCallHost()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-query-blocked",
            "payload": {
                "job_id": "job-query-blocked",
                "selected_path": "/Series",
                "user_id": 123,
                "chat_id": 10,
                "operation_id": "op-query-blocked",
                "operation_revision": 2,
                "download_root": "/Downloads/958271604",
                "final_path": "/Downloads/958271604",
                "resource_name": "958271604",
                "file_tree": [{
                    "relative_path": (
                        "【DHR百合組】[天使降臨到我身邊！_Anime Series "
                        "Title][05][繁體][1080P10][WebRip][HEVC][MP4].mp4"
                    ),
                    "is_dir": False,
                }],
            },
        })
        await runtime.wait()

        self.assertEqual(host.capability_calls, [])
        self.assertEqual(host.storage.renamed, [])
        self.assertEqual(host.storage.moved, [])
        self.assertEqual(host.reports[-1]["state"], "failed")
        self.assertEqual(
            host.reports[-1]["details"]["error_code"],
            "metadata_query_unresolved",
        )

    @patch("telepiplex_rename.query_recovery.recover_query_with_ai")
    async def test_unavailable_recovery_blocks_nonempty_low_confidence_query(
        self, ai_mock
    ):
        ai_mock.return_value = None

        class NoCallHost(FakeHost):
            def __init__(self):
                super().__init__()
                self.capability_calls = []

            async def call_capability(
                self, capability, method, payload, **kwargs
            ):
                self.capability_calls.append((capability, method))
                return await super().call_capability(
                    capability, method, payload, **kwargs
                )

        host = NoCallHost()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-query-unavailable",
            "payload": {
                "job_id": "job-query-unavailable",
                "selected_path": "/Series",
                "user_id": 123,
                "chat_id": 10,
                "operation_id": "op-query-unavailable",
                "operation_revision": 2,
                "download_root": "/Downloads/958271604",
                "final_path": "/Downloads/958271604",
                "resource_name": "958271604",
                "file_tree": [{
                    "relative_path": (
                        "【DHR百合組】[天使降臨到我身邊！_Anime Series "
                        "Title][05][繁體][1080P10][WebRip][HEVC][MP4].mp4"
                    ),
                    "is_dir": False,
                }],
            },
        })
        await runtime.wait()

        self.assertEqual(host.capability_calls, [])
        self.assertEqual(host.storage.renamed, [])
        self.assertEqual(host.storage.moved, [])
        self.assertEqual(
            host.reports[-1]["details"]["error_code"],
            "metadata_query_unresolved",
        )

    async def test_download_event_calls_storage_rpc_and_publishes_media_organized(self):
        host = FakeHost()
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        await feature.download_completed({
            "event_id": "event-1",
            "payload": {
                "job_id": "job-1",
                "link": "magnet:?x",
                "selected_path": "/Movies",
                "user_id": 123,
                "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024",
                "provider": "download",
                "media_metadata": movie_contract(),
            },
        })
        await runtime.wait()

        self.assertEqual(host.assert_capability, "storage.provider")
        self.assertEqual(host.events[0][0], "media.organized")
        self.assertEqual(host.events[0][1]["job_id"], "job-1")
        self.assertEqual(host.events[0][1]["final_path"], "/Movies/中文电影 (English Movie)")
        self.assertTrue(host.events[0][1]["media_metadata"]["confirmed"])
        self.assertEqual(
            host.events[0][1]["media_metadata"]["identity"]["english_title"],
            "English Movie",
        )
        self.assertIn("整理完成", host.notifications[0][1])
        self.assertNotIn("`", host.notifications[0][1])

    async def test_incomplete_cleanup_notifies_without_publishing_organized(self):
        host = FakeHost(CleanupFailureStorage([
            {"fn": "Movie.2024.mkv", "fid": "1", "fc": "1", "fs": 1000},
        ]))
        feature = RenameFeature(
            config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
            host=host,
        )
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)

        await feature.download_completed({
            "event_id": "event-cleanup-failed",
            "payload": {
                "job_id": "job-cleanup-failed", "selected_path": "/Movies",
                "user_id": 123, "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024", "media_metadata": movie_contract(),
            },
        })
        await runtime.wait()

        self.assertEqual(host.events, [])
        self.assertIn("源目录清理未完成", host.notifications[0][1])

    async def test_delivery_replay_does_not_repeat_destructive_storage_operations(self):
        from telepiplex_rename.jobs import RenameJobStore

        with tempfile.TemporaryDirectory() as tmpdir:
            host = FakeHost()
            feature = RenameFeature(
                config={"unorganized_path": "/Unorganized", "storage_timeout": 3},
                host=host, jobs=RenameJobStore(Path(tmpdir) / "jobs.db"),
            )
            runtime = FakeRuntime()
            feature.bind_runtime(runtime)
            request = {"event_id": "event-replay", "payload": {
                "job_id": "job-replay", "selected_path": "/Movies", "user_id": 123,
                "final_path": "/Downloads/Release", "resource_name": "Movie.2024",
                "media_metadata": movie_contract(),
            }}
            host.fail_notification = True
            with self.assertRaises(RuntimeError):
                await feature.download_completed(request)
                await runtime.wait()
            moved_count = len(host.storage.moved)
            host.fail_notification = False

            replay = await feature.download_completed(request)

            self.assertEqual(len(host.storage.moved), moved_count)
            self.assertTrue(replay["organized"])

    async def test_lost_accept_report_response_still_starts_executor_once(self):
        from telepiplex_rename.jobs import RenameJobStore

        class LostAcceptAckHost(FakeHost):
            def __init__(self):
                super().__init__()
                self.report_attempts = 0

            async def report_operation(self, operation):
                self.report_attempts += 1
                if self.report_attempts <= 2:
                    raise RuntimeError("Host response lost")
                self.reports.append(dict(operation))
                return {"accepted": True, "revision": operation["revision"]}

        with tempfile.TemporaryDirectory() as tmpdir:
            host = LostAcceptAckHost()
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            feature = RenameFeature(
                config={"unorganized_path": "/Unorganized"},
                host=host,
                jobs=jobs,
            )
            runtime = FakeRuntime()
            feature.bind_runtime(runtime)
            request = {"event_id": "lost-accept-ack", "payload": {
                "job_id": "job-lost-accept-ack",
                "selected_path": "/Movies",
                "user_id": 123,
                "chat_id": 10,
                "final_path": "/Downloads/Release",
                "resource_name": "Movie.2024",
                "media_metadata": movie_contract(),
                "operation_id": "op-lost-accept-ack",
                "operation_revision": 5,
            }}

            accepted = await feature.download_completed(request)
            duplicate = await feature.download_completed(request)

            self.assertTrue(accepted["accepted"])
            self.assertTrue(accepted["report_pending"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["state"], "processing")
            self.assertEqual(list(runtime.tasks), [
                "rename-job-lost-accept-ack"
            ])
            await runtime.wait()
            self.assertEqual(jobs.get("job-lost-accept-ack")["state"], "completed")
            self.assertEqual(len(host.storage.moved), 1)

    async def test_rejected_operation_claim_never_changes_media_files(self):
        from telepiplex_rename.jobs import RenameJobStore

        class RejectedHost(FakeHost):
            async def report_operation(self, operation):
                self.reports.append(dict(operation))
                return {
                    "accepted": False,
                    "state": "cancelled",
                    "revision": operation["revision"],
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            host = RejectedHost()
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            feature = RenameFeature(
                config={"unorganized_path": "/Unorganized"},
                host=host,
                jobs=jobs,
            )
            runtime = FakeRuntime()
            feature.bind_runtime(runtime)
            result = await feature.download_completed({
                "event_id": "rejected-claim",
                "payload": {
                    "job_id": "job-rejected-claim",
                    "user_id": 123,
                    "chat_id": 10,
                    "final_path": "/Downloads/Release",
                    "operation_id": "op-rejected-claim",
                    "operation_revision": 5,
                },
            })

            self.assertEqual(result["state"], "interrupted")
            self.assertEqual(runtime.tasks, {})
            self.assertEqual(host.storage.moved, [])
            self.assertEqual(jobs.get("job-rejected-claim")["state"], "cancelled")

    async def test_processed_replay_restores_operation_before_plex_publish(self):
        from telepiplex_rename.jobs import RenameJobStore

        with tempfile.TemporaryDirectory() as tmpdir:
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            jobs.claim("job-processed-replay")
            jobs.update("job-processed-replay", "processed", {
                "organized": True,
                "final_path": "/Movies/Movie",
                "message": "✅ 整理完成",
                "user_id": 123,
                "job_id": "job-processed-replay",
                "event_payload": {
                    "job_id": "job-processed-replay",
                    "user_id": 123,
                    "chat_id": 10,
                    "provider": "download",
                    "final_path": "/Movies/Movie",
                    "media_metadata": movie_contract(),
                    "operation_id": "op-processed-replay",
                    "operation_revision": 9,
                },
            })
            host = FakeHost()
            feature = RenameFeature(
                config={"unorganized_path": "/Unorganized"},
                host=host,
                jobs=jobs,
            )
            feature.bind_runtime(FakeRuntime())

            replay = await feature.download_completed({
                "event_id": "same-event",
                "payload": {
                    "job_id": "job-processed-replay",
                    "user_id": 123,
                    "chat_id": 10,
                    "operation_id": "op-processed-replay",
                    "operation_revision": 9,
                },
            })

            self.assertTrue(replay["organized"])
            self.assertEqual(host.reports[-1]["state"], "handed_off")
            self.assertEqual(
                host.events[-1][1]["operation_id"],
                "op-processed-replay",
            )
            self.assertEqual(
                host.events[-1][1]["operation_revision"],
                host.reports[-1]["revision"],
            )

    async def test_processed_replay_without_durable_identity_stops_downstream(self):
        from telepiplex_rename.jobs import RenameJobStore

        with tempfile.TemporaryDirectory() as tmpdir:
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            jobs.claim("job-missing-identity")
            jobs.update("job-missing-identity", "processed", {
                "organized": True,
                "event_payload": {
                    "job_id": "job-missing-identity",
                    "user_id": 123,
                    "chat_id": 10,
                    "final_path": "/Movies/Movie",
                },
            })
            host = FakeHost()
            feature = RenameFeature(
                config={"unorganized_path": "/Unorganized"},
                host=host,
                jobs=jobs,
            )
            feature.bind_runtime(FakeRuntime())

            replay = await feature.download_completed({
                "event_id": "same-event",
                "payload": {
                    "job_id": "job-missing-identity",
                    "user_id": 123,
                    "chat_id": 10,
                    "operation_id": "op-missing-identity",
                },
            })

            self.assertEqual(replay["state"], "interrupted")
            self.assertEqual(host.events, [])
            self.assertEqual(host.reports[-1]["state"], "interrupted")
            self.assertTrue(
                host.reports[-1]["details"]["manual_check_required"]
            )

    async def test_completed_chain_replay_only_acks_durable_duplicate(self):
        from telepiplex_rename.jobs import RenameJobStore

        with tempfile.TemporaryDirectory() as tmpdir:
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            outcome = {
                "organized": True,
                "final_path": "/Movies/Movie",
                "message": "✅ 整理完成",
                "user_id": 123,
                "job_id": "job-completed-chain",
                "handoff_reported": True,
                "event_payload": {
                    "job_id": "job-completed-chain",
                    "user_id": 123,
                    "chat_id": 10,
                    "final_path": "/Movies/Movie",
                    "operation_id": "op-completed-chain",
                    "operation_revision": 12,
                },
            }
            jobs.claim("job-completed-chain")
            jobs.update("job-completed-chain", "completed", outcome)
            host = FakeHost()
            feature = RenameFeature(
                config={"unorganized_path": "/Unorganized"},
                host=host,
                jobs=jobs,
            )
            feature.bind_runtime(FakeRuntime())

            replay = await feature.download_completed({
                "event_id": "lost-source-ack",
                "payload": {
                    "job_id": "job-completed-chain",
                    "user_id": 123,
                    "chat_id": 10,
                    "operation_id": "op-completed-chain",
                    "operation_revision": 9,
                },
            })

            self.assertTrue(replay["duplicate"])
            self.assertEqual(replay["state"], "completed")
            self.assertEqual(host.reports, [])
            self.assertEqual(host.events, [])
            self.assertEqual(host.notifications, [])

    async def test_lost_handoff_ack_replays_same_durable_revision(self):
        from telepiplex_rename.jobs import RenameJobStore

        class LostAckHost(FakeHost):
            async def report_operation(self, operation):
                self.reports.append(dict(operation))
                if operation["state"] == "handed_off":
                    raise RuntimeError("handoff response lost")
                return {"accepted": True, "revision": operation["revision"]}

        with tempfile.TemporaryDirectory() as tmpdir:
            jobs = RenameJobStore(Path(tmpdir) / "jobs.db")
            outcome = {
                "organized": True,
                "final_path": "/Movies/Movie",
                "message": "✅ 整理完成",
                "user_id": 123,
                "job_id": "job-lost-handoff-ack",
                "event_payload": {
                    "job_id": "job-lost-handoff-ack",
                    "user_id": 123,
                    "chat_id": 10,
                    "final_path": "/Movies/Movie",
                    "operation_id": "op-lost-handoff-ack",
                    "operation_revision": 9,
                },
            }
            jobs.claim("job-lost-handoff-ack")
            jobs.update("job-lost-handoff-ack", "processed", outcome)
            first_host = LostAckHost()
            first = RenameFeature(
                config={"unorganized_path": "/Unorganized"},
                host=first_host,
                jobs=jobs,
            )
            first.bind_runtime(FakeRuntime())
            request = {"event_id": "lost-handoff-source", "payload": {
                "job_id": "job-lost-handoff-ack",
                "user_id": 123,
                "chat_id": 10,
                "operation_id": "op-lost-handoff-ack",
                "operation_revision": 9,
            }}

            with self.assertRaises(RuntimeError):
                await first.download_completed(request)
            durable = jobs.get("job-lost-handoff-ack")["result"]
            proposed = durable["handoff_operation"]["revision"]
            self.assertFalse(durable.get("handoff_reported", False))

            replay_host = FakeHost()
            replayed = RenameFeature(
                config={"unorganized_path": "/Unorganized"},
                host=replay_host,
                jobs=jobs,
            )
            replay_runtime = FakeRuntime()
            replayed.bind_runtime(replay_runtime)
            await replay_runtime.wait()

            self.assertEqual(
                [report["state"] for report in replay_host.reports],
                ["handed_off"],
            )
            self.assertEqual(replay_host.reports[0]["revision"], proposed)
            self.assertEqual(len(replay_host.events), 1)


class FeatureSourceContractTest(unittest.TestCase):
    def test_release_identity_uses_new_confirmed_identity_version(self):
        manifest = yaml.safe_load(
            (ROOT / "manifest.yaml").read_text(encoding="utf-8")
        )
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "1.4.0")
        self.assertEqual(manifest["host_api"], ">=1.4,<2.0")
        self.assertIn('version = "1.4.0"', project)

    def test_inventory_command_is_visible_and_config_command_is_hidden(self):
        manifest = yaml.safe_load(
            (ROOT / "manifest.yaml").read_text(encoding="utf-8")
        )
        commands = {
            item["name"]: item for item in manifest["commands"]
        }

        self.assertTrue(commands["rename"]["menu_visible"])
        self.assertFalse(commands["rename_config"]["menu_visible"])

    def test_readme_build_example_uses_current_version(self):
        source = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("/tmp/rename-1.4.0.tpx", source)
        self.assertNotIn("dist/rename-1.4.0.tpx", source)

    def test_source_has_no_host_telegram_or_init_imports(self):
        forbidden = []
        for path in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                forbidden.extend(name for name in names if name.split(".", 1)[0] in {"app", "init", "telegram"})
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
