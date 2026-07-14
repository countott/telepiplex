import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


class ConfigSchemaContractTest(unittest.TestCase):
    def test_schema_declares_independent_renaming_config_wizard(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["x-telepiplex-config-command"], "renaming_config")

    def test_ai_and_tvdb_are_visual_form_sections_with_write_only_keys(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        tvdb = schema["properties"]["metadata"]["properties"]["tvdb"]
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

    def test_default_config_validates_against_schema(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(default)


if __name__ == "__main__":
    unittest.main()
