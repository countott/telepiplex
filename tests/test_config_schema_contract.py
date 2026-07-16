import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, ValidationError


ROOT = Path(__file__).resolve().parents[1]


class ConfigSchemaContractTest(unittest.TestCase):
    def test_schema_declares_independent_plex_config_wizard(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["x-telepiplex-config-command"], "plex_config")

    def test_configuration_sections_exclude_ai(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))

        expected = {"category_folder", "plex", "tmdb", "fanart", "mcp"}
        self.assertEqual(set(default), expected)
        self.assertEqual(set(schema["properties"]), expected)
        self.assertEqual(set(schema["required"]), expected)
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

    def test_1_1_ai_config_has_documented_manual_migration(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        legacy = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))
        legacy["ai"] = {
            "enabled": False,
            "api_url": "",
            "api_key": "",
            "model": "",
            "timeout": 30,
            "max_tool_rounds": 3,
        }
        validator = Draft202012Validator(schema)

        with self.assertRaises(ValidationError):
            validator.validate(legacy)
        legacy.pop("ai")
        validator.validate(legacy)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("从 1.1.x 升级", readme)
        self.assertIn("删除整个 `ai:` 配置段", readme)
        self.assertIn("config_migration_required", readme)


if __name__ == "__main__":
    unittest.main()
