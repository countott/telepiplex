import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.directory_config import get_save_directories


class DirectoryConfigTest(unittest.TestCase):
    def test_flat_category_folder_is_used_as_save_directories(self):
        config = {
            "category_folder": [
                {"name": "电影", "path": "/电影"},
                {"name": "剧集", "path": "/剧集"},
            ]
        }

        self.assertEqual(
            get_save_directories(config),
            [
                {"name": "电影", "path": "/电影"},
                {"name": "剧集", "path": "/剧集"},
            ],
        )

    def test_legacy_nested_category_folder_is_ignored_after_flat_directory_migration(self):
        config = {
            "category_folder": [
                {
                    "name": "media",
                    "display_name": "媒体",
                    "path_map": [
                        {"name": "电影", "path": "/电影"},
                        {"name": "剧集", "path": "/剧集"},
                    ],
                }
            ]
        }

        self.assertEqual(get_save_directories(config), [])

    def test_plex_library_id_is_not_part_of_feature_115_directory_contract(self):
        config = {
            "category_folder": [
                {"name": "电影", "path": "/电影", "plex_library_id": "1"},
            ]
        }

        self.assertEqual(get_save_directories(config), [{"name": "电影", "path": "/电影"}])


if __name__ == "__main__":
    unittest.main()
