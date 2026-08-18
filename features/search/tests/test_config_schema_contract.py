import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


class ConfigSchemaContractTest(unittest.TestCase):
    def test_search_release_version_is_1_11_1_with_config_schema_v2(self):
        manifest = yaml.safe_load(
            (ROOT / "manifest.yaml").read_text(encoding="utf-8")
        )
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "1.11.4")
        self.assertEqual(manifest["config_schema_version"], 2)
        self.assertIn('version = "1.11.4"', pyproject)

    def test_config_schema_v2_declares_removal_of_legacy_ai_section(self):
        migration = json.loads(
            (ROOT / "migrations/config-1-to-2.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            migration,
            {
                "format": "telepiplex.config-migration.v1",
                "from_version": 1,
                "to_version": 2,
                "operations": [{"op": "remove", "path": ["ai"]}],
            },
        )

    def test_manifest_routes_wikidata_direct_links_to_search(self):
        manifest = yaml.safe_load(
            (ROOT / "manifest.yaml").read_text(encoding="utf-8")
        )

        self.assertIn("wikidata.org", manifest["direct_message_hosts"])

    def test_schema_declares_independent_search_config_wizard(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(
            schema["x-telepiplex-config-command"],
            "search_config",
        )

    def test_tvdb_is_visual_form_section_with_write_only_keys(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))

        metadata = schema["properties"]["metadata"]
        tvdb = metadata["properties"]["tvdb"]
        self.assertEqual(tvdb["title"], "TVDB")
        self.assertEqual(
            set(tvdb["properties"]),
            {"enable", "api_key", "base_url", "subscriber_pin", "timeout"},
        )
        self.assertTrue(tvdb["properties"]["api_key"]["writeOnly"])
        self.assertTrue(tvdb["properties"]["subscriber_pin"]["writeOnly"])
        self.assertNotIn("ai", schema["properties"])

    def test_tmdb_and_anilist_are_independent_metadata_sources(self):
        schema = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        default = yaml.safe_load((ROOT / "config.default.yaml").read_text(encoding="utf-8"))
        metadata = schema["properties"]["metadata"]["properties"]

        self.assertEqual(
            set(metadata["tmdb"]["properties"]),
            {"enable", "api_key", "base_url", "timeout"},
        )
        self.assertTrue(metadata["tmdb"]["properties"]["api_key"]["writeOnly"])
        self.assertEqual(
            default["metadata"]["tmdb"],
            {
                "enable": True,
                "api_key": "",
                "base_url": "https://api.themoviedb.org/3",
                "timeout": 15,
            },
        )
        self.assertEqual(
            default["metadata"]["anilist"],
            {
                "enable": True,
                "endpoint": "https://graphql.anilist.co",
                "timeout": 15,
            },
        )
        self.assertNotIn("api_key", metadata["anilist"]["properties"])

    def test_active_metadata_defaults_are_bounded(self):
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
            default["metadata"]["wikipedia"],
            {
                "enable": True,
                "languages": ["zh", "en"],
                "timeout": 10,
                "min_interval": 3,
                "max_queries": 2,
                "rate_limit_cooldown": 30,
            },
        )
        self.assertEqual(
            set(
                schema["properties"]["metadata"]["properties"][
                    "wikipedia"
                ]["properties"]
            ),
            {
                "enable",
                "languages",
                "timeout",
                "min_interval",
                "max_queries",
                "rate_limit_cooldown",
            },
        )
        self.assertNotIn("ai", default)
        self.assertNotIn("ai", schema["required"])

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

    def test_prowlarr_search_timeout_defaults_to_two_hundred_seconds(self):
        default = yaml.safe_load(
            (ROOT / "config.default.yaml").read_text(encoding="utf-8")
        )

        self.assertEqual(default["search"]["prowlarr"]["timeout"], 200)
        self.assertEqual(
            default["search"]["prowlarr"]["indexer_timeout"],
            75,
        )


if __name__ == "__main__":
    unittest.main()
