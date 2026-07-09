import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.utils.ai import (
    chat_completion,
    infer_metadata_backfill_with_ai,
    infer_tvdb_episode_plan_with_ai,
    normalize_search_query_with_ai,
    parse_ai_json_response,
)


class AiInferenceTest(unittest.TestCase):
    def setUp(self):
        init.bot_config = {
            "ai": {
                "api_url": "https://api.example/v1",
                "api_key": "key",
                "model": "model",
            }
        }

    def test_parse_ai_json_response_supports_openai_choices(self):
        result = parse_ai_json_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"tvdb_series_id": "79349", "episode_map": []}\n```'
                        }
                    }
                ]
            }
        )

        self.assertEqual(result, {"tvdb_series_id": "79349", "episode_map": []})

    def test_parse_ai_json_response_handles_invalid_json_without_logger(self):
        old_logger = init.logger
        init.logger = None
        try:
            result = parse_ai_json_response({"choices": [{"message": {"content": "not-json"}}]})
        finally:
            init.logger = old_logger

        self.assertIsNone(result)

    @patch("app.utils.ai.requests.post")
    def test_chat_completion_uses_timeout_and_redacts_failure_response(self, post_mock):
        init.bot_config["ai"]["timeout"] = 12
        old_logger = init.logger
        init.logger = Mock()
        self.addCleanup(setattr, init, "logger", old_logger)
        response = Mock()
        response.status_code = 500
        response.text = (
            '{"access_token":"secret-access",'
            '"download_url":"https://download.example/private.mkv"}'
        )
        post_mock.return_value = response

        self.assertIsNone(chat_completion("prompt"))

        self.assertEqual(post_mock.call_args.kwargs["timeout"], 12)
        log_message = init.logger.warn.call_args.args[0]
        self.assertIn("***redacted***", log_message)
        self.assertNotIn("secret-access", log_message)
        self.assertNotIn("https://download.example/private.mkv", log_message)

    @patch("app.utils.ai.chat_completion")
    def test_search_query_normalization_logs_input_and_raw_response(self, chat_mock):
        old_logger = init.logger
        init.logger = Mock()
        self.addCleanup(setattr, init, "logger", old_logger)
        chat_mock.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"status":"ok","lookup_candidates":[{"query":"Rick and Morty Season 9","title":"Rick and Morty","scope":"episode","season_number":9,"episode_number":7}],"warnings":[]}'
                    }
                }
            ]
        }

        plan = normalize_search_query_with_ai("瑞克和莫迪season9e07")

        self.assertEqual(plan["status"], "ok")
        logged = "\n".join(call.args[0] for call in init.logger.info.call_args_list)
        self.assertIn("AI搜索清洗输入 raw=瑞克和莫迪season9e07", logged)
        self.assertIn("AI搜索清洗原始响应", logged)

    @patch("app.utils.ai.chat_completion")
    def test_infer_tvdb_episode_plan_with_ai_builds_prompt_and_returns_json(self, chat_mock):
        chat_mock.return_value = {
            "choices": [
                {
                    "message": {
                        "content": """
                        {
                          "tvdb_series_id": "79349",
                          "series_name": "Dexter",
                          "season_type": "official",
                          "evidence": {"title_match": true, "year_match": true},
                          "episode_map": [
                            {
                              "source_file": "Dexter.S01E01.mkv",
                              "target_name": "Dexter S01E01.mkv",
                              "tvdb_episode_id": 349232,
                              "season_number": 1,
                              "episode_number": 1
                            }
                          ],
                          "warnings": []
                        }
                        """
                    }
                }
            ]
        }

        plan = infer_tvdb_episode_plan_with_ai(
            {
                "metadata": {"english_title": "Dexter", "year": "2006"},
                "tvdb_candidates": [{"tvdb_series_id": "79349", "name": "Dexter"}],
                "tvdb_episodes": [{"tvdb_episode_id": 349232, "season_number": 1, "episode_number": 1}],
                "file_tree": [{"name": "Dexter.S01E01.mkv"}],
                "release_title": "Dexter.2006.S01.1080p",
            }
        )

        prompt = chat_mock.call_args.args[0]
        self.assertIn("只返回JSON", prompt)
        self.assertIn("episode_map", prompt)
        self.assertIn("target_relative_path", prompt)
        self.assertIn("Series Name Season 01/Series Name S01E01.ext", prompt)
        self.assertIn("单个视频文件也可能是剧集单集", prompt)
        self.assertIn("不要仅因为 file_tree 只有一个视频文件就判定为电影", prompt)
        self.assertEqual(plan["tvdb_series_id"], "79349")
        self.assertEqual(plan["episode_map"][0]["target_name"], "Dexter S01E01.mkv")

    @patch("app.utils.ai.chat_completion")
    def test_infer_metadata_backfill_with_ai_requires_chinese_and_english_titles(self, chat_mock):
        chat_mock.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"status":"ok","media_type":"series","chinese_title":"瑞克和莫蒂 第九季",'
                            '"english_title":"Rick and Morty","year":"2026",'
                            '"external_ids":{"tvdb":"275274"}}'
                        )
                    }
                }
            ]
        }

        result = infer_metadata_backfill_with_ai(
            {
                "english_title": "Rick and Morty",
                "year": "2026",
                "external_ids": {"tvdb": "275274"},
            }
        )

        self.assertEqual(result["chinese_title"], "瑞克和莫蒂 第九季")
        self.assertEqual(result["english_title"], "Rick and Morty")
        self.assertEqual(result["external_ids"], {"tvdb": "275274"})
        self.assertEqual(result["source"], "ai_metadata_backfill")
        prompt = chat_mock.call_args.args[0]
        self.assertIn("补全媒体库重命名元数据", prompt)
        self.assertIn("不要编造外部ID", prompt)


if __name__ == "__main__":
    unittest.main()
