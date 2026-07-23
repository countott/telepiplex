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
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


_REFERENCE_RE = re.compile(r"^(?P<plugin>[a-z][a-z0-9-]{0,63})@(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PLUGIN_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


class CatalogError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)


@dataclass(frozen=True)
class ResolvedArtifact:
    path: Path
    expected_sha256: str


@dataclass(frozen=True)
class CatalogRelease:
    plugin_id: str
    version: str
    host_api: str
    url: str
    sha256: str
    source_branch: str
    source_commit: str
    provides: tuple[str, ...]
    requires: tuple[str, ...]


@dataclass(frozen=True)
class CatalogUpdate:
    plugin_id: str
    current_version: str
    target_version: str
    reference: str
    source_commit: str
    sha256: str


@dataclass(frozen=True)
class CatalogInstallCandidate:
    plugin_id: str
    target_version: str
    reference: str
    source_commit: str
    sha256: str
    provides: tuple[str, ...]
    requires: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    dependency_plugins: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing_capabilities


class PluginCatalog:
    def __init__(
        self,
        catalog_path: str | Path,
        cache_root: Path,
        *,
        opener=None,
        timeout: float = 30,
        max_download_bytes: int = 256 * 1024 * 1024,
        max_catalog_bytes: int = 2 * 1024 * 1024,
    ):
        self.cache_root = Path(cache_root)
        raw_source = str(catalog_path).strip()
        parsed_source = urlparse(raw_source)
        self.catalog_url = ""
        if parsed_source.scheme:
            if (
                parsed_source.scheme != "https"
                or not parsed_source.netloc
                or parsed_source.username
                or parsed_source.password
            ):
                raise CatalogError(
                    "invalid_catalog_source",
                    "Remote Feature catalog must use HTTPS",
                )
            self.catalog_url = raw_source
            self.catalog_path = self.cache_root / "catalog.yaml"
        else:
            self.catalog_path = Path(raw_source)
        self._opener = opener or urllib.request.urlopen
        self.timeout = float(timeout)
        self.max_download_bytes = int(max_download_bytes)
        self.max_catalog_bytes = int(max_catalog_bytes)

    async def refresh(self) -> Path:
        if not self.catalog_url:
            await asyncio.to_thread(self._catalog_data)
            return self.catalog_path
        await asyncio.to_thread(self._refresh_remote)
        return self.catalog_path

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
        if self.catalog_url:
            try:
                await self.refresh()
            except CatalogError:
                # A previously validated catalog is sufficient for an explicit,
                # digest-pinned update when the remote endpoint is temporarily
                # unavailable. Discovery still requires a successful refresh.
                await asyncio.to_thread(self._catalog_data)
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
        data = self._catalog_data()
        try:
            entry = data["plugins"][plugin_id]["versions"][version]
        except (KeyError, TypeError):
            raise CatalogError("release_not_found", f"Catalog release not found: {plugin_id}@{version}") from None
        if not isinstance(entry, dict):
            raise CatalogError("invalid_catalog", "Catalog release must be a mapping")
        return dict(entry)

    async def available_updates(
        self,
        installed_versions: dict[str, str],
        host_api_version: str,
    ) -> list[CatalogUpdate]:
        data = await asyncio.to_thread(self._catalog_data)
        updates = []
        for plugin_id, current_text in sorted((installed_versions or {}).items()):
            try:
                current = Version(str(current_text))
            except InvalidVersion:
                continue
            releases = self._releases(data, plugin_id)
            compatible = []
            for release in releases:
                try:
                    if Version(str(host_api_version)) not in SpecifierSet(release.host_api):
                        continue
                    candidate = Version(release.version)
                except (InvalidSpecifier, InvalidVersion):
                    continue
                if candidate > current and not candidate.is_prerelease:
                    compatible.append((candidate, release))
            if not compatible:
                continue
            _, release = max(compatible, key=lambda item: item[0])
            updates.append(CatalogUpdate(
                plugin_id=plugin_id,
                current_version=str(current_text),
                target_version=release.version,
                reference=f"{plugin_id}@{release.version}",
                source_commit=release.source_commit,
                sha256=release.sha256,
            ))
        return updates

    async def available_plugins(
        self,
        installed_plugin_ids: set[str],
        host_api_version: str,
        *,
        available_capabilities: set[str] | tuple[str, ...] = (),
    ) -> list[CatalogInstallCandidate]:
        data = await asyncio.to_thread(self._catalog_data)
        installed = {str(item) for item in (installed_plugin_ids or set())}
        selected: dict[str, CatalogRelease] = {}
        plugin_ids = sorted(
            key
            for key in (data.get("plugins") or {})
            if isinstance(key, str) and _PLUGIN_RE.fullmatch(key)
        )
        for plugin_id in plugin_ids:
            if plugin_id in installed:
                continue
            compatible = []
            for release in self._releases(data, plugin_id):
                try:
                    if Version(str(host_api_version)) not in SpecifierSet(release.host_api):
                        continue
                    version = Version(release.version)
                except (InvalidSpecifier, InvalidVersion):
                    continue
                if not version.is_prerelease:
                    compatible.append((version, release))
            if compatible:
                _, selected[plugin_id] = max(compatible, key=lambda item: item[0])

        providers: dict[str, set[str]] = {}
        for plugin_id, release in selected.items():
            for capability in release.provides:
                providers.setdefault(capability, set()).add(plugin_id)

        available = {str(item) for item in (available_capabilities or set())}
        candidates = []
        for plugin_id, release in selected.items():
            effective_available = available | set(release.provides)
            missing = tuple(sorted(
                capability
                for capability in release.requires
                if capability not in effective_available
            ))
            dependencies = tuple(sorted({
                provider
                for capability in missing
                for provider in providers.get(capability, set())
                if provider != plugin_id
            }))
            candidates.append(CatalogInstallCandidate(
                plugin_id=plugin_id,
                target_version=release.version,
                reference=f"{plugin_id}@{release.version}",
                source_commit=release.source_commit,
                sha256=release.sha256,
                provides=release.provides,
                requires=release.requires,
                missing_capabilities=missing,
                dependency_plugins=dependencies,
            ))
        return sorted(candidates, key=lambda item: (not item.ready, item.plugin_id))

    def _catalog_data(self) -> dict:
        if not self.catalog_path.is_file():
            raise CatalogError("catalog_unavailable", "Feature catalog is not configured")
        try:
            payload = self.catalog_path.read_bytes()
        except OSError as exc:
            raise CatalogError("invalid_catalog", "Feature catalog cannot be read") from exc
        return self._parse_catalog(payload)

    @staticmethod
    def _parse_catalog(payload: bytes) -> dict:
        try:
            data = yaml.safe_load(payload.decode("utf-8")) or {}
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise CatalogError("invalid_catalog", "Feature catalog cannot be read") from exc
        if not isinstance(data, dict) or not isinstance(data.get("plugins"), dict):
            raise CatalogError("invalid_catalog", "Feature catalog must contain plugins")
        return data

    def _refresh_remote(self) -> None:
        temporary_path = None
        try:
            with self._opener(
                urllib.request.Request(self.catalog_url),
                timeout=self.timeout,
            ) as response:
                final_url = (
                    response.geturl()
                    if hasattr(response, "geturl")
                    else self.catalog_url
                )
                if urlparse(str(final_url)).scheme != "https":
                    raise CatalogError(
                        "insecure_redirect",
                        "Feature catalog was redirected outside HTTPS",
                    )
                chunks = []
                total = 0
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_catalog_bytes:
                        raise CatalogError(
                            "catalog_too_large",
                            "Feature catalog exceeds download limit",
                        )
                    chunks.append(chunk)
            payload = b"".join(chunks)
            self._parse_catalog(payload)
            self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".catalog-",
                suffix=".tmp",
                dir=self.catalog_path.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.catalog_path)
        except CatalogError:
            raise
        except Exception as exc:
            raise CatalogError(
                "catalog_download_failed",
                "Feature catalog download failed",
            ) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _releases(data: dict, plugin_id: str) -> list[CatalogRelease]:
        if not _PLUGIN_RE.fullmatch(str(plugin_id)):
            return []
        versions = (((data.get("plugins") or {}).get(plugin_id) or {}).get("versions") or {})
        if not isinstance(versions, dict):
            return []
        releases = []
        for version, entry in versions.items():
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "")
            digest = str(entry.get("sha256") or "").lower()
            host_api = str(entry.get("host_api") or "")
            source = entry.get("source") or {}
            branch = str(source.get("branch") or "") if isinstance(source, dict) else ""
            commit = str(source.get("commit") or "").lower() if isinstance(source, dict) else ""
            raw_provides = entry.get("provides", [])
            raw_requires = entry.get("requires", [])
            if not isinstance(raw_provides, list) or not isinstance(raw_requires, list):
                continue
            provides = tuple(str(item) for item in raw_provides)
            requires = tuple(str(item) for item in raw_requires)
            try:
                parsed_version = Version(str(version))
                SpecifierSet(host_api)
            except (InvalidVersion, InvalidSpecifier):
                continue
            parsed_url = urlparse(url)
            if (
                str(parsed_version) != str(version)
                or parsed_version.is_prerelease
                or parsed_url.scheme != "https"
                or not parsed_url.netloc
                or _SHA256_RE.fullmatch(digest) is None
                or not branch
                or _COMMIT_RE.fullmatch(commit) is None
                or any(_CAPABILITY_RE.fullmatch(item) is None for item in provides)
                or any(_CAPABILITY_RE.fullmatch(item) is None for item in requires)
                or len(set(provides)) != len(provides)
                or len(set(requires)) != len(requires)
            ):
                continue
            releases.append(CatalogRelease(
                plugin_id=str(plugin_id),
                version=str(version),
                host_api=host_api,
                url=url,
                sha256=digest,
                source_branch=branch,
                source_commit=commit,
                provides=provides,
                requires=requires,
            ))
        return releases

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
