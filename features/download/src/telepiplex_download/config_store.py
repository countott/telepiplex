from __future__ import annotations

import os
import tempfile
import threading
import asyncio
import copy
from pathlib import Path

import yaml

from .cleanup import normalize_minimum_video_size_mib
from .directories import normalize_save_directories


class HostConfigStore:
    """Host-authoritative config; synchronous mutations run on worker threads.

    Event-loop readers use the latest snapshot, so rendering a menu never waits
    synchronously for RPC. Worker reads re-fetch current Host values.
    """

    central_authoritative = True

    def __init__(self, host, initial: dict):
        self.host = host
        self._config = copy.deepcopy(initial)
        self._lock = threading.RLock()

    def _on_event_loop(self):
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _request(self, method, *args):
        if self._on_event_loop():
            raise RuntimeError("Host config mutations require a worker thread")
        return asyncio.run(method(*args))

    def read(self):
        # Do not take a worker-held lock on the RPC event loop.
        if self._on_event_loop():
            return copy.deepcopy(self._config)
        with self._lock:
            self._config = self._request(self.host.read_config)
            return copy.deepcopy(self._config)

    def snapshot(self):
        snapshot = {"exists": True, "config": self.read(), "owned": None}
        return snapshot

    def _patch(self, patch, *, snapshot=None):
        with self._lock:
            current = self.read()
            expected = {key: current.get(key) for key in patch}
            updated = self._request(self.host.patch_config, patch, expected)
            self._config = copy.deepcopy(updated)
            if snapshot is not None:
                snapshot["config"].update(expected)
                snapshot["owned"] = copy.deepcopy(patch)
            return copy.deepcopy(updated)

    def write_tokens(self, access_token, refresh_token, *, auth_mode, _snapshot=None):
        if auth_mode not in {"direct", "scan"}:
            raise ValueError("download auth_mode must be direct or scan")
        access_token, refresh_token = str(access_token or "").strip(), str(refresh_token or "").strip()
        if not access_token or not refresh_token:
            raise ValueError("download access_token and refresh_token are required")
        return self._patch({"access_token": access_token, "refresh_token": refresh_token, "auth_mode": auth_mode}, snapshot=_snapshot)

    def write_authorization_tokens(self, snapshot, access_token, refresh_token, *, auth_mode):
        return self.write_tokens(access_token, refresh_token, auth_mode=auth_mode, _snapshot=snapshot)

    def persist_refreshed_tokens(self, access_token, refresh_token, expected):
        """Compare against the tokens that actually authorized the HTTP refresh."""
        patch = {"access_token": str(access_token), "refresh_token": str(refresh_token)}
        with self._lock:
            try:
                self._config = self._request(self.host.patch_config, patch, expected)
            except Exception as exc:
                if getattr(exc, "code", "") != "config_conflict":
                    raise
                return self.read()
            return copy.deepcopy(self._config)

    def write_save_directories(self, directories):
        return self._patch({"save_directories": normalize_save_directories(directories)})

    def write_minimum_video_size_mib(self, value):
        return self._patch({"minimum_video_size_mib": normalize_minimum_video_size_mib(value)})

    def restore(self, snapshot):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("config"), dict):
            raise ValueError("invalid download config snapshot")
        with self._lock:
            current = self.read()
            owned = snapshot.get("owned")
            if not owned or any(current.get(key) != value for key, value in owned.items()):
                return current
            patch = {key: snapshot["config"].get(key) for key in owned}
            try:
                self._config = self._request(self.host.patch_config, patch, owned)
            except Exception as exc:
                if getattr(exc, "code", "") != "config_conflict":
                    raise
                return self.read()
            return copy.deepcopy(self._config)


class FeatureConfigStore:
    """Atomic writer for the download private configuration file."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> dict:
        with self._lock:
            return self._read_unlocked()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "exists": self.path.exists(),
                "config": self._read_unlocked(),
            }

    def restore(self, snapshot: dict) -> dict:
        if not isinstance(snapshot, dict) or not isinstance(
            snapshot.get("config"), dict
        ):
            raise ValueError("invalid download config snapshot")
        with self._lock:
            config = dict(snapshot["config"])
            if snapshot.get("exists"):
                self._write_unlocked(config)
            else:
                self.path.unlink(missing_ok=True)
            return config

    def _read_unlocked(self) -> dict:
        if not self.path.exists():
            return {}
        value = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(value, dict):
            raise ValueError("download config must be a mapping")
        return value

    def write_tokens(
        self,
        access_token: str,
        refresh_token: str,
        *,
        auth_mode: str,
    ) -> dict:
        if auth_mode not in {"direct", "scan"}:
            raise ValueError("download auth_mode must be direct or scan")
        access_token = str(access_token or "").strip()
        refresh_token = str(refresh_token or "").strip()
        if not access_token or not refresh_token:
            raise ValueError("download access_token and refresh_token are required")
        with self._lock:
            config = self._read_unlocked()
            config.update({
                "auth_mode": auth_mode,
                "access_token": access_token,
                "refresh_token": refresh_token,
            })
            self._write_unlocked(config)
            return dict(config)

    def write_save_directories(self, directories) -> dict:
        normalized = normalize_save_directories(directories)
        with self._lock:
            config = self._read_unlocked()
            config["save_directories"] = normalized
            self._write_unlocked(config)
            return dict(config)

    def write_minimum_video_size_mib(self, value) -> dict:
        normalized = normalize_minimum_video_size_mib(value)
        with self._lock:
            config = self._read_unlocked()
            config["minimum_video_size_mib"] = normalized
            self._write_unlocked(config)
            return dict(config)

    def _write_unlocked(self, config: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            dir=self.path.parent,
            text=True,
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(
                    config,
                    handle,
                    allow_unicode=True,
                    sort_keys=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
