import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


class ConfigSchemaContractTest(unittest.TestCase):
    def test_schema_declares_independent_plex_config_wizard(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["x-telepiplex-config-command"], "plex_config")

    def test_ai_is_visual_form_section_with_write_only_key(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        ai = schema["properties"]["ai"]
        self.assertEqual(ai["title"], "AI")
        self.assertTrue(ai["properties"]["api_key"]["writeOnly"])
        self.assertTrue(schema["properties"]["plex"]["properties"]["token"]["writeOnly"])

    def test_mcp_path_is_part_of_public_config_contract(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))

        mcp = schema["properties"]["mcp"]
        self.assertIn("path", mcp["properties"])
        self.assertIn("path", mcp["required"])
        self.assertEqual(default["mcp"]["path"], "/mcp")

    def test_default_config_validates_against_schema(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(default)


if __name__ == "__main__":
    unittest.main()
