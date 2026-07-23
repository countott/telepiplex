import unittest

from telepiplex_search.deterministic import (
    build_rule_hypotheses,
    evaluate_deterministic_plan,
)
from telepiplex_search.search_plan import TemporarySpecialAllocator, finalize_search_plan


def wikipedia_movie(*, year="2010", english="Inception"):
    return {
        "source": "wikipedia",
        "status": "ok",
        "facts": [
            {
                "language": "zh",
                "title": "盗梦空间",
                "chinese_title": "盗梦空间",
                "english_title": "",
                "year": year,
                "media_type": "movie",
                "wikibase_item": "Q25188",
                "url": "https://zh.wikipedia.org/wiki/盗梦空间",
            },
            {
                "language": "en",
                "title": english,
                "chinese_title": "",
                "english_title": english,
                "year": year,
                "media_type": "movie",
                "wikibase_item": "Q25188",
                "url": "https://en.wikipedia.org/wiki/Inception",
            },
        ],
        "source_urls": [
            "https://zh.wikipedia.org/wiki/盗梦空间",
            "https://en.wikipedia.org/wiki/Inception",
        ],
        "error": "",
    }


def douban_movie(*, year="2010", english="Inception"):
    return {
        "source": "douban",
        "status": "ok",
        "facts": [{
            "subject_id": "3541415",
            "external_ids": {"douban_subject": "3541415"},
            "url": "https://movie.douban.com/subject/3541415/",
            "title": english,
            "chinese_title": "盗梦空间",
            "english_title": english,
            "year": year,
            "media_type": "movie",
            "aliases": [],
            "genres": ["剧情", "科幻"],
            "cover_url": "",
        }],
        "source_urls": ["https://movie.douban.com/subject/3541415/"],
        "error": "",
    }


def douban_series():
    return {
        "source": "douban",
        "status": "ok",
        "facts": [{
            "subject_id": "35314632",
            "external_ids": {"douban_subject": "35314632"},
            "url": "https://movie.douban.com/subject/35314632/",
            "title": "The Glory",
            "chinese_title": "黑暗荣耀",
            "english_title": "The Glory",
            "year": "2022",
            "media_type": "series",
            "aliases": [],
            "genres": ["剧情"],
            "cover_url": "",
        }],
        "source_urls": ["https://movie.douban.com/subject/35314632/"],
        "error": "",
    }


def tvdb_series(episodes=None):
    episodes = episodes if episodes is not None else [
        {
            "tvdb_episode_id": "ep-1",
            "name": "Episode 1",
            "season_number": 1,
            "episode_number": 1,
            "aired": "2022-12-30",
        },
        {
            "tvdb_episode_id": "ep-2",
            "name": "Episode 2",
            "season_number": 1,
            "episode_number": 2,
            "aired": "2022-12-30",
        },
    ]
    return {
        "source": "tvdb",
        "status": "ok",
        "facts": [{
            "hypothesis": {"title": "黑暗荣耀", "year": "2022"},
            "movies": [],
            "series": [{
                "tvdb_id": "411469",
                "tvdb_series_id": "411469",
                "media_type": "series",
                "name": "The Glory",
                "english_title": "The Glory",
                "year": "2022",
                "aliases": ["黑暗荣耀"],
            }],
            "episodes_by_series": {"411469": episodes},
        }],
        "source_urls": ["https://thetvdb.com/series/411469"],
        "error": "",
    }


class RuleHypothesesTest(unittest.TestCase):
    def test_builds_provider_queries_without_ai(self):
        result = build_rule_hypotheses("黑暗荣耀 S01E02 2022")

        self.assertEqual(result["intent"]["scope"], "episode")
        self.assertEqual(result["intent"]["season_number"], 1)
        self.assertEqual(result["intent"]["episode_number"], 2)
        self.assertEqual(result["source_queries"]["douban"], ["黑暗荣耀 2022"])

    def test_whole_series_words_are_removed_from_provider_query(self):
        result = build_rule_hypotheses("黑暗荣耀 全季 2022")

        self.assertEqual(result["intent"]["scope"], "whole_series")
        self.assertEqual(result["intent"]["title"], "黑暗荣耀")
        self.assertEqual(result["source_queries"]["tvdb"], ["黑暗荣耀 2022"])


class DeterministicPlannerTest(unittest.TestCase):
    def test_two_sources_create_movie_plan(self):
        result = evaluate_deterministic_plan(
            "plan-movie",
            "盗梦空间 2010",
            [wikipedia_movie(), douban_movie()],
        )

        self.assertEqual(result.reason_codes, ())
        self.assertEqual(result.decision["mode"], "deterministic")
        self.assertEqual(result.decision["scope"], "movie")
        contract = result.plan["media_metadata"]
        self.assertEqual(contract["identity"]["english_title"], "Inception")
        self.assertEqual(contract["identity"]["chinese_title"], "盗梦空间")
        self.assertEqual(contract["placement"]["category_kind"], "live_action_movie")
        source_entry = contract["source_entry"]
        self.assertIn(source_entry["provider"], source_entry["url"])
        finalized = finalize_search_plan(
            result.plan, TemporarySpecialAllocator(), set()
        )
        self.assertEqual(finalized["prowlarr_queries"], ["Inception 2010"])

    def test_tvdb_and_douban_create_locked_episode_plan(self):
        result = evaluate_deterministic_plan(
            "plan-episode",
            "黑暗荣耀 S01E02 2022",
            [douban_series(), tvdb_series()],
        )

        self.assertEqual(result.reason_codes, ())
        contract = result.plan["media_metadata"]
        self.assertEqual(contract["identity"]["external_ids"]["tvdb"], "411469")
        self.assertEqual(
            [(item["season_number"], item["episode_number"]) for item in contract["items"]],
            [(1, 2)],
        )
        self.assertEqual(result.plan["prowlarr_queries"], ["The Glory S01E02"])
        self.assertIsNotNone(
            finalize_search_plan(result.plan, TemporarySpecialAllocator(), set())
        )

    def test_single_source_is_not_deterministic(self):
        result = evaluate_deterministic_plan(
            "plan-single", "盗梦空间 2010", [wikipedia_movie()]
        )

        self.assertIsNone(result.plan)
        self.assertIn("insufficient_independent_support", result.reason_codes)

    def test_conflicting_years_are_not_deterministic(self):
        result = evaluate_deterministic_plan(
            "plan-conflict",
            "盗梦空间",
            [wikipedia_movie(year="2010"), douban_movie(year="2022")],
        )

        self.assertIsNone(result.plan)
        self.assertIn("evidence_conflict", result.reason_codes)

    def test_missing_year_is_not_deterministic(self):
        result = evaluate_deterministic_plan(
            "plan-no-year",
            "盗梦空间",
            [wikipedia_movie(year=""), douban_movie(year="")],
        )

        self.assertIsNone(result.plan)
        self.assertIn("missing_year", result.reason_codes)

    def test_missing_tvdb_episode_is_not_deterministic(self):
        result = evaluate_deterministic_plan(
            "plan-missing",
            "黑暗荣耀 S01E09 2022",
            [douban_series(), tvdb_series()],
        )

        self.assertIsNone(result.plan)
        self.assertIn("tvdb_scope_not_verified", result.reason_codes)

    def test_unreleased_tvdb_episode_is_not_deterministic(self):
        result = evaluate_deterministic_plan(
            "plan-future",
            "黑暗荣耀 S01E02 2022",
            [
                douban_series(),
                tvdb_series(episodes=[{
                    "tvdb_episode_id": "future-2",
                    "name": "Future Episode",
                    "season_number": 1,
                    "episode_number": 2,
                    "aired": "2099-01-01",
                }]),
            ],
        )

        self.assertIsNone(result.plan)
        self.assertIn("tvdb_scope_not_verified", result.reason_codes)

    def test_complex_relation_signal_requires_ai(self):
        result = evaluate_deterministic_plan(
            "plan-special",
            "盗梦空间 Special 2010",
            [wikipedia_movie(), douban_movie()],
        )

        self.assertIsNone(result.plan)
        self.assertIn("complex_identity_requires_ai", result.reason_codes)

    def test_provider_relation_signal_requires_ai(self):
        wikipedia = wikipedia_movie()
        wikipedia["facts"][0]["extract"] = "这是同名电视剧的续集电影。"

        result = evaluate_deterministic_plan(
            "plan-provider-special",
            "盗梦空间 2010",
            [wikipedia, douban_movie()],
        )

        self.assertIsNone(result.plan)
        self.assertIn("complex_identity_requires_ai", result.reason_codes)

    def test_missing_bilingual_identity_requires_ai(self):
        result = evaluate_deterministic_plan(
            "plan-title",
            "盗梦空间 2010",
            [wikipedia_movie(english=""), douban_movie(english="")],
        )

        self.assertIsNone(result.plan)
        self.assertIn("missing_bilingual_identity", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
