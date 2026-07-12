from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml


_REFERENCE_RE = re.compile(r"^(?P<plugin>[a-z][a-z0-9-]{0,63})@(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CatalogError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)


@dataclass(frozen=True)
class ResolvedArtifact:
    path: Path
    expected_sha256: str


class PluginCatalog:
    def __init__(
        self,
        catalog_path: Path,
        cache_root: Path,
        *,
        opener=None,
        timeout: float = 30,
        max_download_bytes: int = 256 * 1024 * 1024,
    ):
        self.catalog_path = Path(catalog_path)
        self.cache_root = Path(cache_root)
        self._opener = opener or urllib.request.urlopen
        self.timeout = float(timeout)
        self.max_download_bytes = int(max_download_bytes)

    async def resolve(self, reference: str | Path) -> ResolvedArtifact:
        raw = str(reference).strip()
        direct = Path(raw).expanduser()
        if direct.is_file():
            if direct.suffix.lower() != ".tpx":
                raise CatalogError("invalid_artifact", "Feature artifact must use the .tpx extension")
            return ResolvedArtifact(direct.resolve(), "")

        match = _REFERENCE_RE.fullmatch(raw)
        if match is None:
            raise CatalogError(
                "invalid_reference",
                "Feature reference must be an existing .tpx path or name@version",
            )
        plugin_id = match.group("plugin")
        version = match.group("version")
        entry = await asyncio.to_thread(self._entry, plugin_id, version)
        expected_sha256 = str(entry.get("sha256") or "").lower()
        if _SHA256_RE.fullmatch(expected_sha256) is None:
            raise CatalogError("invalid_catalog", "Catalog release must pin a valid sha256 digest")

        path_value = entry.get("path")
        url_value = entry.get("url")
        if bool(path_value) == bool(url_value):
            raise CatalogError("invalid_catalog", "Catalog release must define exactly one path or url")
        if path_value:
            path = Path(str(path_value)).expanduser()
            if not path.is_absolute():
                path = self.catalog_path.parent / path
            path = path.resolve()
            if not path.is_file() or path.suffix.lower() != ".tpx":
                raise CatalogError("artifact_not_found", "Catalog artifact does not exist")
            return ResolvedArtifact(path, expected_sha256)

        url = str(url_value)
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise CatalogError("invalid_catalog", "Catalog artifact URL must use HTTPS")
        target = self.cache_root / plugin_id / version / f"{expected_sha256}.tpx"
        if target.is_file() and await asyncio.to_thread(self._matches, target, expected_sha256):
            return ResolvedArtifact(target, expected_sha256)
        await asyncio.to_thread(self._download, url, target, expected_sha256)
        return ResolvedArtifact(target, expected_sha256)

    def _entry(self, plugin_id: str, version: str) -> dict:
        if not self.catalog_path.is_file():
            raise CatalogError("catalog_unavailable", "Feature catalog is not configured")
        try:
            data = yaml.safe_load(self.catalog_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise CatalogError("invalid_catalog", "Feature catalog cannot be read") from exc
        try:
            entry = data["plugins"][plugin_id]["versions"][version]
        except (KeyError, TypeError):
            raise CatalogError("release_not_found", f"Catalog release not found: {plugin_id}@{version}") from None
        if not isinstance(entry, dict):
            raise CatalogError("invalid_catalog", "Catalog release must be a mapping")
        return dict(entry)

    def _download(self, url: str, target: Path, expected_sha256: str):
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".download-",
            suffix=".tpx",
            dir=target.parent,
        )
        temporary_path = Path(temporary_name)
        total = 0
        try:
            with os.fdopen(file_descriptor, "wb") as output:
                with self._opener(urllib.request.Request(url), timeout=self.timeout) as response:
                    final_url = response.geturl() if hasattr(response, "geturl") else url
                    if urlparse(str(final_url)).scheme != "https":
                        raise CatalogError(
                            "insecure_redirect",
                            "Feature artifact download was redirected outside HTTPS",
                        )
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.max_download_bytes:
                            raise CatalogError("artifact_too_large", "Feature artifact exceeds download limit")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            if digest.hexdigest() != expected_sha256:
                raise CatalogError("digest_mismatch", "Feature artifact sha256 mismatch")
            os.replace(temporary_path, target)
        except CatalogError:
            raise
        except Exception as exc:
            raise CatalogError("download_failed", "Feature artifact download failed") from exc
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _matches(path: Path, expected_sha256: str) -> bool:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256
