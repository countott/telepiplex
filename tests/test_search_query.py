import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.search_query import (
    extract_search_query_from_ocr_text,
    is_supported_metadata_url,
    parse_media_page_title,
)


class SearchQueryHelpersTest(unittest.TestCase):
    def test_parse_douban_title_removes_site_suffix(self):
        html = "<html><head><title>布达佩斯大饭店 The Grand Budapest Hotel (豆瓣)</title></head></html>"

        self.assertEqual(parse_media_page_title(html), "布达佩斯大饭店 The Grand Budapest Hotel")

    def test_parse_imdb_title_keeps_year_as_search_hint(self):
        html = "<html><head><title>The Grand Budapest Hotel (2014) - IMDb</title></head></html>"

        self.assertEqual(parse_media_page_title(html), "The Grand Budapest Hotel 2014")

    def test_parse_tvdb_title_removes_site_suffix(self):
        html = '<html><head><meta property="og:title" content="Breaking Bad | TheTVDB.com" /></head></html>'

        self.assertEqual(parse_media_page_title(html), "Breaking Bad")

    def test_supported_metadata_urls_include_douban_imdb_and_tvdb(self):
        self.assertTrue(is_supported_metadata_url("https://movie.douban.com/subject/11525673/"))
        self.assertTrue(is_supported_metadata_url("https://www.imdb.com/title/tt2278388/"))
        self.assertTrue(is_supported_metadata_url("https://thetvdb.com/series/breaking-bad"))
        self.assertFalse(is_supported_metadata_url("https://example.com/title/tt2278388/"))

    def test_extract_search_query_from_ocr_text_prefers_title_and_nearby_year(self):
        text = """
        IMDb
        The Grand Budapest Hotel
        2014
        1h 39m
        User reviews
        """

        self.assertEqual(extract_search_query_from_ocr_text(text), "The Grand Budapest Hotel 2014")

    def test_extract_search_query_from_douban_screenshot_text(self):
        text = """
        豆瓣电影
        布达佩斯大饭店 The Grand Budapest Hotel
        2014 / 美国 德国 / 剧情 喜剧
        8.9
        想看 看过
        """

        self.assertEqual(extract_search_query_from_ocr_text(text), "布达佩斯大饭店 The Grand Budapest Hotel 2014")


if __name__ == "__main__":
    unittest.main()
