import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.utils.search_query import (
    extract_douban_subject_id,
    is_supported_metadata_url,
    parse_douban_mobile_title,
    parse_douban_rexxar_title,
    parse_douban_subject_abstract_title,
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

    def test_extract_douban_subject_id_from_movie_url(self):
        self.assertEqual(extract_douban_subject_id("https://movie.douban.com/subject/4864908/"), "4864908")
        self.assertEqual(extract_douban_subject_id("https://m.douban.com/movie/subject/4864908/"), "4864908")
        self.assertEqual(extract_douban_subject_id("https://example.com/subject/4864908/"), "")

    def test_parse_douban_subject_abstract_title_extracts_title_and_year(self):
        payload = {"subject": {"title": "影", "release_year": "2018"}}

        self.assertEqual(parse_douban_subject_abstract_title(payload), "影 2018")

    def test_parse_douban_rexxar_title_extracts_title_and_year(self):
        payload = {"title": "影", "year": "2018"}

        self.assertEqual(parse_douban_rexxar_title(payload), "影 2018")

    def test_parse_douban_mobile_title_rejects_generic_douban_title(self):
        self.assertEqual(parse_douban_mobile_title("<html><head><title>豆瓣</title></head></html>"), "")
        self.assertEqual(
            parse_douban_mobile_title("<html><head><title>影 Shadow (2018) - 豆瓣</title></head></html>"),
            "影 Shadow 2018",
        )


if __name__ == "__main__":
    unittest.main()
