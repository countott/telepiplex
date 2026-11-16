import unittest
from pathlib import Path
import json
import tempfile
import shutil
import subprocess
import sys
import pytest

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
        self.assertEqual(parsed["config_version"], 3)
        self.assertEqual(parsed["plugins"]["runtime_root"], "/tmp/telepiplex")
        self.assertEqual(parsed["plugins"]["event_retry_interval"], 1)
        self.assertEqual(set(parsed["features"]), {"download", "search", "rename", "sync", "caption"})
        self.assertEqual(len(parsed["shared"]["directory_roles"]["media_categories"]), 4)
        self.assertEqual(parsed["shared"]["directory_roles"]["manual_downloads"], [])
        self.assertNotIn("search", parsed["features"]["search"])
        for plugin in ("search", "rename", "sync"):
            self.assertNotIn("category_folder", parsed["features"][plugin])
        caption = parsed["features"]["caption"]
        self.assertEqual(caption["provider_defaults"]["github"], {"github_token": "", "cache_ttl_seconds": 3600})
        for provider in ("nekomoe", "mingy", "kitauji", "haruhana"):
            self.assertNotIn("github_token", caption["providers"][provider])
            self.assertNotIn("cache_ttl_seconds", caption["providers"][provider])
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
                    from app.runtime.config_projection import ProjectionContract
                    actual = backend.effective(plugin_id, default, contract=ProjectionContract(schema))
                    Draft202012Validator(schema).validate(actual)
                    if plugin_id == "sync":
                        # Sync's standalone categories are empty; the Host supplies shared ones.
                        default["category_folder"] = yaml.safe_load((ROOT / "features/search/config.default.yaml").read_text())["category_folder"]
                    self.assertEqual(actual, default)
                    # Project without package defaults: a missing public leaf
                    # cannot quietly reappear during the snapshot merge.
                    private = {"search": {"enable": default["search"]["enable"]}} if plugin_id == "search" else {}
                    self.assertEqual(backend.effective(plugin_id, private, contract=ProjectionContract(schema)), default)

    def test_legacy_host_module_snippet_is_removed(self):
        self.assertFalse((ROOT / "config/modules/host.yaml.example").exists())


if __name__ == "__main__":
    unittest.main()


def test_bundled_runtime_contracts_and_valid_cold_zero(tmp_path, monkeypatch):
    from tests.test_bot_runtime_startup import load_bot_module
    from app.runtime.config_settings import contract_for, setting_rows
    from app.runtime.config_semantics_migration import load_builtin_contracts

    cfg = {"config_version": 3, "plugins": {"root": str(tmp_path/"plugins"), "runtime_root": str(tmp_path/"run"),
                                             "restart_limit": 0, "stabilize_seconds": 0}}
    manager = load_bot_module().build_plugin_manager(cfg, tmp_path/"db", config_path=tmp_path/"config.yaml")
    assert (manager.supervisor.restart_limit, manager.stabilize_seconds) == (0, 0)
    assert manager.broker.dispatcher.delivery_deadline == 1800
    assert manager.store.active("search") is None
    assert len(setting_rows(manager, "runtime")) == 2
    rowset = load_builtin_contracts()
    assert len(rowset) == 5
    descriptor = json.loads((ROOT/"app/runtime/config_settings.generated.json").read_bytes())
    for key, contract in rowset.items():
        src = ROOT/"features"/key
        assert contract_for(manager, key) == contract == (yaml.safe_load((src/"config.default.yaml").read_bytes()),
                                                         json.loads((src/"config.schema.json").read_bytes()))
        manifest = yaml.safe_load((src/"manifest.yaml").read_bytes())
        for field in ("version", "config_schema_version"):
            assert descriptor["features"][key][field] == manifest[field]
    from app.runtime import config_settings
    from app.runtime.central_config import CentralConfigError
    monkeypatch.setattr(config_settings, "__file__", str(tmp_path/"image/runtime/config_settings.py"))
    assert load_builtin_contracts() == {}
    with pytest.raises(CentralConfigError, match="metadata unavailable"):
        contract_for(manager, "search")


def test_generator_check_detects_drift_without_writes(tmp_path):
    script = ROOT/"scripts/generate_config_templates.py"
    assert script.is_file()
    paths = ["scripts/generate_config_templates.py", "app/runtime/config_defaults.py",
             "app/runtime/config_settings.generated.json", "app/config.yaml.example", "config/config.yaml.example",
             "app/runtime/config_aliases.py", "app/runtime/config_projection.py", "app/runtime/central_config.py",
             "app/__init__.py", "app/runtime/__init__.py"]
    for key in ("download", "search", "rename", "sync", "caption"):
        paths.extend(f"features/{key}/{name}" for name in ("manifest.yaml", "config.default.yaml", "config.schema.json"))
    for path in paths:
        destination = tmp_path/path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/path, destination)
    shutil.copytree(ROOT/"sdk/src/telepiplex_plugin_sdk", tmp_path/"sdk/src/telepiplex_plugin_sdk",
                    ignore=shutil.ignore_patterns("__pycache__"))
    command = [sys.executable, str(tmp_path/paths[0]), "--check"]
    subprocess.run(command, cwd=tmp_path.parent, check=True)
    template = tmp_path/"app/config.yaml.example"
    template.unlink()
    schema = tmp_path/"features/search/config.schema.json"
    content = json.loads(schema.read_bytes())
    content["properties"]["search"]["properties"]["prowlarr"]["properties"]["result_limit"]["maximum"] = 99
    schema.write_text(json.dumps(content))
    defaults = tmp_path/"features/search/config.default.yaml"
    value = yaml.safe_load(defaults.read_bytes())
    value["search"]["prowlarr"]["result_limit"] = 33
    defaults.write_text(yaml.safe_dump(value))
    result = subprocess.run(command, cwd=tmp_path.parent, capture_output=True, text=True)
    assert result.returncode == 1
    for path in paths[2:5]:
        assert path in result.stdout
    assert not template.exists()
    assert (tmp_path/paths[2]).read_bytes() == (ROOT/paths[2]).read_bytes()
    # Shared Host defaults are the factory authority. A changed private
    # release fallback must be reconciled before regenerating that authority.
    private = tmp_path/"features/rename/config.default.yaml"
    value = yaml.safe_load(private.read_bytes())
    value["unorganized_path"] = "/changed"
    private.write_text(yaml.safe_dump(value))
    failed = subprocess.run(command[:-1], cwd=tmp_path.parent, capture_output=True, text=True)
    assert failed.returncode != 0 and "rename.unorganized_path" in failed.stderr
    assert not template.exists()
    value["unorganized_path"] = "/未整理"
    value["ai"]["base_url"] = "https://example.invalid"
    del value["ai"]["api_url"]
    private.write_text(yaml.safe_dump(value))
    subprocess.run(command[:-1], cwd=tmp_path.parent, check=True)
    canonical = yaml.safe_load(template.read_bytes())["features"]["rename"]["ai"]
    assert canonical["api_url"] == "https://example.invalid" and "base_url" not in canonical


def test_factory_checks_ordered_download_menu_against_shared_bindings():
    from app.runtime.config_defaults import shared_defaults
    from scripts.generate_config_templates import feature_contract, public_features

    contracts = {key: feature_contract(key) for key in ("download", "search", "rename", "sync", "caption")}
    shared = shared_defaults()
    menu = [{"name": "Archive", "path": "private/archive"}, {"name": "Queue", "path": "private/queue"}]
    contracts["download"]["defaults"]["save_directories"] = menu
    conflict = "download.save_directories with shared.directory_roles.manual_downloads"
    with pytest.raises(ValueError, match=conflict):
        public_features(contracts, shared)

    shared["directories"].update({"archive": {"name": "Archive directory", "path": "/private/archive"},
                                  "queue": {"name": "Queue", "path": "/private/queue"}})
    bindings = [{"directory_id": "archive", "label": "Archive"}, {"directory_id": "queue"}]
    shared["directory_roles"]["manual_downloads"] = bindings
    assert "save_directories" not in public_features(contracts, shared)["download"]
    assert contracts["download"]["defaults"]["save_directories"] == menu
    bindings.reverse()
    with pytest.raises(ValueError, match=conflict):
        public_features(contracts, shared)
    bindings.reverse()
    bindings[0]["label"] = "Changed name"
    with pytest.raises(ValueError, match=conflict):
        public_features(contracts, shared)


def test_shared_default_copies_and_frozen_legacy_baseline(monkeypatch):
    from app.runtime import config_projection
    from app.runtime import config_defaults
    first = config_defaults.shared_defaults()
    first["directories"]["live_action_movie"]["path"] = "/edited"
    assert config_defaults.shared_defaults()["directories"]["live_action_movie"]["path"] == "/真人电影"
    monkeypatch.setattr(config_defaults, "MEDIA_CATEGORIES", (("live_action_movie", "新默认"), *config_defaults.MEDIA_CATEGORIES[1:]))
    assert config_projection.category_view({"config_version": 3})[0]["path"] == "/新默认"
    assert config_projection.category_view({"config_version": 2})[0]["path"] == "/真人电影"


def test_descriptor_leaf_follows_local_pointer_and_preserves_title():
    from app.runtime.config_settings import leaf_schema
    from app.runtime.central_config import CentralConfigError
    schema = {"properties": {"sources": {"type": "array", "items": {"additionalProperties": {
        "$ref": "#/$defs/provider~1name~0", "title": "来源凭据"}}}},
              "$defs": {"provider/name~": {"$ref": "#/$defs/token"}, "token": {"type": "string", "maxLength": 20}}}
    assert leaf_schema(schema, ("sources", 0, "catalog")) == {"title": "来源凭据", "type": "string", "maxLength": 20}
    schema["$defs"]["token"] = {"$ref": "#/$defs/missing"}
    with pytest.raises(CentralConfigError):
        leaf_schema(schema, ("sources", 0, "catalog"))
    schema["$defs"]["token"] = {"$ref": "#/$defs/token"}
    with pytest.raises(CentralConfigError):
        leaf_schema(schema, ("sources", 0, "catalog"))
