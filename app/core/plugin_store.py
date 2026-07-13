from __future__ import annotations

import json
import os
import shutil
import uuid
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.core.plugin_artifact import VerifiedArtifact, verify_tpx
from app.core.plugin_manifest import PluginManifest


class StoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class StagedRelease:
    plugin_id: str
    version: str
    path: Path
    manifest: PluginManifest
    artifact_sha256: str


@dataclass(frozen=True)
class ActiveRelease:
    plugin_id: str
    version: str
    path: Path
    manifest: PluginManifest
    artifact_sha256: str
    previous_version: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class InstalledPlugin:
    plugin_id: str
    version: str
    path: Path
    manifest: PluginManifest
    active: bool


def _manifest_at(path: Path) -> PluginManifest:
    value = yaml.safe_load((path / "manifest.yaml").read_text(encoding="utf-8"))
    return PluginManifest.from_mapping(value)


def _schema_at(path: Path) -> dict:
    value = json.loads((path / "config.schema.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StoreError("invalid_config", "config schema must be an object")
    return value


def _default_at(path: Path) -> dict:
    value = yaml.safe_load((path / "config.default.yaml").read_text(encoding="utf-8"))
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise StoreError("invalid_config", "default config must be a mapping")
    return value


def _validate(schema: dict, value: dict) -> dict:
    if not isinstance(value, dict):
        raise StoreError("invalid_config", "plugin config must be a mapping")
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except (SchemaError, ValidationError) as exc:
        raise StoreError("invalid_config", exc.message) from None
    return deepcopy(value)


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_yaml(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = yaml.safe_dump(
        value,
        allow_unicode=True,
        sort_keys=True,
    ).encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            os.chmod(temporary, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


class PluginStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.staging_root = self.root / ".staging"
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def _plugin_root(self, plugin_id: str) -> Path:
        return self.root / plugin_id

    def stage(self, artifact: VerifiedArtifact) -> StagedRelease:
        verified = verify_tpx(artifact.path, artifact.sha256)
        manifest = verified.manifest
        staged_path = self.staging_root / (
            f"{manifest.plugin_id}-{manifest.version}-{uuid.uuid4().hex}"
        )
        staged_path.mkdir(mode=0o700)
        try:
            with zipfile.ZipFile(verified.path, "r") as bundle:
                for member in verified.members:
                    target = staged_path / member
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bundle.read(member))

            schema = _schema_at(staged_path)
            default = _validate(schema, _default_at(staged_path))
            config_path = self._plugin_root(manifest.plugin_id) / "config.yaml"
            if config_path.exists():
                try:
                    current = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, yaml.YAMLError) as exc:
                    raise StoreError(
                        "invalid_config",
                        f"cannot parse plugin config: {type(exc).__name__}",
                    ) from None
                _validate(schema, current)
            else:
                (staged_path / ".validated-default.json").write_text(
                    json.dumps(default, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
            return StagedRelease(
                plugin_id=manifest.plugin_id,
                version=manifest.version,
                path=staged_path,
                manifest=manifest,
                artifact_sha256=verified.sha256,
            )
        except Exception:
            shutil.rmtree(staged_path, ignore_errors=True)
            raise

    def discard(self, staged: StagedRelease):
        if staged.path.parent != self.staging_root:
            raise StoreError("invalid_staging", "release is not in this store staging area")
        shutil.rmtree(staged.path, ignore_errors=True)

    def activate(self, staged: StagedRelease) -> ActiveRelease:
        previous = self.active(staged.plugin_id)
        committed = self.commit(staged)
        return self.set_active(
            committed,
            previous_version=previous.version if previous else None,
            enabled=True,
        )

    def commit(self, staged: StagedRelease) -> ActiveRelease:
        if staged.path.parent != self.staging_root or not staged.path.is_dir():
            raise StoreError("invalid_staging", "release is not in this store staging area")
        plugin_root = self._plugin_root(staged.plugin_id)
        releases_root = plugin_root / "releases"
        target = releases_root / staged.version
        if target.exists():
            raise StoreError("release_exists", f"release already exists: {staged.version}")

        releases_root.mkdir(parents=True, exist_ok=True)
        os.replace(staged.path, target)
        plugin_root.joinpath("state").mkdir(parents=True, exist_ok=True)

        config_path = plugin_root / "config.yaml"
        if not config_path.exists():
            default = _default_at(target)
            _atomic_yaml(config_path, default)
        (target / ".validated-default.json").unlink(missing_ok=True)

        _atomic_json(target / ".artifact.json", {
            "artifact_sha256": staged.artifact_sha256,
        })
        return ActiveRelease(
            plugin_id=staged.plugin_id,
            version=staged.version,
            path=target,
            manifest=staged.manifest,
            artifact_sha256=staged.artifact_sha256,
            enabled=False,
        )

    def set_active(
        self,
        release: ActiveRelease,
        *,
        previous_version: str | None = None,
        enabled: bool = True,
    ) -> ActiveRelease:
        current = self.active(release.plugin_id)
        if previous_version is None and current and current.version != release.version:
            previous_version = current.version
        record = {
            "plugin_id": release.plugin_id,
            "active_version": release.version,
            "previous_version": previous_version,
            "enabled": bool(enabled),
            "artifact_sha256": release.artifact_sha256,
            "source": {
                "repository": release.manifest.source.repository,
                "branch": release.manifest.source.branch,
                "commit": release.manifest.source.commit,
            },
        }
        _atomic_json(self._plugin_root(release.plugin_id) / "active.json", record)
        return ActiveRelease(
            plugin_id=release.plugin_id,
            version=release.version,
            path=release.path,
            manifest=release.manifest,
            artifact_sha256=release.artifact_sha256,
            previous_version=previous_version,
            enabled=bool(enabled),
        )

    def set_enabled(self, plugin_id: str, enabled: bool) -> ActiveRelease:
        current = self.active(plugin_id)
        if current is None:
            raise StoreError("not_installed", f"active release not found: {plugin_id}")
        return self.set_active(
            current,
            previous_version=current.previous_version,
            enabled=bool(enabled),
        )

    def clear_active(self, plugin_id: str):
        self._plugin_root(str(plugin_id)).joinpath("active.json").unlink(missing_ok=True)

    def remove_plugin(self, plugin_id: str):
        plugin_root = self._plugin_root(str(plugin_id))
        if plugin_root.parent != self.root:
            raise StoreError("invalid_plugin_id", "plugin path escapes store root")
        shutil.rmtree(plugin_root, ignore_errors=True)

    def release(self, plugin_id: str, version: str) -> ActiveRelease | None:
        path = self._plugin_root(str(plugin_id)) / "releases" / str(version)
        if not path.is_dir():
            return None
        try:
            manifest = _manifest_at(path)
            artifact_record = json.loads(
                (path / ".artifact.json").read_text(encoding="utf-8")
            )
        except Exception:
            return None
        return ActiveRelease(
            plugin_id=manifest.plugin_id,
            version=manifest.version,
            path=path,
            manifest=manifest,
            artifact_sha256=str(artifact_record.get("artifact_sha256") or ""),
            enabled=False,
        )

    def active(self, plugin_id: str) -> ActiveRelease | None:
        plugin_root = self._plugin_root(str(plugin_id))
        record_path = plugin_root / "active.json"
        if not record_path.exists():
            return None
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("plugin_id") != plugin_id:
                raise ValueError("active record identity mismatch")
            version = str(record["active_version"])
            path = plugin_root / "releases" / version
            manifest = _manifest_at(path)
            if manifest.plugin_id != plugin_id or manifest.version != version:
                raise ValueError("active release manifest mismatch")
            return ActiveRelease(
                plugin_id=plugin_id,
                version=version,
                path=path,
                manifest=manifest,
                artifact_sha256=str(record.get("artifact_sha256") or ""),
                previous_version=record.get("previous_version"),
                enabled=bool(record.get("enabled", True)),
            )
        except Exception:
            quarantine = plugin_root / f"active.corrupt.{uuid.uuid4().hex}.json"
            os.replace(record_path, quarantine)
            return None

    def list_installed(self) -> list[InstalledPlugin]:
        installed = []
        if not self.root.exists():
            return installed
        for plugin_root in sorted(self.root.iterdir(), key=lambda item: item.name):
            if not plugin_root.is_dir() or plugin_root.name.startswith("."):
                continue
            current = self.active(plugin_root.name)
            releases_root = plugin_root / "releases"
            if not releases_root.is_dir():
                continue
            for release_path in sorted(releases_root.iterdir(), key=lambda item: item.name):
                if not release_path.is_dir():
                    continue
                try:
                    manifest = _manifest_at(release_path)
                except Exception:
                    continue
                installed.append(InstalledPlugin(
                    plugin_id=manifest.plugin_id,
                    version=manifest.version,
                    path=release_path,
                    manifest=manifest,
                    active=bool(current and current.version == manifest.version),
                ))
        return installed

    def validate_config(self, release: ActiveRelease | StagedRelease, value: dict) -> dict:
        return _validate(_schema_at(release.path), value)

    def config_schema(self, release: ActiveRelease | StagedRelease) -> dict:
        return deepcopy(_schema_at(release.path))

    def read_config(self, release: ActiveRelease | StagedRelease) -> dict:
        config_path = self._plugin_root(release.plugin_id) / "config.yaml"
        try:
            value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise StoreError(
                "invalid_config",
                f"cannot read plugin config: {type(exc).__name__}",
            ) from None
        return _validate(_schema_at(release.path), value)

    def write_config(
        self,
        release: ActiveRelease | StagedRelease,
        value: dict,
    ) -> dict:
        validated = _validate(_schema_at(release.path), value)
        config_path = self._plugin_root(release.plugin_id) / "config.yaml"
        try:
            _atomic_yaml(config_path, validated)
        except OSError as exc:
            raise StoreError(
                "config_write_failed",
                f"cannot write plugin config: {type(exc).__name__}",
            ) from None
        return deepcopy(validated)
