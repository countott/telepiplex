import json
import unittest
from unittest.mock import patch

import requests

from telepiplex_search.ai import (
    chat_completion_messages,
    extract_ai_message,
    infer_candidate_scorecard_with_ai,
    infer_relation_hypotheses_with_ai,
    infer_search_hypotheses_with_ai,
)
from telepiplex_search.context import runtime_context


class SearchAiPipelineTest(unittest.TestCase):
    def setUp(self):
        runtime_context.configure({
            "ai": {
                "enable": True,
                "api_url": "https://ai.example/v1",
                "api_key": "secret-key",
                "model": "tool-model",
                "timeout": 12,
            },
        })

    @patch("telepiplex_search.ai.requests.post")
    def test_tool_transport_sends_messages_tools_and_forced_choice(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [],
                },
            }],
        }
        tools = [{
            "type": "function",
            "function": {
                "name": "search_media_sources",
                "parameters": {"type": "object"},
            },
        }]

        result = chat_completion_messages(
            [
                {"role": "system", "content": "system-contract"},
                {"role": "user", "content": "query"},
            ],
            tools=tools,
            tool_choice={
                "type": "function",
                "function": {"name": "search_media_sources"},
            },
            thinking_mode="enabled",
            max_tokens=2048,
        )

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "tool-model")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(
            payload["tools"][0]["function"]["name"],
            "search_media_sources",
        )
        self.assertEqual(
            payload["tool_choice"]["function"]["name"],
            "search_media_sources",
        )
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["max_tokens"], 2048)
        self.assertEqual(
            extract_ai_message(result),
            {
                "role": "assistant",
                "tool_calls": [],
            },
        )

    @patch("telepiplex_search.ai.requests.post")
    def test_tool_transport_omits_tool_fields_for_plain_messages(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
        }

        chat_completion_messages([{"role": "user", "content": "query"}])

        payload = post.call_args.kwargs["json"]
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        self.assertNotIn("thinking", payload)

    @patch("telepiplex_search.ai.requests.post")
    def test_non_200_preserves_structured_provider_error(self, post):
        post.return_value.status_code = 400
        post.return_value.headers = {"x-request-id": "req-123"}
        post.return_value.json.return_value = {
            "error": {
                "message": "Thinking mode does not support this tool_choice",
                "type": "invalid_request_error",
                "param": "tool_choice",
                "code": "invalid_request_error",
            },
        }

        result = chat_completion_messages([
            {"role": "user", "content": "query"},
        ])

        self.assertEqual(result, {
            "error": {
                "kind": "provider_invalid_request",
                "http_status": 400,
                "code": "invalid_request_error",
                "type": "invalid_request_error",
                "param": "tool_choice",
                "message": "Thinking mode does not support this tool_choice",
                "retryable": False,
                "request_id": "req-123",
            },
        })

    @patch("telepiplex_search.ai.requests.post")
    def test_timeout_preserves_retryable_provider_kind(self, post):
        post.side_effect = requests.Timeout("provider took too long")

        result = chat_completion_messages([
            {"role": "user", "content": "query"},
        ])

        self.assertEqual(result["error"]["kind"], "provider_timeout")
        self.assertTrue(result["error"]["retryable"])
        self.assertEqual(result["error"]["http_status"], 0)
        self.assertEqual(result["error"]["message"], "provider took too long")

    @patch("telepiplex_search.ai.requests.post")
    def test_connection_error_preserves_provider_unavailable_kind(self, post):
        post.side_effect = requests.ConnectionError("provider offline")

        result = chat_completion_messages([
            {"role": "user", "content": "query"},
        ])

        self.assertEqual(result["error"]["kind"], "provider_unavailable")
        self.assertTrue(result["error"]["retryable"])
        self.assertEqual(result["error"]["message"], "provider offline")

    @patch("telepiplex_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_search.ai.chat_completion")
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

    @patch("telepiplex_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_search.ai.chat_completion")
    def test_needs_clarification_preserves_title_hints_and_reason(
        self,
        chat_mock,
        _available,
    ):
        chat_mock.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "status": "needs_clarification",
                "title_hints": ["康斯坦汀", "康斯坦丁"],
                "media_type_hint": "unknown",
                "scope_hint": "unknown",
                "season_number": None,
                "episode_number": None,
                "numeric_tokens": [],
                "relation_hint": "unknown",
                "clarification_reason": "可能指电影或剧集。",
            })}}],
        }

        result = infer_search_hypotheses_with_ai({
            "raw_query": "康斯坦汀",
        })

        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(
            result["source_queries"]["douban"],
            ["康斯坦汀", "康斯坦丁"],
        )
        self.assertEqual(
            result["clarification_reason"],
            "可能指电影或剧集。",
        )

    @patch("telepiplex_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_search.ai.chat_completion")
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

    @patch("telepiplex_search.ai.check_ai_api_available", return_value=False)
    def test_relation_fails_closed_without_ai(self, _available):
        self.assertIsNone(infer_relation_hypotheses_with_ai({"facts": []}))

    @patch("telepiplex_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_search.ai.chat_completion")
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

    @patch("telepiplex_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_search.ai.chat_completion")
    def test_candidate_scorecard_returns_only_score_objects(
        self,
        chat_mock,
        _available,
    ):
        chat_mock.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "scores": [{
                    "candidate_key": "tvdb:series:1",
                    "title_equivalence": 18,
                    "intent_relevance": 9,
                    "relation_consistency": 8,
                    "fact_ids": ["tvdb:1"],
                }],
            })}}],
        }

        result = infer_candidate_scorecard_with_ai({
            "candidates": [{"candidate_key": "tvdb:series:1"}],
        })

        self.assertEqual(
            result["scores"][0]["candidate_key"],
            "tvdb:series:1",
        )
        prompt = chat_mock.call_args.args[0]
        self.assertIn("只能引用输入中的 candidate_key 和 fact_id", prompt)
        self.assertIn("不得输出或修改标题、年份", prompt)

    @patch("telepiplex_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_search.ai.chat_completion")
    def test_candidate_scorecard_preserves_more_than_seven_scores(
        self,
        chat_mock,
        _available,
    ):
        scores = [{
            "candidate_key": f"candidate:{index}",
            "title_equivalence": 10,
            "intent_relevance": 5,
            "relation_consistency": 5,
            "fact_ids": [f"fact:{index}"],
        } for index in range(8)]
        chat_mock.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({"scores": scores}),
                },
            }],
        }

        result = infer_candidate_scorecard_with_ai({
            "candidates": [
                {"candidate_key": item["candidate_key"]}
                for item in scores
            ],
        })

        self.assertEqual(result["scores"], scores)

if __name__ == "__main__":
    unittest.main()
