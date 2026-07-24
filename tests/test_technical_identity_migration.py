from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import yaml

from telepiplex_plugin_sdk import FeatureRuntime


ROOT = Path(__file__).resolve().parents[1]
FEATURES = {
    "search": ("telepiplex_search", "1.0.1"),
    "download": ("telepiplex_download", "1.0.0"),
    "rename": ("telepiplex_rename", "1.0.0"),
    "sync": ("telepiplex_sync", "1.0.0"),
    "caption": ("telepiplex_caption", "0.1.0"),
}
LEGACY_FEATURE_DIRS = (
    "media-search",
    "open115",
    "renaming",
    "plex-management",
)


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_features_use_only_the_new_technical_identities():
    for legacy in LEGACY_FEATURE_DIRS:
        assert not (ROOT / "features" / legacy).exists()

    for plugin_id, (package, version) in FEATURES.items():
        feature_dir = ROOT / "features" / plugin_id
        assert feature_dir.is_dir()

        manifest = _yaml(feature_dir / "manifest.yaml")
        assert manifest["plugin_id"] == plugin_id
        assert manifest["name"] == plugin_id
        assert manifest["version"] == version
        assert manifest["entry_point"] == f"{package}.runtime:main"
        assert manifest["source"]["branch"] == "main"
        assert "host_api" in manifest
        assert "core_api" not in manifest

        project = tomllib.loads(
            (feature_dir / "pyproject.toml").read_text(encoding="utf-8")
        )
        assert project["project"]["name"] == f"telepiplex-{plugin_id}"
        assert project["project"]["version"] == version
        assert (feature_dir / "src" / package).is_dir()


def test_feature_public_classes_and_test_files_use_new_identities():
    public_symbols = {
        "search": ("SearchFeature", "MediaSearchFeature"),
        "download": ("DownloadFeature", "Open115Feature"),
        "rename": ("RenameFeature", "RenamingFeature"),
        "sync": ("SyncFeature", "PlexFeature"),
    }
    for plugin_id, (current, legacy) in public_symbols.items():
        init_file = (
            ROOT
            / "features"
            / plugin_id
            / "src"
            / f"telepiplex_{plugin_id}"
            / "__init__.py"
        ).read_text(encoding="utf-8")
        assert current in init_file
        assert legacy not in init_file

    assert (ROOT / "features" / "search" / "tests" / "test_search_utils.py").is_file()
    assert not (
        ROOT / "features" / "search" / "tests" / "test_media_search_utils.py"
    ).exists()
    assert (ROOT / "features" / "sync" / "tests" / "test_sync_service.py").is_file()
    assert not (
        ROOT / "features" / "sync" / "tests" / "test_plex_management.py"
    ).exists()


def test_sync_exposes_a_backend_neutral_library_capability():
    manifest = _yaml(ROOT / "features" / "sync" / "manifest.yaml")
    assert manifest["provides"] == [{"name": "library.sync", "exclusive": True}]
    assert manifest["callbacks"] == ["sync"]
    assert {command["name"] for command in manifest["commands"]} == {
        "sync",
        "scan",
        "sync_config",
    }


def test_caption_is_an_installable_inert_placeholder():
    feature_dir = ROOT / "features" / "caption"
    manifest = _yaml(feature_dir / "manifest.yaml")
    for field in ("provides", "requires", "subscribes", "publishes", "commands", "callbacks"):
        assert manifest[field] == []

    sys.path.insert(0, str(feature_dir / "src"))
    try:
        module = importlib.import_module("telepiplex_caption.runtime")
        runtime = module.main(SimpleNamespace(manifest=manifest, token="test-token"))
    finally:
        sys.path.pop(0)
        sys.modules.pop("telepiplex_caption.runtime", None)
        sys.modules.pop("telepiplex_caption", None)

    assert isinstance(runtime, FeatureRuntime)
    assert runtime.capabilities == {}
    assert runtime.events == {}
    assert runtime.commands == {}
    assert runtime.callbacks == {}
    assert runtime.messages is None


def test_feature_release_catalog_accepts_only_new_ids():
    from tools.update_feature_catalog import FEATURE_SOURCE_DIRS, parse_feature_tag

    assert FEATURE_SOURCE_DIRS == {
        plugin_id: f"features/{plugin_id}"
        for plugin_id in FEATURES
    }
    for plugin_id, (_, version) in FEATURES.items():
        assert parse_feature_tag(f"{plugin_id}-v{version}") == (plugin_id, version)


def test_root_release_identity_is_telepiplex():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    compose = _yaml(ROOT / "docker-compose.yaml")

    assert "telepiplex-v*" in workflow
    assert "core-v" not in workflow
    assert "ghcr.io/${{ github.repository_owner }}/telepiplex" in workflow
    assert "telepiplex-core" not in workflow
    assert list(compose["services"]) == ["telepiplex"]
    assert compose["services"]["telepiplex"]["container_name"] == "telepiplex"
    assert compose["services"]["telepiplex"]["image"] == "telepiplex:latest"


def test_runtime_protocol_uses_host_not_legacy_name():
    assert (ROOT / "app" / "runtime").is_dir()
    assert not (ROOT / "app" / "core").exists()
    assert (
        ROOT / "sdk" / "src" / "telepiplex_plugin_sdk" / "host_client.py"
    ).is_file()
    assert not (
        ROOT / "sdk" / "src" / "telepiplex_plugin_sdk" / "core_client.py"
    ).exists()

    sdk_init = (
        ROOT / "sdk" / "src" / "telepiplex_plugin_sdk" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "HostClient" in sdk_init
    assert "CoreClient" not in sdk_init


def test_persistent_runtime_log_uses_telepiplex_identity():
    from app.utils.logger import host_log_path

    assert host_log_path("/config") == Path("/config/logs/telepiplex.log")
