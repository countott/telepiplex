import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


class ConfigSchemaContractTest(unittest.TestCase):
    def test_schema_declares_independent_media_search_config_wizard(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(
            schema["x-telepiplex-config-command"],
            "media_search_config",
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
            {"enable", "api_url", "api_key", "model", "timeout"},
        )
        self.assertTrue(tvdb["properties"]["api_key"]["writeOnly"])
        self.assertTrue(tvdb["properties"]["subscriber_pin"]["writeOnly"])
        self.assertTrue(ai["properties"]["api_key"]["writeOnly"])

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


if __name__ == "__main__":
    unittest.main()
