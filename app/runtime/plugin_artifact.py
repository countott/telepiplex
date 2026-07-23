from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from app.runtime.plugin_manifest import PluginManifest


MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_MEMBER_COUNT = 4096
_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_REQUIRED_SOURCE_MEMBERS = {
    "manifest.yaml",
    "plugin.whl",
    "config.schema.json",
    "config.default.yaml",
}
_REQUIRED_ARCHIVE_MEMBERS = _REQUIRED_SOURCE_MEMBERS | {"checksums.sha256"}


class ArtifactError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class VerifiedArtifact:
    path: Path
    sha256: str
    manifest: PluginManifest
    members: tuple[str, ...]


def _error(code: str, message: str):
    raise ArtifactError(code, message)


def _safe_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        _error("unsafe_member", f"unsafe archive member: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _error("unsafe_member", f"unsafe archive member: {name}")
    normalized = path.as_posix()
    if normalized != name or name.endswith("/"):
        _error("unsafe_member", f"unsafe archive member: {name}")
    return normalized


def _allowed_member(name: str, *, archive: bool) -> bool:
    exact = _REQUIRED_ARCHIVE_MEMBERS if archive else _REQUIRED_SOURCE_MEMBERS
    if name in exact:
        return True
    if name.startswith("wheelhouse/"):
        return name.endswith(".whl") and len(PurePosixPath(name).parts) == 2
    if name.startswith("migrations/"):
        return len(PurePosixPath(name).parts) >= 2
    return False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_manifest(data: bytes) -> PluginManifest:
    try:
        value = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        _error("invalid_manifest", f"cannot parse manifest.yaml: {type(exc).__name__}")
    return PluginManifest.from_mapping(value)


def _validate_static_config(schema_data: bytes, default_data: bytes):
    try:
        schema = json.loads(schema_data.decode("utf-8"))
        default = yaml.safe_load(default_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        _error("invalid_config_bundle", f"cannot parse config bundle: {type(exc).__name__}")
    if not isinstance(schema, dict):
        _error("invalid_config_bundle", "config.schema.json must contain an object")
    if default is not None and not isinstance(default, dict):
        _error("invalid_config_bundle", "config.default.yaml must contain a mapping")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _FIXED_TIMESTAMP)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def build_tpx(source_dir: Path, output: Path) -> Path:
    source = Path(source_dir).resolve()
    output = Path(output).resolve()
    if not source.is_dir():
        _error("invalid_source", f"source directory does not exist: {source}")

    members: dict[str, bytes] = {}
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            _error("unsafe_member", f"source member is not a regular file: {path}")
        name = _safe_member_name(path.relative_to(source).as_posix())
        if not _allowed_member(name, archive=False):
            _error("unexpected_member", f"unexpected source member: {name}")
        data = path.read_bytes()
        if len(data) > MAX_MEMBER_BYTES:
            _error("member_too_large", f"source member is too large: {name}")
        members[name] = data

    missing = _REQUIRED_SOURCE_MEMBERS - set(members)
    if missing:
        _error("missing_member", f"missing required members: {sorted(missing)}")
    if not any(name.startswith("wheelhouse/") for name in members):
        _error("missing_member", "wheelhouse must contain at least one wheel")
    _load_manifest(members["manifest.yaml"])
    _validate_static_config(
        members["config.schema.json"],
        members["config.default.yaml"],
    )

    checksum_lines = [
        f"{_sha256(members[name])}  {name}"
        for name in sorted(members)
    ]
    members["checksums.sha256"] = ("\n".join(checksum_lines) + "\n").encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as bundle:
            for name in sorted(members):
                bundle.writestr(_zip_info(name), members[name])
        if temporary.stat().st_size > MAX_PACKAGE_BYTES:
            _error("package_too_large", "built artifact exceeds package size limit")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _parse_checksums(data: bytes) -> dict[str, str]:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        _error("invalid_checksums", "checksums.sha256 is not UTF-8")
    checksums = {}
    for line in lines:
        if not line:
            continue
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            _error("invalid_checksums", "checksums.sha256 has an invalid line")
        name = _safe_member_name(name)
        if name == "checksums.sha256" or name in checksums:
            _error("invalid_checksums", f"invalid checksum member: {name}")
        checksums[name] = digest
    return checksums


def verify_tpx(path: Path, expected_sha256: str = "") -> VerifiedArtifact:
    artifact = Path(path).resolve()
    if not artifact.is_file():
        _error("archive_not_found", f"artifact does not exist: {artifact}")
    if artifact.stat().st_size > MAX_PACKAGE_BYTES:
        _error("package_too_large", "artifact exceeds package size limit")
    archive_digest = _sha256(artifact.read_bytes())
    if expected_sha256 and archive_digest != str(expected_sha256).strip().lower():
        _error("archive_checksum_mismatch", "artifact SHA-256 does not match")

    try:
        bundle = zipfile.ZipFile(artifact, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        _error("invalid_archive", f"cannot open artifact: {type(exc).__name__}")

    with bundle:
        infos = bundle.infolist()
        if len(infos) > MAX_MEMBER_COUNT:
            _error("too_many_members", "artifact has too many members")
        names = [info.filename for info in infos]
        if len(set(names)) != len(names):
            _error("duplicate_member", "artifact contains duplicate members")
        for info in infos:
            name = _safe_member_name(info.filename)
            if not _allowed_member(name, archive=True):
                _error("unexpected_member", f"unexpected artifact member: {name}")
            mode = info.external_attr >> 16
            if info.flag_bits & 0x1 or stat.S_ISLNK(mode) or (mode and not stat.S_ISREG(mode)):
                _error("unsafe_member", f"artifact member is not a regular file: {name}")
            if info.file_size > MAX_MEMBER_BYTES:
                _error("member_too_large", f"artifact member is too large: {name}")
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            _error(
                "package_uncompressed_too_large",
                "artifact uncompressed content exceeds size limit",
            )

        missing = _REQUIRED_ARCHIVE_MEMBERS - set(names)
        if missing:
            _error("missing_member", f"missing required members: {sorted(missing)}")
        if not any(name.startswith("wheelhouse/") for name in names):
            _error("missing_member", "wheelhouse must contain at least one wheel")

        contents = {name: bundle.read(name) for name in names}
        checksums = _parse_checksums(contents["checksums.sha256"])
        payload_names = set(names) - {"checksums.sha256"}
        if set(checksums) != payload_names:
            _error("invalid_checksums", "checksum member set does not match artifact")
        for name in payload_names:
            if _sha256(contents[name]) != checksums[name]:
                _error("member_checksum_mismatch", f"checksum mismatch: {name}")
        manifest = _load_manifest(contents["manifest.yaml"])
        _validate_static_config(
            contents["config.schema.json"],
            contents["config.default.yaml"],
        )

    return VerifiedArtifact(
        path=artifact,
        sha256=archive_digest,
        manifest=manifest,
        members=tuple(sorted(names)),
    )
