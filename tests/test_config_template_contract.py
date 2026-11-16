import unittest
from pathlib import Path
import json
import tempfile

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_TERMS = (
    "115_app_id",
    "access_token",
    "refresh_token",
    "download",
    "search",
    "prowlarr",
    "media",
    "metadata",
    "tvdb",
    "plex",
    "ai",
    "category_folder",
    "modules",
)


class ConfigTemplateContractTest(unittest.TestCase):
    def test_runtime_templates_are_identical_central_configuration_contracts(self):
        app_source = (ROOT / "app/config.yaml.example").read_text(encoding="utf-8")
        runtime_source = (ROOT / "config/config.yaml.example").read_text(encoding="utf-8")

        self.assertEqual(app_source, runtime_source)
        parsed = yaml.safe_load(runtime_source)
        self.assertEqual(
            set(parsed),
            {"config_version", "log_level", "bot_token", "allowed_user", "plugins", "shared", "features"},
        )
        self.assertEqual(parsed["plugins"]["root"], "/config/plugins")
        self.assertEqual(
            parsed["plugins"]["catalog"],
            "https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml",
        )
        self.assertEqual(parsed["plugins"]["catalog_refresh_interval"], 21600)
        self.assertEqual(parsed["config_version"], 2)
        self.assertEqual(set(parsed["features"]), {"download", "search", "rename", "sync", "caption"})
        self.assertEqual(len(parsed["shared"]["category_folder"]), 4)
        self.assertNotIn("api_key", parsed["features"]["search"]["metadata"]["tmdb"])
        self.assertNotIn("api_key", parsed["features"]["sync"]["tmdb"])
        for term in BUSINESS_TERMS:
            self.assertNotIn(term, parsed)

    def test_complete_main_template_projects_into_every_feature_schema(self):
        from app.runtime.central_config import CentralConfigStore

        parsed = yaml.safe_load((ROOT / "config/config.yaml.example").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            backend = CentralConfigStore(Path(temporary) / "config.yaml", initial=parsed)
            for plugin_id in ("download", "search", "rename", "sync", "caption"):
                with self.subTest(plugin_id=plugin_id):
                    source = ROOT / "features" / plugin_id
                    default = yaml.safe_load((source / "config.default.yaml").read_text(encoding="utf-8"))
                    schema = json.loads((source / "config.schema.json").read_text(encoding="utf-8"))
                    Draft202012Validator(schema).validate(backend.effective(plugin_id, default))

    def test_legacy_host_module_snippet_is_removed(self):
        self.assertFalse((ROOT / "config/modules/host.yaml.example").exists())


if __name__ == "__main__":
    unittest.main()
