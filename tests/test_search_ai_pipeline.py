import json
import unittest
from unittest.mock import patch

from telepiplex_media_search.ai import (
    infer_relation_hypotheses_with_ai,
    infer_search_hypotheses_with_ai,
    score_candidates_with_ai,
)


class SearchAiPipelineTest(unittest.TestCase):
    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_query_hypotheses_never_return_prowlarr_query(self, chat_mock, _available):
        chat_mock.return_value = {"choices": [{"message": {"content": json.dumps({
            "status": "ok",
            "hypotheses": [],
            "source_queries": {"wikipedia": ["想见你"], "douban": [], "tvdb": []},
            "warnings": [],
            "prowlarr_query": "forbidden",
        })}}]}

        result = infer_search_hypotheses_with_ai({"raw_query": "想见你"})

        self.assertNotIn("prowlarr_query", result)

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=False)
    def test_relation_and_score_fail_closed_without_ai(self, _available):
        self.assertIsNone(infer_relation_hypotheses_with_ai({"facts": []}))
        self.assertIsNone(score_candidates_with_ai({"candidates": []}))

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

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_scorecard_rejects_full_metadata_shape(self, chat_mock, _available):
        chat_mock.return_value = {"choices": [{"message": {"content": json.dumps({
            "media_metadata": {}, "scorecards": [],
        })}}]}

        self.assertIsNone(score_candidates_with_ai({"candidates": []}))

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_scorecard_returns_only_strict_array(self, chat_mock, _available):
        scorecard = {
            "candidate_key": "c1",
            "title_equivalence": {"score": 20, "fact_ids": ["d:1"]},
            "relation_consistency": {"score": 5, "fact_ids": ["d:1"]},
            "intent_relevance": {"score": 10, "fact_ids": ["d:1"]},
        }
        chat_mock.return_value = {"choices": [{"message": {"content": json.dumps({
            "scorecards": [scorecard]
        })}}]}

        result = score_candidates_with_ai({"candidates": []})

        self.assertEqual(result, {"scorecards": [scorecard]})
        self.assertEqual(chat_mock.call_args.kwargs["max_tokens"], 2200)


if __name__ == "__main__":
    unittest.main()
