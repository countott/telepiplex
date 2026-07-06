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
    parse_douban_page_title,
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

    def test_parse_imdb_title_removes_series_label_and_keeps_year(self):
        html = "<html><head><title>Dexter: Original Sin (TV Series 2024) - IMDb</title></head></html>"

        self.assertEqual(parse_media_page_title(html), "Dexter: Original Sin 2024")

    def test_parse_tvdb_title_removes_site_suffix(self):
        html = '<html><head><meta property="og:title" content="Breaking Bad | TheTVDB.com" /></head></html>'

        self.assertEqual(parse_media_page_title(html), "Breaking Bad")

    def test_parse_tmdb_title_removes_site_suffix_and_keeps_year(self):
        html = """
        <html>
          <head>
            <meta property="og:title" content="The Lord of the Rings: The Fellowship of the Ring (2001) | The Movie Database (TMDB)" />
          </head>
        </html>
        """

        self.assertEqual(parse_media_page_title(html), "The Lord of the Rings: The Fellowship of the Ring 2001")

    def test_supported_metadata_urls_include_douban_imdb_tvdb_and_tmdb(self):
        self.assertTrue(is_supported_metadata_url("https://movie.douban.com/subject/11525673/"))
        self.assertTrue(is_supported_metadata_url("https://www.imdb.com/title/tt2278388/"))
        self.assertTrue(is_supported_metadata_url("https://thetvdb.com/series/breaking-bad"))
        self.assertTrue(is_supported_metadata_url("https://www.themoviedb.org/movie/120-the-lord-of-the-rings"))
        self.assertTrue(is_supported_metadata_url("https://www.tmdb.org/movie/120-the-lord-of-the-rings"))
        self.assertFalse(is_supported_metadata_url("https://example.com/title/tt2278388/"))

    def test_extract_douban_subject_id_from_movie_url(self):
        self.assertEqual(extract_douban_subject_id("https://movie.douban.com/subject/4864908/"), "4864908")
        self.assertEqual(extract_douban_subject_id("https://m.douban.com/movie/subject/4864908/"), "4864908")
        self.assertEqual(extract_douban_subject_id("https://example.com/subject/4864908/"), "")

    def test_parse_douban_subject_abstract_title_extracts_title_and_year(self):
        payload = {"subject": {"title": "影", "original_title": "Shadow", "release_year": "2018"}}

        self.assertEqual(parse_douban_subject_abstract_title(payload), "Shadow 2018")

    def test_parse_douban_rexxar_title_extracts_title_and_year(self):
        payload = {"title": "影", "original_title": "Shadow", "year": "2018"}

        self.assertEqual(parse_douban_rexxar_title(payload), "Shadow 2018")

    def test_parse_douban_subject_abstract_title_prefers_latin_alias(self):
        payload = {"subject": {"title": "影", "release_year": "2018", "aka": ["三国·荆州", "Shadow"]}}

        self.assertEqual(parse_douban_subject_abstract_title(payload), "Shadow 2018")

    def test_parse_douban_subject_abstract_title_preserves_accented_latin_title(self):
        payload = {"subject": {"title": "这个杀手不太冷", "original_title": "Léon", "release_year": "1994"}}

        self.assertEqual(parse_douban_subject_abstract_title(payload), "Léon 1994")

    def test_parse_douban_subject_abstract_title_extracts_accented_latin_from_mixed_title(self):
        payload = {"subject": {"title": "这个杀手不太冷 Léon", "release_year": "1994"}}

        self.assertEqual(parse_douban_subject_abstract_title(payload), "Léon 1994")

    def test_parse_douban_subject_abstract_title_extracts_latin_from_chinese_english_year_mix(self):
        payload = {"subject": {"title": "嗜血法医：源罪 Dexter: Original Sin‎ (2024)", "release_year": "2024"}}

        self.assertEqual(parse_douban_subject_abstract_title(payload), "Dexter Original Sin 2024")

    def test_parse_douban_mobile_title_prefers_original_name_and_rejects_generic_douban_title(self):
        self.assertEqual(parse_douban_mobile_title("<html><head><title>豆瓣</title></head></html>"), "")
        self.assertEqual(
            parse_douban_mobile_title("<html><head><title>影 Shadow (2018) - 豆瓣</title></head></html>"),
            "Shadow 2018",
        )

    def test_parse_douban_page_title_rejects_generic_site_title(self):
        self.assertEqual(parse_douban_page_title("<html><head><title>豆瓣</title></head></html>"), "")
        self.assertEqual(
            parse_douban_page_title("<html><head><title>影 Shadow (2018) (豆瓣)</title></head></html>"),
            "Shadow 2018",
        )
        html = """
        <html>
          <head><title>影 Shadow (2018) - 豆瓣</title></head>
          <body><span>原名:</span> Shadow<br><span>年份:</span> 2018</body>
        </html>
        """
        self.assertEqual(
            parse_douban_mobile_title(html),
            "Shadow 2018",
        )


if __name__ == "__main__":
    unittest.main()
