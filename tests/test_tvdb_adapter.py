import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.adapters import tvdb


class TvdbAdapterTest(unittest.TestCase):
    def test_korean_primary_name_uses_latin_alias_and_search_image(self):
        item = tvdb._normalize_search_item(
            {
                "tvdb_id": "411469",
                "name": "더 글로리",
                "aliases": ["The Glory (2022)", "The Glory (KR)"],
                "image_url": "https://art.example/glory.jpg",
                "year": "2022",
            },
            "series",
        )

        self.assertEqual(item["english_title"], "The Glory")
        self.assertEqual(item["cover_url"], "https://art.example/glory.jpg")
        self.assertEqual(item["tvdb_series_id"], "411469")

    def test_search_image_falls_back_to_poster_thumbnail_then_image(self):
        self.assertEqual(
            tvdb._normalize_search_item({"tvdb_id": "1", "poster": "poster.jpg"}, "movie")["cover_url"],
            "poster.jpg",
        )
        self.assertEqual(
            tvdb._normalize_search_item({"tvdb_id": "1", "posters": ["", "posters.jpg"]}, "movie")["cover_url"],
            "posters.jpg",
        )
        self.assertEqual(
            tvdb._normalize_search_item({"tvdb_id": "1", "thumbnail": "thumb.jpg"}, "movie")["cover_url"],
            "thumb.jpg",
        )
        self.assertEqual(
            tvdb._normalize_search_item({"tvdb_id": "1", "image": "image.jpg"}, "movie")["cover_url"],
            "image.jpg",
        )

    def test_non_english_translations_are_preserved_as_match_aliases(self):
        item = tvdb._normalize_search_item(
            {
                "tvdb_id": "411469",
                "name": "더 글로리",
                "name_translated": "黑暗荣耀",
                "translations": {"eng": "The Glory", "zho": "黑暗荣耀"},
                "aliases": ["The Glory (2022)"],
                "year": "2022",
            },
            "series",
        )

        self.assertEqual(item["english_title"], "The Glory")
        self.assertIn("黑暗荣耀", item["aliases"])

    @patch.object(tvdb, "_tvdb_get")
    def test_movie_search_uses_translation_endpoint_only_without_latin_title(self, get_mock):
        get_mock.side_effect = [
            {"data": [{"tvdb_id": "123", "name": "中文片名", "year": "2024"}]},
            {"data": {"name": "English Movie", "language": "eng"}},
        ]

        result = tvdb.search_tvdb_movies("中文片名", "2024")

        self.assertEqual(result[0]["english_title"], "English Movie")
        self.assertEqual(result[0]["tvdb_movie_id"], "123")
        self.assertEqual(get_mock.call_args_list[1].args[0], "/movies/123/translations/eng")

    @patch.object(tvdb, "_tvdb_get")
    def test_latin_alias_avoids_translation_request(self, get_mock):
        get_mock.return_value = {
            "data": [
                {
                    "tvdb_id": "411469",
                    "name": "더 글로리",
                    "aliases": ["The Glory (2022)"],
                    "year": "2022",
                }
            ]
        }

        result = tvdb.search_tvdb_series("黑暗荣耀", "2022")

        self.assertEqual(result[0]["english_title"], "The Glory")
        get_mock.assert_called_once_with("/search", params={"query": "黑暗荣耀", "type": "series", "year": "2022"})

    @patch.object(tvdb, "_tvdb_get")
    def test_movie_artwork_uses_extended_image(self, get_mock):
        get_mock.return_value = {"data": {"image": "https://art.example/movie.jpg"}}

        self.assertEqual(
            tvdb.get_tvdb_movie_artwork_url("123"),
            "https://art.example/movie.jpg",
        )
        get_mock.assert_called_once_with("/movies/123/extended", params={"short": True})


if __name__ == "__main__":
    unittest.main()
