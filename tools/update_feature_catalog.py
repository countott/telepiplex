#!/usr/bin/env python3
"""Merge one immutable Feature release into an existing catalog snapshot."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import tempfile
from pathlib import Path

import yaml

from app.runtime.plugin_artifact import ArtifactError, verify_tpx
from app.runtime.plugin_contract import ContractError


FEATURE_SOURCE_DIRS = {
    "download": "features/download",
    "search": "features/search",
    "rename": "features/rename",
    "sync": "features/sync",
    "caption": "features/caption",
}
FEATURE_SOURCE_BRANCH = "main"
_SEMVER_PATTERN = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
FEATURE_TAG_RE = re.compile(
    rf"^(?P<plugin>{'|'.join(map(re.escape, FEATURE_SOURCE_DIRS))})"
    rf"-v(?P<version>{_SEMVER_PATTERN})$"
)
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class CatalogUpdateError(RuntimeError):
    pass


def parse_feature_tag(tag) -> tuple[str, str]:
    match = FEATURE_TAG_RE.fullmatch(str(tag))
    if not match:
        raise CatalogUpdateError("invalid Feature release tag")
    return match.group("plugin"), match.group("version")


def _release_entry(verified, repository: str, tag: str) -> dict:
    manifest = verified.manifest
    asset_name = f"{manifest.plugin_id}-{manifest.version}.tpx"
    url = f"https://github.com/{repository}/releases/download/{tag}/{asset_name}"
    if not url.startswith("https://"):
        raise CatalogUpdateError("Feature release URL must use HTTPS")
    return {
        "url": url,
        "sha256": verified.sha256,
        "host_api": manifest.host_api,
        "provides": sorted(item.name for item in manifest.provides),
        "requires": sorted(manifest.requires),
        "source": {
            "branch": manifest.source.branch,
            "commit": manifest.source.commit,
        },
    }


def _validate_previous_identity(previous_entry, current_entry, identity: str) -> None:
    if not isinstance(previous_entry, dict):
        raise CatalogUpdateError(f"invalid previous catalog entry: {identity}")
    previous_source = previous_entry.get("source")
    current_source = current_entry["source"]
    if (
        str(previous_entry.get("sha256") or "").strip().lower()
        != current_entry["sha256"]
    ):
        raise CatalogUpdateError(
            f"version digest changed without version bump: {identity}"
        )
    if not isinstance(previous_source, dict) or (
        str(previous_source.get("branch") or "") != current_source["branch"]
    ):
        raise CatalogUpdateError(
            f"version source branch changed without version bump: {identity}"
        )
    if str(previous_source.get("commit") or "") != current_source["commit"]:
        raise CatalogUpdateError(
            f"version source commit changed without version bump: {identity}"
        )


def merge_feature_release(
    previous_catalog,
    artifact_path,
    repository,
    tag,
) -> dict:
    plugin_id, version = parse_feature_tag(tag)
    repository = str(repository or "").strip()
    if not _REPOSITORY_RE.fullmatch(repository):
        raise CatalogUpdateError("repository must use owner/name")

    artifact_path = Path(artifact_path)
    asset_name = f"{plugin_id}-{version}.tpx"
    if artifact_path.name != asset_name:
        raise CatalogUpdateError(
            f"artifact filename must match release identity: {asset_name}"
        )
    try:
        verified = verify_tpx(artifact_path)
    except (ArtifactError, ContractError, OSError) as exc:
        raise CatalogUpdateError(f"invalid Feature artifact: {artifact_path.name}") from exc

    manifest = verified.manifest
    if manifest.plugin_id != plugin_id:
        raise CatalogUpdateError("Feature tag plugin does not match manifest")
    if manifest.version != version:
        raise CatalogUpdateError("Feature tag version does not match manifest")
    if manifest.source.branch != FEATURE_SOURCE_BRANCH:
        raise CatalogUpdateError(
            f"unexpected source branch for {plugin_id}: {manifest.source.branch}"
        )

    if previous_catalog is None:
        merged = {"schema_version": 1, "plugins": {}}
    elif isinstance(previous_catalog, dict):
        merged = copy.deepcopy(previous_catalog)
    else:
        raise CatalogUpdateError("previous catalog must contain a mapping")
    if merged.get("schema_version", 1) != 1:
        raise CatalogUpdateError("unsupported catalog schema version")
    merged["schema_version"] = 1

    plugins = merged.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise CatalogUpdateError("previous catalog plugins must contain a mapping")
    for unsupported_plugin_id in set(plugins) - set(FEATURE_SOURCE_DIRS):
        plugins.pop(unsupported_plugin_id, None)
    plugin = plugins.setdefault(plugin_id, {"versions": {}})
    if not isinstance(plugin, dict):
        raise CatalogUpdateError(f"invalid previous plugin entry: {plugin_id}")
    versions = plugin.setdefault("versions", {})
    if not isinstance(versions, dict):
        raise CatalogUpdateError(f"invalid previous versions entry: {plugin_id}")

    entry = _release_entry(verified, repository, str(tag))
    if version in versions:
        _validate_previous_identity(
            versions[version],
            entry,
            f"{plugin_id}@{version}",
        )
    versions[version] = entry
    merged["release"] = str(tag)
    return merged


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_feature_catalog(
    previous_catalog,
    artifact_path,
    repository,
    tag,
    output,
) -> Path:
    output = Path(output)
    catalog = merge_feature_release(
        previous_catalog,
        artifact_path,
        repository,
        tag,
    )
    payload = yaml.safe_dump(
        catalog,
        allow_unicode=True,
        sort_keys=True,
    ).encode("utf-8")
    _atomic_write(output, payload)
    digest = hashlib.sha256(payload).hexdigest()
    _atomic_write(
        output.with_name(f"{output.name}.sha256"),
        f"{digest}  {output.name}\n".encode("utf-8"),
    )
    return output
