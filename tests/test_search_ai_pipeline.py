import json
import unittest
from unittest.mock import patch

from telepiplex_media_search.ai import (
    infer_relation_hypotheses_with_ai,
    infer_search_hypotheses_with_ai,
)


class SearchAiPipelineTest(unittest.TestCase):
    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_intent_hint_is_converted_to_source_queries(self, chat_mock, _available):
        chat_mock.return_value = {"choices": [{"message": {"content": json.dumps({
            "status": "parsed",
            "title_hints": ["想见你"],
            "media_type_hint": "unknown",
            "scope_hint": "work",
            "season_number": None,
            "episode_number": None,
            "numeric_tokens": [],
            "relation_hint": "none",
            "clarification_reason": "",
        })}}]}

        result = infer_search_hypotheses_with_ai({"raw_query": "想见你"})

        self.assertNotIn("prowlarr_query", result)
        self.assertEqual(result["source_queries"]["tvdb"], ["想见你"])

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_intent_hint_rejects_stable_ids_and_final_contracts(
        self, chat_mock, _available
    ):
        chat_mock.return_value = {"choices": [{"message": {"content": json.dumps({
            "status": "parsed",
            "title_hints": ["想见你"],
            "media_type_hint": "movie",
            "scope_hint": "work",
            "season_number": None,
            "episode_number": None,
            "numeric_tokens": [],
            "relation_hint": "movie_version",
            "clarification_reason": "",
            "tvdb_id": "123",
            "media_metadata": {},
        })}}]}

        self.assertIsNone(infer_search_hypotheses_with_ai({"raw_query": "想见你"}))

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=False)
    def test_relation_fails_closed_without_ai(self, _available):
        self.assertIsNone(infer_relation_hypotheses_with_ai({"facts": []}))

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_relation_scout_returns_only_three_hypotheses(self, chat_mock, _available):
        chat_mock.return_value = {"choices": [{"message": {"content": json.dumps({
            "hypotheses": [
                {"candidate_key": "c1", "relation_type": "sequel", "fact_ids": ["d:1"]},
                {"candidate_key": "c1", "relation_type": "special", "fact_ids": ["t:1"]},
                {"candidate_key": "c2", "relation_type": "spin_off", "fact_ids": ["w:1"]},
                {"candidate_key": "c3", "relation_type": "prequel", "fact_ids": ["w:2"]},
            ]
        })}}]}

        result = infer_relation_hypotheses_with_ai({"facts": []})

        self.assertEqual(len(result["hypotheses"]), 3)
        self.assertIn("不得编造", chat_mock.call_args.args[0])

if __name__ == "__main__":
    unittest.main()
