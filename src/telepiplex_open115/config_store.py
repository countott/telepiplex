from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

import yaml

from .directories import normalize_save_directories


class FeatureConfigStore:
    """Atomic writer for the open115 private configuration file."""

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
            raise ValueError("invalid open115 config snapshot")
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
            raise ValueError("open115 config must be a mapping")
        return value

    def write_tokens(
        self,
        access_token: str,
        refresh_token: str,
        *,
        auth_mode: str,
    ) -> dict:
        if auth_mode not in {"direct", "scan"}:
            raise ValueError("open115 auth_mode must be direct or scan")
        access_token = str(access_token or "").strip()
        refresh_token = str(refresh_token or "").strip()
        if not access_token or not refresh_token:
            raise ValueError("open115 access_token and refresh_token are required")
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
