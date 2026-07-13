#!/usr/bin/env python3
"""Generate a deterministic digest-pinned catalog from verified Feature artifacts."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import tempfile
from pathlib import Path

import yaml

from app.core.plugin_artifact import ArtifactError, verify_tpx


_REQUIRED_PLUGINS = {
    "open115": "feature/115",
    "media-search": "feature/media-search",
    "renaming": "feature/renaming",
    "plex-management": "feature/plex-management",
}
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TAG_RE = re.compile(r"^platform-v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")


class CatalogBuildError(RuntimeError):
    pass


def _validate_release(repository: str, tag: str) -> tuple[str, str]:
    repository = str(repository or "").strip()
    tag = str(tag or "").strip()
    if not _REPOSITORY_RE.fullmatch(repository):
        raise CatalogBuildError("repository must use owner/name")
    if not _TAG_RE.fullmatch(tag):
        raise CatalogBuildError("tag must use platform-v<semver>")
    return repository, tag


def build_catalog(
    repository: str,
    tag: str,
    artifact_paths,
    *,
    previous_catalog: dict | None = None,
) -> dict:
    repository, tag = _validate_release(repository, tag)
    releases = {}
    for raw_path in artifact_paths or []:
        path = Path(raw_path)
        if path.suffix.lower() != ".tpx":
            raise CatalogBuildError(f"artifact must use .tpx: {path}")
        try:
            verified = verify_tpx(path)
        except (ArtifactError, OSError) as exc:
            raise CatalogBuildError(f"invalid Feature artifact: {path.name}") from exc
        manifest = verified.manifest
        plugin_id = manifest.plugin_id
        if plugin_id not in _REQUIRED_PLUGINS:
            raise CatalogBuildError(f"unexpected plugin: {plugin_id}")
        if manifest.source.branch != _REQUIRED_PLUGINS[plugin_id]:
            raise CatalogBuildError(
                f"unexpected source branch for {plugin_id}: {manifest.source.branch}"
            )
        if plugin_id in releases:
            raise CatalogBuildError(f"duplicate plugin version: {plugin_id}@{manifest.version}")
        asset_name = f"{plugin_id}-{manifest.version}.tpx"
        if path.name != asset_name:
            raise CatalogBuildError(
                f"artifact filename must match manifest: {path.name} != {asset_name}"
            )
        previous_versions = (
            (((previous_catalog or {}).get("plugins") or {}).get(plugin_id) or {})
            .get("versions") or {}
        )
        previous_entry = previous_versions.get(manifest.version)
        if isinstance(previous_entry, dict):
            previous_digest = str(previous_entry.get("sha256") or "").lower()
            if previous_digest and previous_digest != verified.sha256:
                raise CatalogBuildError(
                    f"version digest changed without version bump: "
                    f"{plugin_id}@{manifest.version}"
                )
        releases[plugin_id] = {
            "versions": {
                manifest.version: {
                    "url": (
                        f"https://github.com/{repository}/releases/download/"
                        f"{tag}/{asset_name}"
                    ),
                    "sha256": verified.sha256,
                    "core_api": manifest.core_api,
                    "provides": sorted(item.name for item in manifest.provides),
                    "requires": sorted(manifest.requires),
                    "source": {
                        "branch": manifest.source.branch,
                        "commit": manifest.source.commit,
                    },
                }
            }
        }

    missing = set(_REQUIRED_PLUGINS) - set(releases)
    if missing:
        raise CatalogBuildError(f"missing required plugins: {sorted(missing)}")
    return {
        "schema_version": 1,
        "release": tag,
        "plugins": {
            plugin_id: releases[plugin_id]
            for plugin_id in sorted(releases)
        },
    }


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


def write_catalog(
    repository: str,
    tag: str,
    artifact_paths,
    output: Path,
    *,
    previous_catalog: dict | None = None,
) -> Path:
    output = Path(output)
    catalog = build_catalog(
        repository,
        tag,
        artifact_paths,
        previous_catalog=previous_catalog,
    )
    payload = yaml.safe_dump(
        catalog,
        allow_unicode=True,
        sort_keys=True,
    ).encode("utf-8")
    _atomic_write(output, payload)
    digest = hashlib.sha256(payload).hexdigest()
    digest_path = output.with_name(f"{output.name}.sha256")
    _atomic_write(
        digest_path,
        f"{digest}  {output.name}\n".encode("utf-8"),
    )
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a verified Telepiplex release catalog"
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--previous-catalog", type=Path)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        previous_catalog = None
        if args.previous_catalog:
            try:
                previous_catalog = yaml.safe_load(
                    args.previous_catalog.read_text(encoding="utf-8")
                ) or {}
            except (OSError, yaml.YAMLError) as exc:
                raise CatalogBuildError("previous catalog cannot be read") from exc
            if not isinstance(previous_catalog, dict):
                raise CatalogBuildError("previous catalog must contain a mapping")
        output = write_catalog(
            args.repository,
            args.tag,
            args.artifacts,
            args.output,
            previous_catalog=previous_catalog,
        )
    except CatalogBuildError as exc:
        parser.error(str(exc))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
