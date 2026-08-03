import unittest

from telepiplex_search.input_contract import (
    classify_search_input,
    extract_message_urls,
    has_ambiguous_bare_number,
)


class InputContractTest(unittest.TestCase):
    def test_plain_and_scoped_text_queries(self):
        plain = classify_search_input("黑暗荣耀")
        season = classify_search_input("黑暗荣耀 第一季")
        episode = classify_search_input("黑暗荣耀 S01E03")

        self.assertEqual((plain.kind, plain.title, plain.scope), ("text", "黑暗荣耀", "work"))
        self.assertEqual((season.scope, season.season_number), ("season", 1))
        self.assertEqual(
            (episode.scope, episode.season_number, episode.episode_number),
            ("episode", 1, 3),
        )
        self.assertEqual(season.media_type, "series")

    def test_year_and_media_type_are_separate_from_clean_title(self):
        movie = classify_search_input("康斯坦丁 2005 电影")
        series = classify_search_input("康斯坦丁（电视剧） 2014")

        self.assertEqual(
            (movie.title, movie.year, movie.media_type, movie.raw_query),
            ("康斯坦丁", "2005", "movie", "康斯坦丁 2005"),
        )
        self.assertEqual(
            (series.title, series.year, series.media_type, series.raw_query),
            ("康斯坦丁", "2014", "series", "康斯坦丁 2014"),
        )

    def test_nfkc_and_punctuation_keep_the_full_title(self):
        parsed = classify_search_input("《蝙蝠侠：黑暗骑士》")

        self.assertEqual(parsed.title, "蝙蝠侠 黑暗骑士")

    def test_numeric_english_season_and_episode_are_supported(self):
        season = classify_search_input("The Glory Season 01")
        episode = classify_search_input("The Glory Season 1 Episode 2")

        self.assertEqual((season.scope, season.season_number), ("season", 1))
        self.assertEqual(
            (episode.scope, episode.season_number, episode.episode_number),
            ("episode", 1, 2),
        )

    def test_whole_series_words_set_scope_without_polluting_title(self):
        for query in (
            "黑暗荣耀 全季",
            "黑暗荣耀 整剧",
            "黑暗荣耀 整劇",
            "黑暗荣耀 全剧",
            "黑暗荣耀 全劇",
        ):
            with self.subTest(query=query):
                parsed = classify_search_input(query)

                self.assertEqual(parsed.scope, "whole_series")
                self.assertEqual(parsed.title, "黑暗荣耀")

    def test_ranges_and_number_words_are_rejected(self):
        for query in (
            "Title S01-S03",
            "Title S01E01-E05",
            "Title Season One",
        ):
            with self.subTest(query=query):
                parsed = classify_search_input(query)

                self.assertEqual(parsed.kind, "unsupported_text")
                self.assertEqual(parsed.reason, "unsupported_scope_syntax")

    def test_1x02_is_not_a_supported_user_scope(self):
        parsed = classify_search_input("Title 1x02")

        self.assertEqual(parsed.kind, "unsupported_text")
        self.assertEqual(parsed.reason, "unsupported_scope_syntax")

    def test_year_is_not_an_ambiguous_bare_number(self):
        parsed = classify_search_input("蝙蝠侠 1989")

        self.assertEqual(parsed.year, "1989")
        self.assertFalse(has_ambiguous_bare_number("蝙蝠侠 1989", parsed))

    def test_quoted_pure_number_is_an_explicit_title_not_a_year(self):
        for query in ('"1917"', "“1917”"):
            with self.subTest(query=query):
                parsed = classify_search_input(query)

                self.assertEqual(parsed.raw_query, "1917")
                self.assertEqual(parsed.title, "1917")
                self.assertEqual(parsed.year, "")

    def test_unverified_title_suffix_is_recorded_without_guessing_its_role(self):
        batman = classify_search_input("蝙蝠侠1")
        transformers = classify_search_input("变形金刚3")

        self.assertTrue(has_ambiguous_bare_number("蝙蝠侠1", batman))
        self.assertTrue(has_ambiguous_bare_number("变形金刚3", transformers))
        self.assertEqual(batman.numeric_tokens[0].role, "ambiguous")
        self.assertEqual(transformers.numeric_tokens[0].value, 3)

    def test_douban_work_link(self):
        parsed = classify_search_input(
            "https://movie.douban.com/subject/35314632/"
        )

        self.assertEqual(parsed.kind, "link")
        self.assertEqual(parsed.link.provider, "douban")
        self.assertEqual(parsed.link.entity_id, "35314632")
        self.assertEqual(parsed.link.scope, "work")

    def test_share_text_extracts_one_mobile_douban_entity(self):
        raw = (
            "分享《繁花》\n"
            "https://m.douban.com/movie/subject/36490422/?from=share"
        )

        parsed = classify_search_input(raw)

        self.assertEqual(parsed.kind, "link")
        self.assertEqual(parsed.link.provider, "douban")
        self.assertEqual(parsed.link.entity_id, "36490422")
        self.assertEqual(parsed.urls, (
            "https://m.douban.com/movie/subject/36490422/?from=share",
        ))
        self.assertEqual(parsed.fallback_title, "分享《繁花》")

    def test_url_extraction_strips_share_punctuation(self):
        self.assertEqual(
            extract_message_urls(
                "见：https://zh.wikipedia.org/wiki/%E7%B9%81%E8%8A%B1。"
            ),
            ("https://zh.wikipedia.org/wiki/%E7%B9%81%E8%8A%B1",),
        )

    def test_tvdb_work_season_and_episode_links(self):
        series = classify_search_input("https://thetvdb.com/series/411469")
        season = classify_search_input("https://thetvdb.com/seasons/205768")
        episode = classify_search_input("https://thetvdb.com/episodes/9481027")

        self.assertEqual((series.link.media_type, series.link.scope), ("series", "work"))
        self.assertEqual((season.link.media_type, season.link.scope), ("series", "season"))
        self.assertEqual((episode.link.media_type, episode.link.scope), ("series", "episode"))

    def test_tvdb_localized_link_is_supported(self):
        parsed = classify_search_input(
            "https://thetvdb.com/zh-CN/series/411469"
        )

        self.assertEqual(parsed.kind, "link")
        self.assertEqual(parsed.link.entity_id, "411469")

    def test_wikipedia_article_link_is_a_supported_exact_anchor(self):
        parsed = classify_search_input(
            "https://en.wikipedia.org/wiki/The_Grand_Budapest_Hotel"
        )

        self.assertEqual(parsed.kind, "link")
        self.assertEqual(parsed.link.provider, "wikipedia")
        self.assertEqual(
            parsed.link.entity_id,
            "en:The Grand Budapest Hotel",
        )
        self.assertEqual(parsed.link.scope, "work")

    def test_wikipedia_mobile_article_uses_language_identity(self):
        parsed = classify_search_input(
            "https://zh.m.wikipedia.org/wiki/%E7%B9%81%E8%8A%B1_(2023%E5%B9%B4%E7%94%B5%E8%A7%86%E5%89%A7)"
        )

        self.assertEqual(parsed.kind, "link")
        self.assertEqual(parsed.link.provider, "wikipedia")
        self.assertTrue(parsed.link.entity_id.startswith("zh:繁花"))

    def test_supported_non_entity_page_can_be_resolved_or_downgraded(self):
        parsed = classify_search_input("https://thetvdb.com/search?query=glory")

        self.assertEqual(parsed.kind, "resolvable_link")
        self.assertEqual(parsed.urls, (
            "https://thetvdb.com/search?query=glory",
        ))

    def test_short_wikipedia_link_requires_resolution(self):
        parsed = classify_search_input("分享 https://w.wiki/AbCd")

        self.assertEqual(parsed.kind, "resolvable_link")
        self.assertEqual(parsed.link.provider, "wikipedia")

    def test_duplicate_links_for_same_entity_are_one_link(self):
        parsed = classify_search_input(
            "https://movie.douban.com/subject/1/ "
            "https://m.douban.com/movie/subject/1/"
        )

        self.assertEqual(parsed.kind, "link")
        self.assertEqual(parsed.link.entity_id, "1")

    def test_multiple_distinct_entities_are_rejected(self):
        parsed = classify_search_input(
            "https://movie.douban.com/subject/1/ "
            "https://movie.douban.com/subject/2/"
        )

        self.assertEqual(parsed.kind, "invalid_link")
        self.assertEqual(parsed.reason, "multiple_metadata_entities")

    def test_provider_lookalike_domains_are_not_trusted_as_metadata_links(self):
        for url in (
            "https://thetvdb.com.evil.example/series/411469",
            "https://eviltvdb.com/series/411469",
            "https://movie.douban.com.evil.example/subject/35314632/",
            "https://fakedouban.com/subject/35314632/",
        ):
            with self.subTest(url=url):
                parsed = classify_search_input(url)

                self.assertNotEqual(parsed.kind, "link")
                self.assertNotEqual(parsed.kind, "invalid_link")


if __name__ == "__main__":
    unittest.main()
