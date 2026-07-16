import json
import unittest
from unittest.mock import patch

from telepiplex_media_search.ai import (
    infer_media_metadata_draft_with_ai,
    infer_relation_hypotheses_with_ai,
    infer_search_hypotheses_with_ai,
    score_candidates_with_ai,
)


class SearchAiPipelineTest(unittest.TestCase):
    def _draft(self):
        return {
            "plan_id": "plan-a",
            "media_metadata": {
                "schema_version": 1,
                "metadata_id": "",
                "confirmed": False,
                "identity": {
                    "chinese_title": "想见你",
                    "english_title": "Someday or One Day The Movie",
                    "year": "2022",
                    "content_kind": "extension_movie",
                    "summary": "电影版延续电视剧故事。",
                    "original_release_date": "2022-12-24",
                    "poster_url": "https://image.example/poster.jpg",
                    "poster_source": "douban",
                    "external_ids": {},
                },
                "relation": {
                    "type": "sequel",
                    "target_series": {
                        "chinese_title": "想见你",
                        "english_title": "Someday or One Day",
                        "year": "2019",
                        "external_ids": {},
                    },
                    "source": "wikipedia",
                },
                "placement": {
                    "library_type": "series",
                    "category_kind": "live_action_series",
                    "season_number": 0,
                    "episode_number": None,
                    "mapping_kind": "temporary_related_special",
                    "mapping_source": "local_allocator",
                    "tvdb_episode_id": "",
                },
                "source_entry": {
                    "title": "想见你 (电影)",
                    "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
                    "provider": "wikipedia",
                    "verification": "verified",
                },
                "items": [],
                "evidence": {},
                "warnings": [],
            },
            "prowlarr_queries": ["Someday or One Day The Movie 2022"],
        }

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_stage_one_returns_source_queries_without_prowlarr_query(
        self, chat_mock, _available
    ):
        chat_mock.return_value = {
            "choices": [{
                "message": {
                    "content": '{"status":"ok","hypotheses":[],"source_queries":{"wikipedia":["想见你 电影"],"douban":["想见你"],"tvdb":["Someday or One Day"]},"warnings":[],"prowlarr_query":"forbidden"}'
                }
            }]
        }
        result = infer_search_hypotheses_with_ai({
            "raw_query": "想见你",
            "intent": {"title": "想见你", "scope": "movie_or_series"},
            "sources": [{"source": "wikipedia", "status": "ok"}],
            "gate_reason_codes": ["ambiguous_candidates"],
        })
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("prowlarr_query", result)
        self.assertIn("wikipedia", result["source_queries"])
        prompt = chat_mock.call_args.args[0]
        self.assertIn("ambiguous_candidates", prompt)
        self.assertIn("wikipedia", prompt)

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_stage_two_returns_search_local_queries_and_nested_contract(
        self, chat_mock, _available
    ):
        chat_mock.return_value = {
            "choices": [{"message": {"content": json.dumps(
                self._draft(), ensure_ascii=False
            )}}]
        }
        payload = infer_media_metadata_draft_with_ai({"sources": []})
        self.assertIn("media_metadata", payload)
        self.assertIn("prowlarr_queries", payload)
        self.assertNotIn("_".join(("download", "plan")), payload)

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=False)
    def test_both_stages_fail_closed_without_ai(self, _available):
        self.assertIsNone(infer_search_hypotheses_with_ai("想见你"))
        self.assertIsNone(infer_media_metadata_draft_with_ai({"sources": []}))

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_ai_inferred_tvdb_episode_keeps_explicit_warning(
        self, chat_mock, _available
    ):
        draft = self._draft()
        draft["media_metadata"]["placement"].update({
            "episode_number": 5,
            "mapping_kind": "ai_inferred_tvdb",
            "mapping_source": "ai_only",
        })
        draft["media_metadata"]["warnings"] = [
            "S00E05 仅由 AI 推断，未实时通过 TVDB 校验"
        ]
        chat_mock.return_value = {
            "choices": [{"message": {"content": json.dumps(
                draft, ensure_ascii=False
            )}}]
        }
        result = infer_media_metadata_draft_with_ai({
            "sources": [{"source": "tvdb", "status": "server_down"}]
        })
        contract = result["media_metadata"]
        self.assertEqual(contract["placement"]["episode_number"], 5)
        self.assertIn("未实时通过 TVDB 校验", contract["warnings"][0])

    @patch("telepiplex_media_search.ai.check_ai_api_available", return_value=True)
    @patch("telepiplex_media_search.ai.chat_completion")
    def test_relation_scout_returns_only_three_hypotheses(
        self, chat_mock, _available
    ):
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
            "media_metadata": {},
            "scorecards": [],
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
