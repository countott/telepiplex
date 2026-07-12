import json
import unittest
from unittest.mock import patch

from telepiplex_media_search.ai import (
    infer_media_metadata_draft_with_ai,
    infer_search_hypotheses_with_ai,
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
        result = infer_search_hypotheses_with_ai("想见你")
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("prowlarr_query", result)
        self.assertIn("wikipedia", result["source_queries"])

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


if __name__ == "__main__":
    unittest.main()
