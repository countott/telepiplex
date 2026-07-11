import unittest
from unittest.mock import patch

from app.utils.ai import infer_download_plan_with_ai, infer_search_hypotheses_with_ai


class SearchAiPipelineTest(unittest.TestCase):
    @patch("app.utils.ai.check_ai_api_available", return_value=True)
    @patch("app.utils.ai.chat_completion")
    def test_stage_one_returns_source_queries_without_prowlarr_query(
        self, chat_mock, _available
    ):
        chat_mock.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"status":"ok","hypotheses":[],"source_queries":{"wikipedia":["想见你 电影"],"douban":["想见你"],"tvdb":["Someday or One Day"]},"warnings":[],"prowlarr_query":"forbidden"}'
                    }
                }
            ]
        }
        result = infer_search_hypotheses_with_ai("想见你")
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("prowlarr_query", result)
        self.assertIn("wikipedia", result["source_queries"])

    @patch("app.utils.ai.check_ai_api_available", return_value=True)
    @patch("app.utils.ai.chat_completion")
    def test_stage_two_accepts_all_sources_down_and_returns_draft(
        self, chat_mock, _available
    ):
        chat_mock.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"schema_version":1,"plan_id":"p1","display_title":"想见你","english_title":"Someday or One Day The Movie","year":"2022","content_identity":"extension_movie","relation":{"type":"sequel","target_series_title":"Someday or One Day","target_series_year":"2019","source":"ai"},"placement":{"library_type":"series","category_kind":"live_action_series","season_number":0,"episode_number":null,"mapping_kind":"temporary_related_special","mapping_source":"local_allocator"},"source_entry":{"title":"想见你 (电影)","url":"https://zh.wikipedia.org/wiki/想見你_(電影)","provider":"wikipedia","availability":"server_down","verification":"ai_supplied_unverified"},"prowlarr_queries":["Someday or One Day The Movie 2022"],"evidence":{},"warnings":["Wikipedia 未实时验证"],"confirmed":false}'
                    }
                }
            ]
        }
        result = infer_download_plan_with_ai(
            {"sources": [{"source": "tvdb", "status": "server_down"}]}
        )
        self.assertEqual(
            result["placement"]["mapping_kind"], "temporary_related_special"
        )
        self.assertIsNone(result["placement"]["episode_number"])

    @patch("app.utils.ai.check_ai_api_available", return_value=False)
    def test_both_stages_fail_closed_without_ai(self, _available):
        self.assertIsNone(infer_search_hypotheses_with_ai("想见你"))
        self.assertIsNone(infer_download_plan_with_ai({"sources": []}))

    @patch("app.utils.ai.check_ai_api_available", return_value=True)
    @patch("app.utils.ai.chat_completion")
    def test_ai_inferred_tvdb_episode_keeps_explicit_warning(
        self, chat_mock, _available
    ):
        chat_mock.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"schema_version":1,"plan_id":"p2","display_title":"想见你","english_title":"Someday or One Day The Movie","year":"2022","content_identity":"extension_movie","relation":{"type":"sequel","target_series_title":"Someday or One Day","target_series_year":"2019","source":"ai"},"placement":{"library_type":"series","category_kind":"live_action_series","season_number":0,"episode_number":5,"mapping_kind":"ai_inferred_tvdb","mapping_source":"ai_only"},"source_entry":{"title":"想见你 (电影)","url":"https://zh.wikipedia.org/wiki/想見你_(電影)","provider":"wikipedia","availability":"server_down","verification":"ai_supplied_unverified"},"prowlarr_queries":["Someday or One Day The Movie 2022"],"evidence":{},"warnings":["S00E05 仅由 AI 推断，未实时通过 TVDB 校验"],"confirmed":false}'
                    }
                }
            ]
        }
        result = infer_download_plan_with_ai(
            {"sources": [{"source": "tvdb", "status": "server_down"}]}
        )
        self.assertEqual(result["placement"]["episode_number"], 5)
        self.assertIn("未实时通过 TVDB 校验", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
