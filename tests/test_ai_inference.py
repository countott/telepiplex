import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.utils.ai import infer_tvdb_episode_plan_with_ai, parse_ai_json_response


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
        self.assertEqual(plan["tvdb_series_id"], "79349")
        self.assertEqual(plan["episode_map"][0]["target_name"], "Dexter S01E01.mkv")


if __name__ == "__main__":
    unittest.main()
