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
