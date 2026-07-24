import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


class ConfigSchemaContractTest(unittest.TestCase):
    def test_schema_declares_independent_search_config_wizard(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(
            schema["x-telepiplex-config-command"],
            "search_config",
        )

    def test_ai_and_tvdb_are_visual_form_sections_with_write_only_keys(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        metadata = schema["properties"]["metadata"]
        tvdb = metadata["properties"]["tvdb"]
        ai = schema["properties"]["ai"]
        self.assertEqual(tvdb["title"], "TVDB")
        self.assertEqual(ai["title"], "AI")
        self.assertEqual(
            set(tvdb["properties"]),
            {"enable", "api_key", "base_url", "subscriber_pin", "timeout"},
        )
        self.assertEqual(
            set(ai["properties"]),
            {
                "enable",
                "api_url",
                "api_key",
                "model",
                "timeout",
                "source_orchestration",
            },
        )
        self.assertTrue(tvdb["properties"]["api_key"]["writeOnly"])
        self.assertTrue(tvdb["properties"]["subscriber_pin"]["writeOnly"])
        self.assertTrue(ai["properties"]["api_key"]["writeOnly"])

    def test_source_orchestration_and_douban_defaults_are_bounded(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))

        self.assertEqual(
            default["metadata"]["douban"],
            {
                "enable": True,
                "timeout": 10,
                "cache_ttl": 900,
                "max_concurrency": 2,
                "circuit_breaker_failures": 3,
                "circuit_breaker_seconds": 300,
            },
        )
        self.assertEqual(
            default["ai"]["source_orchestration"],
            {
                "enable": True,
                "max_targeted_rounds": 2,
                "max_tools_per_round": 3,
                "protocol": "openai_tools_v1",
                "thinking_mode": "enabled",
                "tool_choice_mode": "omit",
            },
        )
        orchestration = (
            schema["properties"]["ai"]["properties"]["source_orchestration"]
        )
        self.assertEqual(
            orchestration["properties"]["max_targeted_rounds"]["maximum"],
            2,
        )
        self.assertEqual(
            orchestration["properties"]["max_tools_per_round"]["maximum"],
            3,
        )
        self.assertEqual(
            orchestration["properties"]["thinking_mode"]["enum"],
            ["enabled", "disabled"],
        )
        self.assertEqual(
            orchestration["properties"]["tool_choice_mode"]["enum"],
            ["omit", "forced"],
        )

    def test_search_scoring_is_part_of_public_config_contract(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))

        search = schema["properties"]["search"]
        scoring = search["properties"]["scoring"]
        self.assertEqual(scoring["title"], "评分")
        self.assertEqual(
            set(scoring["properties"]),
            {
                "prefer_resolution",
                "prefer_source",
                "prefer_codec",
                "prefer_audio",
                "reject_keywords",
                "keyword_scores",
                "indexer_scores",
            },
        )
        self.assertIn("scoring", default["search"])
        self.assertIn("keyword_scores", default["search"]["scoring"])
        self.assertIn("indexer_scores", default["search"]["scoring"])

    def test_default_config_validates_against_schema(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(default)

    def test_prowlarr_result_limit_is_fixed_at_twelve(self):
        schema = json.loads(
            (ROOT / "config.schema.json").read_text(encoding="utf-8")
        )
        default = yaml.safe_load(
            (ROOT / "config.default.yaml").read_text(encoding="utf-8")
        )

        result_limit = (
            schema["properties"]["search"]["properties"]["prowlarr"]
            ["properties"]["result_limit"]
        )
        self.assertEqual(default["search"]["prowlarr"]["result_limit"], 12)
        self.assertEqual(result_limit["maximum"], 12)


if __name__ == "__main__":
    unittest.main()
