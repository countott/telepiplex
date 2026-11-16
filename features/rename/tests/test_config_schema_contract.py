import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


class ConfigSchemaContractTest(unittest.TestCase):
    def test_inventory_category_routes_define_four_canonical_targets(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))

        self.assertIn("category_folder", schema["properties"])
        self.assertEqual(
            [item["kind"] for item in default["category_folder"]],
            [
                "live_action_movie",
                "animated_movie",
                "live_action_series",
                "animated_series",
            ],
        )

    def test_schema_declares_independent_rename_config_wizard(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["x-telepiplex-config-command"], "rename_config")

    def test_ai_is_a_visual_form_section_with_a_write_only_key(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        ai = schema["properties"]["ai"]
        self.assertEqual(ai["title"], "AI")
        self.assertEqual(
            set(ai["properties"]),
            {"enable", "api_url", "api_key", "model", "timeout"},
        )
        self.assertTrue(ai["properties"]["api_key"]["writeOnly"])

    def test_active_rename_configuration_does_not_require_metadata_credentials(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        config = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))
        config.pop("metadata", None)
        config["ai"].update({
            "api_url": "https://ai.example/v1",
            "api_key": "operator-ai-key",
            "model": "operator-model",
        })

        Draft202012Validator(schema).validate(config)

    def test_default_config_validates_against_schema(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(default)
        self.assertEqual(default["storage_move_batch_size"], 100)
        batch = schema["properties"]["storage_move_batch_size"]
        self.assertEqual(batch["minimum"], 1)
        self.assertEqual(batch["maximum"], 100)


if __name__ == "__main__":
    unittest.main()
