import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init


class SearchResolutionTest(unittest.TestCase):
    def test_parse_search_intent_extracts_sxxeyy_scope(self):
        from app.utils.search_resolution import parse_search_intent

        intent = parse_search_intent("Dexter Original Sin S02E05")

        self.assertEqual(intent["title"], "Dexter Original Sin")
        self.assertEqual(intent["scope"], "episode")
        self.assertEqual(intent["season_number"], 2)
        self.assertEqual(intent["episode_number"], 5)

    def test_parse_search_intent_extracts_chinese_episode_scope(self):
        from app.utils.search_resolution import parse_search_intent

        intent = parse_search_intent("绝命毒师 第2季第5集")

        self.assertEqual(intent["title"], "绝命毒师")
        self.assertEqual(intent["scope"], "episode")
        self.assertEqual(intent["season_number"], 2)
        self.assertEqual(intent["episode_number"], 5)

    def test_parse_search_intent_extracts_chinese_numeral_episode_scope(self):
        from app.utils.search_resolution import parse_search_intent

        intent = parse_search_intent("瑞克和莫迪第九季第七集")

        self.assertEqual(intent["title"], "瑞克和莫迪")
        self.assertEqual(intent["scope"], "episode")
        self.assertEqual(intent["season_number"], 9)
        self.assertEqual(intent["episode_number"], 7)

    def test_parse_search_intent_leaves_double_episode_marker_for_ai_normalization(self):
        from app.utils.search_resolution import parse_search_intent

        intent = parse_search_intent("瑞克和莫迪第九集第七集")

        self.assertEqual(intent["title"], "瑞克和莫迪第九集第七集")
        self.assertEqual(intent["scope"], "movie_or_series")
        self.assertIsNone(intent["season_number"])
        self.assertIsNone(intent["episode_number"])

    def test_parse_search_intent_extracts_chinese_equivalent_episode_expressions(self):
        from app.utils.search_resolution import parse_search_intent

        examples = [
            "瑞克和莫迪九季七集",
            "瑞克和莫迪第九季七话",
            "瑞克和莫迪9季第7話",
        ]

        for query in examples:
            with self.subTest(query=query):
                intent = parse_search_intent(query)

                self.assertEqual(intent["title"], "瑞克和莫迪")
                self.assertEqual(intent["scope"], "episode")
                self.assertEqual(intent["season_number"], 9)
                self.assertEqual(intent["episode_number"], 7)

    def test_parse_search_intent_extracts_english_equivalent_episode_expressions(self):
        from app.utils.search_resolution import parse_search_intent

        examples = [
            "Rick and Morty Season 9 Episode 7",
            "Rick and Morty season nine ep seven",
            "Rick and Morty 9x07",
        ]

        for query in examples:
            with self.subTest(query=query):
                intent = parse_search_intent(query)

                self.assertEqual(intent["title"], "Rick and Morty")
                self.assertEqual(intent["scope"], "episode")
                self.assertEqual(intent["season_number"], 9)
                self.assertEqual(intent["episode_number"], 7)

    def test_parse_search_intent_extracts_english_word_number_season_scope(self):
        from app.utils.search_resolution import parse_search_intent

        intent = parse_search_intent("Rick and Morty season nine")

        self.assertEqual(intent["title"], "Rick and Morty")
        self.assertEqual(intent["scope"], "season")
        self.assertEqual(intent["season_number"], 9)

    def test_candidate_to_prowlarr_query_uses_confirmed_scope(self):
        from app.utils.search_resolution import candidate_to_prowlarr_query

        movie = {"media_type": "movie", "title": "The Batman", "year": "2022", "scope": "movie"}
        season = {"media_type": "series", "title": "Breaking Bad", "scope": "season", "season_number": 2}
        episode = {
            "media_type": "series",
            "title": "Breaking Bad",
            "scope": "episode",
            "season_number": 2,
            "episode_number": 5,
        }

        self.assertEqual(candidate_to_prowlarr_query(movie), "The Batman 2022")
        self.assertEqual(candidate_to_prowlarr_query(season), "Breaking Bad S02")
        self.assertEqual(candidate_to_prowlarr_query(episode), "Breaking Bad S02E05")

    def test_candidate_to_prowlarr_query_prefers_clean_english_original_title_for_movies(self):
        from app.utils.search_resolution import candidate_to_prowlarr_query

        movie = {
            "media_type": "movie",
            "scope": "movie",
            "title": "变形金刚4：绝迹重生",
            "chinese_title": "变形金刚4：绝迹重生",
            "english_title": "Transformers: Age-of-Extinction",
            "year": "2014",
        }

        self.assertEqual(candidate_to_prowlarr_query(movie), "Transformers Age of Extinction 2014")

    def test_candidate_to_prowlarr_query_removes_season_suffix_before_episode_marker(self):
        from app.utils.search_resolution import candidate_to_prowlarr_query

        episode = {
            "media_type": "series",
            "scope": "episode",
            "title": "瑞克和莫蒂 第九季",
            "english_title": "Rick and Morty Season 9",
            "season_number": 9,
            "episode_number": 7,
        }

        self.assertEqual(candidate_to_prowlarr_query(episode), "Rick and Morty S09E07")

    def test_candidate_to_prowlarr_query_removes_season_suffix_for_season_scope(self):
        from app.utils.search_resolution import candidate_to_prowlarr_query

        for english_title in ("Rick and Morty Season 9", "Rick and Morty Season Nine"):
            with self.subTest(english_title=english_title):
                season = {
                    "media_type": "series",
                    "scope": "season",
                    "title": "瑞克和莫蒂 第九季",
                    "english_title": english_title,
                    "season_number": 9,
                }

                self.assertEqual(candidate_to_prowlarr_query(season), "Rick and Morty S09")

    def test_unreleased_episode_detection_uses_air_date(self):
        from app.utils.search_resolution import is_unreleased_episode

        self.assertFalse(is_unreleased_episode({"aired": "2026-01-01"}, today=date(2026, 7, 7)))
        self.assertTrue(is_unreleased_episode({"aired": "2026-12-01"}, today=date(2026, 7, 7)))
        self.assertTrue(is_unreleased_episode({"aired": ""}, today=date(2026, 7, 7)))

    def test_build_confirmation_candidates_recommends_requested_episode_and_blocks_unreleased(self):
        from app.utils.search_resolution import build_confirmation_candidates

        entries = [
            {
                "media_type": "series",
                "title": "Breaking Bad",
                "chinese_title": "绝命毒师",
                "year": "2008",
                "external_ids": {"tvdb": "81189"},
            }
        ]
        intent = {"scope": "episode", "season_number": 2, "episode_number": 5}
        episodes = {
            "81189": [
                {"season_number": 2, "episode_number": 5, "aired": "2009-04-05"},
                {"season_number": 2, "episode_number": 6, "aired": "2099-01-01"},
            ]
        }

        candidates = build_confirmation_candidates(entries, intent, episodes, today=date(2026, 7, 7))

        self.assertEqual(candidates[0]["scope"], "episode")
        self.assertEqual(candidates[0]["season_number"], 2)
        self.assertEqual(candidates[0]["episode_number"], 5)
        self.assertTrue(candidates[0]["recommended"])
        self.assertFalse(any(candidate.get("episode_number") == 6 for candidate in candidates))

    def test_build_confirmation_candidates_lists_aired_episodes_for_unfinished_requested_season(self):
        from app.utils.search_resolution import build_confirmation_candidates

        entries = [
            {
                "media_type": "series",
                "title": "Rick and Morty",
                "chinese_title": "瑞克和莫蒂",
                "year": "2026",
                "external_ids": {"tvdb": "275274"},
            }
        ]
        intent = {"scope": "season", "season_number": 9}
        episodes = {
            "275274": [
                {"season_number": 9, "episode_number": 1, "aired": "2026-05-25"},
                {"season_number": 9, "episode_number": 7, "aired": "2026-07-06"},
                {"season_number": 9, "episode_number": 8, "aired": "2026-07-13"},
                {"season_number": 9, "episode_number": 9, "aired": ""},
            ]
        }

        candidates = build_confirmation_candidates(entries, intent, episodes, today=date(2026, 7, 7))

        self.assertEqual([candidate["scope"] for candidate in candidates], ["episode", "episode"])
        self.assertEqual(
            [(candidate["season_number"], candidate["episode_number"]) for candidate in candidates],
            [(9, 7), (9, 1)],
        )
        self.assertTrue(candidates[0]["recommended"])

    @patch("app.utils.ai.chat_completion")
    def test_ai_query_normalization_returns_lookup_candidates_only(self, chat_mock):
        from app.utils.ai import normalize_search_query_with_ai

        old_config = init.bot_config
        init.bot_config = {"ai": {"api_url": "http://ai.example", "model": "model", "api_key": "key"}}
        self.addCleanup(setattr, init, "bot_config", old_config)
        chat_mock.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"status":"ok","lookup_candidates":[{"query":"Breaking Bad","title":"Breaking Bad","scope":"episode","season_number":2,"episode_number":5}]}'
                    }
                }
            ]
        }

        result = normalize_search_query_with_ai("绝名毒师 第二季第五集")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["lookup_candidates"][0]["query"], "Breaking Bad")
        self.assertNotIn("prowlarr_query", result["lookup_candidates"][0])

    @patch("app.utils.ai.chat_completion")
    def test_ai_query_normalization_prompt_allows_obvious_chinese_season_episode_typo(self, chat_mock):
        from app.utils.ai import normalize_search_query_with_ai

        old_config = init.bot_config
        init.bot_config = {"ai": {"api_url": "http://ai.example", "model": "model", "api_key": "key"}}
        self.addCleanup(setattr, init, "bot_config", old_config)
        chat_mock.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"status":"ok","lookup_candidates":[{"query":"Rick and Morty","title":"Rick and Morty","scope":"episode","season_number":9,"episode_number":7}]}'
                    }
                }
            ]
        }

        normalize_search_query_with_ai("瑞克和莫迪第九集第七集")

        prompt = chat_mock.call_args.args[0]
        self.assertIn("第九集第七集", prompt)
        self.assertIn("第九季第七集", prompt)
        self.assertIn("明显口误", prompt)
        self.assertIn("输出前自检", prompt)
        self.assertIn("不确定", prompt)

    @patch("app.utils.ai.chat_completion")
    def test_ai_verified_match_without_external_id_blocks(self, chat_mock):
        from app.utils.ai import infer_verified_search_match_with_ai

        old_config = init.bot_config
        init.bot_config = {"ai": {"api_url": "http://ai.example", "model": "model", "api_key": "key"}}
        self.addCleanup(setattr, init, "bot_config", old_config)
        chat_mock.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"status":"blocked_no_verified_match","candidates":[]}'
                    }
                }
            ]
        }

        result = infer_verified_search_match_with_ai("some unclear show")

        self.assertEqual(result["status"], "blocked_no_verified_match")
        self.assertEqual(result["candidates"], [])


if __name__ == "__main__":
    unittest.main()
