import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.directory_config import find_save_directory_for_path, get_plex_library_id_for_path, get_save_directories


class DirectoryConfigTest(unittest.TestCase):
    def test_flat_category_folder_is_used_as_save_directories(self):
        config = {
            "category_folder": [
                {"name": "真人电影", "path": "/真人电影"},
                {"name": "动画剧集", "path": "/动画剧集"},
            ]
        }

        self.assertEqual(
            get_save_directories(config),
            [
                {"name": "真人电影", "path": "/真人电影"},
                {"name": "动画剧集", "path": "/动画剧集"},
            ],
        )

    def test_legacy_nested_category_folder_is_flattened_for_compatibility(self):
        config = {
            "category_folder": [
                {
                    "name": "media",
                    "display_name": "媒体",
                    "path_map": [
                        {"name": "真人电影", "path": "/真人电影"},
                        {"name": "动画剧集", "path": "/动画剧集"},
                    ],
                }
            ]
        }

        self.assertEqual(
            get_save_directories(config),
            [
                {"name": "真人电影", "path": "/真人电影"},
                {"name": "动画剧集", "path": "/动画剧集"},
            ],
        )

    def test_plex_library_id_maps_by_selected_115_path_prefix(self):
        config = {
            "category_folder": [
                {"name": "真人电影", "path": "/真人电影", "plex_library_id": "1"},
                {"name": "真人剧集", "path": "/真人剧集", "plex_library_id": "2"},
            ],
            "media": {"plex": {"library_id": "fallback"}},
        }

        self.assertEqual(get_plex_library_id_for_path("/真人剧集/Dexter/Dexter Season 01", config), "2")
        self.assertEqual(get_plex_library_id_for_path("/真人电影/Some Movie", config), "1")
        self.assertEqual(get_plex_library_id_for_path("/未映射/Some Movie", config), "fallback")

    def test_directory_match_uses_longest_path_prefix(self):
        config = {
            "category_folder": [
                {"name": "剧集", "path": "/真人剧集", "plex_library_id": "2"},
                {"name": "特别剧集", "path": "/真人剧集/特别篇", "plex_library_id": "9"},
            ]
        }

        self.assertEqual(
            find_save_directory_for_path("/真人剧集/特别篇/Show", config),
            {"name": "特别剧集", "path": "/真人剧集/特别篇", "plex_library_id": "9"},
        )


if __name__ == "__main__":
    unittest.main()
