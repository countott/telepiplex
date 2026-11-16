"""Build or verify Telepiplex's bundled contracts and factory configuration."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "sdk/src")]
from telepiplex_plugin_sdk.storage_paths import normalize_115_directory
from app.runtime.config_aliases import normalize_explicit_aliases
from app.runtime.config_projection import role_value
from app.runtime.config_defaults import (
    EXAMPLE_CREDENTIALS, GITHUB_PROVIDERS, host_defaults, host_schema, shared_defaults,
)


def feature_contract(plugin):
    source = ROOT / "features" / plugin
    manifest = yaml.safe_load((source / "manifest.yaml").read_bytes())
    return {
        "version": manifest["version"], "config_schema_version": manifest["config_schema_version"],
        "defaults": yaml.safe_load((source / "config.default.yaml").read_bytes()),
        "schema": json.loads((source / "config.schema.json").read_bytes()),
    }


def public_features(contracts, shared):
    features = {key: normalize_explicit_aliases(key, contract["defaults"]) for key, contract in contracts.items()}
    for key in ("request_interval", "poll_interval"):
        features["download"].pop(key, None)
    search, rename, caption = (features[key] for key in ("search", "rename", "caption"))
    for key in ("search", "rename", "sync"):
        del features[key]["category_folder"]
    del search["metadata"]["tmdb"]["api_key"], features["sync"]["tmdb"]["api_key"]
    # These defaults live independently in older package contracts. Catch
    # input drift before removing either path from the public document.
    for plugin, key, role in (("rename", "unorganized_path", "unorganized_inventory"),
                              ("caption", "output_path", "standalone_subtitles")):
        binding = shared["directory_roles"][role]
        path = "/" if binding is None else shared["directories"][binding["directory_id"]]["path"]
        if features[plugin].pop(key) != path:
            raise ValueError(f"Reconcile {plugin}.{key} with shared.directory_roles.{role}")
    menu = role_value(shared, "manual_downloads", [])
    if features["download"].pop("save_directories") != menu:
        raise ValueError("Reconcile download.save_directories with shared.directory_roles.manual_downloads")
    rename["caption_stage_timeout_seconds"] = rename.pop("caption_timeout")
    caption["automatic"] = rename.pop("caption_enabled") and caption["automatic"]
    wrapped = search.pop("search")
    del wrapped["enable"]
    for key in ("prowlarr", "scoring"):
        search[key] = wrapped.pop(key)
    if wrapped:
        raise ValueError("Unmapped private Search fields")
    providers = caption["providers"]
    common = {key: providers[GITHUB_PROVIDERS[0]][key] for key in ("github_token", "cache_ttl_seconds")}
    caption["provider_defaults"] = {"github": common}
    for provider in GITHUB_PROVIDERS:
        for key, default in common.items():
            if providers[provider][key] == default:
                del providers[provider][key]
    return features


def outputs():
    contracts = {key: feature_contract(key) for key in ("download", "search", "rename", "sync", "caption")}
    host = host_defaults()
    shared = shared_defaults()
    subtitle = shared["directory_roles"]["standalone_subtitles"]["directory_id"]
    for key, entity in shared["directories"].items():
        entity["path"] = normalize_115_directory(entity["path"], allow_relative=False, allow_root=key == subtitle)
    descriptor = {"host": {"defaults": host, "schema": host_schema()}, "features": contracts}
    document = {"config_version": 3, **EXAMPLE_CREDENTIALS, **host,
                "shared": shared, "features": public_features(contracts, shared)}
    template = ("# Telepiplex 主配置；运行时唯一编辑入口为 /config/config.yaml。\n"
                "# 升级时保留原主配置，预检将原设置迁入新版布局。\n"
                "# /config 确认保存与 115 授权刷新均写入本文件，未安装插件也可预先配置。\n"
                "# shared.tmdb.api_key 填写 API Read Access Token，Search 与 Sync 共用。\n"
                + yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
    return {"app/config.yaml.example": template, "config/config.yaml.example": template,
            "app/runtime/config_settings.generated.json": json.dumps(descriptor, ensure_ascii=False, indent=2) + "\n"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    check = parser.parse_args().check
    stale = []
    for name, content in outputs().items():
        path = ROOT / name
        data = content.encode("utf-8")
        if check:
            if not path.is_file() or path.read_bytes() != data:
                stale.append(name)
        else:
            path.write_bytes(data)
    for name in stale:
        print(name)
    return int(bool(stale))


if __name__ == "__main__":
    sys.exit(main())
