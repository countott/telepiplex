import unittest

from telepiplex_media_search.input_contract import (
    classify_search_input,
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

    def test_year_is_not_an_ambiguous_bare_number(self):
        parsed = classify_search_input("蝙蝠侠 1989")

        self.assertEqual(parsed.year, "1989")
        self.assertFalse(has_ambiguous_bare_number("蝙蝠侠 1989", parsed))

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

    def test_tvdb_work_season_and_episode_links(self):
        series = classify_search_input("https://thetvdb.com/series/411469")
        season = classify_search_input("https://thetvdb.com/seasons/205768")
        episode = classify_search_input("https://thetvdb.com/episodes/9481027")

        self.assertEqual((series.link.media_type, series.link.scope), ("series", "work"))
        self.assertEqual((season.link.media_type, season.link.scope), ("series", "season"))
        self.assertEqual((episode.link.media_type, episode.link.scope), ("series", "episode"))

    def test_malformed_supported_link_does_not_become_text_search(self):
        parsed = classify_search_input("https://thetvdb.com/search?query=glory")

        self.assertEqual(parsed.kind, "invalid_link")
        self.assertEqual(parsed.reason, "unsupported_metadata_link")


if __name__ == "__main__":
    unittest.main()
