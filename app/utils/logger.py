# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping

from telepiplex_plugin_sdk.diagnostics import (
    build_diagnostic_event,
    infer_legacy_diagnostics,
    render_human_event,
    render_machine_event,
    sanitize_diagnostic_value,
)


DEFAULT_HOST_LOG_NAME = "telepiplex"
DEFAULT_SESSION_KEEP_COUNT = 30
DEFAULT_SESSION_KEEP_DAYS = 30
DEFAULT_FEATURE_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
_TELEPIPLEX_HANDLER_MARKER = "_telepiplex_handler_kind"
_TELEPIPLEX_HANDLER_PATH = "_telepiplex_handler_path"
_CURRENT_LOG_SESSION: "LogSession | None" = None
_SESSION_DIRECTORY_RE = re.compile(
    r"^\d{8}T\d{6}[+-]\d{4}-.+$"
)


@dataclass
class LogSession:
    session_id: str
    directory: Path
    human_path: Path
    machine_path: Path
    created_at: datetime
    runtime: dict[str, object] = field(default_factory=dict)
    _producer_sequence: int = 0
    _ingest_sequence: int = 0
    _sequence_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def next_sequences(self) -> tuple[int, int]:
        with self._sequence_lock:
            self._producer_sequence += 1
            self._ingest_sequence += 1
            return self._producer_sequence, self._ingest_sequence

    def feature_paths(self, plugin_id: str) -> tuple[Path, Path]:
        safe_plugin_id = "".join(
            character
            for character in str(plugin_id or "unknown").lower()
            if character.isalnum() or character in {"-", "_"}
        ) or "unknown"
        return (
            self.directory / f"feature-{safe_plugin_id}.human.log",
            self.directory / f"feature-{safe_plugin_id}.machine.jsonl",
        )


def log_sessions_root(config_root: str | Path) -> Path:
    """Return the directory whose direct children are Host startup logs."""
    return Path(config_root) / "logs"


def host_log_path(
    config_root: str | Path,
    session: LogSession | None = None,
) -> Path:
    active = session or _CURRENT_LOG_SESSION
    if active is not None:
        return active.human_path
    return log_sessions_root(config_root)


def feature_runtime_log_path(plugin_root: str | Path) -> Path:
    """Legacy location retained for callers that have not received a Host session."""
    return Path(plugin_root) / "state" / "logs" / "runtime.log"


def current_log_session() -> LogSession | None:
    return _CURRENT_LOG_SESSION


def _session_directory_name(now: datetime, session_id: str) -> str:
    local = now.astimezone()
    timestamp = local.strftime("%Y%m%dT%H%M%S%z")
    return f"{timestamp}-{session_id}"


def prune_log_sessions(
    sessions_root: str | Path,
    *,
    now: datetime | None = None,
    keep_sessions: int = DEFAULT_SESSION_KEEP_COUNT,
    keep_days: int = DEFAULT_SESSION_KEEP_DAYS,
) -> list[Path]:
    root = Path(sessions_root)
    if not root.is_dir():
        return []
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current.timestamp() - timedelta(days=max(0, int(keep_days))).total_seconds()
    directories = sorted(
        (
            path
            for path in root.iterdir()
            if (path.is_dir() or path.is_symlink())
            and _SESSION_DIRECTORY_RE.fullmatch(path.name) is not None
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    root_resolved = root.resolve()
    for index, directory in enumerate(directories):
        too_many = index >= max(0, int(keep_sessions))
        too_old = directory.stat().st_mtime < cutoff
        if not (too_many or too_old):
            continue
        target = directory.resolve(strict=False)
        if target.parent != root_resolved:
            continue
        if directory.is_symlink():
            directory.unlink(missing_ok=True)
        else:
            shutil.rmtree(directory)
        removed.append(directory)
    return removed


def create_log_session(
    config_root: str | Path,
    *,
    now: datetime | None = None,
    session_id: str | None = None,
    keep_sessions: int = DEFAULT_SESSION_KEEP_COUNT,
    keep_days: int = DEFAULT_SESSION_KEEP_DAYS,
    runtime: Mapping[str, object] | None = None,
) -> LogSession:
    global _CURRENT_LOG_SESSION
    created_at = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    identity = str(session_id or uuid.uuid4().hex[:12].upper())
    root = log_sessions_root(config_root)
    root.mkdir(parents=True, exist_ok=True)
    directory = root / _session_directory_name(created_at, identity)
    attempt = 0
    while directory.exists():
        attempt += 1
        directory = root / f"{_session_directory_name(created_at, identity)}-{attempt}"
    directory.mkdir(mode=0o750)
    session = LogSession(
        session_id=identity,
        directory=directory,
        human_path=directory / "telepiplex.human.log",
        machine_path=directory / "telepiplex.machine.jsonl",
        created_at=created_at,
        runtime=dict(runtime or {}),
    )
    session.human_path.touch()
    session.machine_path.touch()
    timestamp = created_at.timestamp()
    os.utime(directory, (timestamp, timestamp))
    prune_log_sessions(
        root,
        now=created_at,
        keep_sessions=keep_sessions,
        keep_days=keep_days,
    )
    _CURRENT_LOG_SESSION = session
    return session


def _normalize_level(level) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        return getattr(logging, level.upper(), logging.INFO)
    return logging.INFO


def _remove_marked_handlers(logger: logging.Logger, *, kinds: set[str]):
    for handler in list(logger.handlers):
        if getattr(handler, _TELEPIPLEX_HANDLER_MARKER, "") in kinds:
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


def _async_task_name() -> str | None:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    return task.get_name() if task is not None else None


def _component_for_record(record: logging.LogRecord) -> str:
    explicit = getattr(record, "diagnostic_component", None)
    if explicit:
        return str(explicit)
    name = str(record.name or "telepiplex")
    if name.startswith("telepiplex.feature."):
        return name.split(".", 2)[-1]
    if name.startswith("telepiplex."):
        return name.split(".", 1)[-1]
    return name


class DualDiagnosticHandler(logging.Handler):
    def __init__(
        self,
        session: LogSession,
        *,
        human_path: Path | None = None,
        machine_path: Path | None = None,
        stream=None,
    ):
        super().__init__()
        self.session = session
        self.human_path = Path(human_path or session.human_path)
        self.machine_path = Path(machine_path or session.machine_path)
        self.human_path.parent.mkdir(parents=True, exist_ok=True)
        self.machine_path.parent.mkdir(parents=True, exist_ok=True)
        self._human = self.human_path.open("a", encoding="utf-8")
        self._machine = self.machine_path.open("a", encoding="utf-8")
        self.stream = stream
        setattr(self, _TELEPIPLEX_HANDLER_MARKER, "dual")
        setattr(self, _TELEPIPLEX_HANDLER_PATH, str(self.human_path))

    def emit(self, record: logging.LogRecord):
        try:
            producer, ingest = self.session.next_sequences()
            incoming = getattr(record, "diagnostic_event", None)
            if isinstance(incoming, dict):
                event = json.loads(json.dumps(incoming, ensure_ascii=False, default=str))
                event["sequence"]["ingest"] = ingest
                event["identity"]["session_id"] = self.session.session_id
                existing_paths = list(
                    (event.get("privacy") or {}).get("redacted_paths") or []
                )
                event_without_privacy = dict(event)
                event_without_privacy.pop("privacy", None)
                event, newly_redacted = sanitize_diagnostic_value(event_without_privacy)
                redacted_paths = sorted(set(existing_paths + newly_redacted))
                event["privacy"] = {
                    "redacted_paths": redacted_paths,
                    "redaction_count": len(redacted_paths),
                    "sanitized": True,
                }
            else:
                runtime = dict(self.session.runtime)
                runtime.update(dict(getattr(record, "diagnostic_runtime", {}) or {}))
                runtime.setdefault("async_task", _async_task_name())
                message = record.getMessage()
                explicit_event_name = getattr(record, "event_name", None)
                explicit_fields = getattr(record, "diagnostic_fields", None)
                if explicit_event_name is None and explicit_fields is None:
                    event_name, diagnostic_fields = infer_legacy_diagnostics(message)
                else:
                    event_name = str(explicit_event_name or "log.message")
                    diagnostic_fields = explicit_fields
                event = build_diagnostic_event(
                    level=record.levelname,
                    event_name=event_name,
                    message=message,
                    session_id=self.session.session_id,
                    logger_name=record.name,
                    component=_component_for_record(record),
                    fields=diagnostic_fields,
                    runtime=runtime,
                    error=(
                        record.exc_info[1]
                        if record.exc_info and isinstance(record.exc_info[1], BaseException)
                        else None
                    ),
                    sequence=producer,
                    ingest_sequence=ingest,
                )
                setattr(record, "diagnostic_event", event)
            machine = render_machine_event(event) + "\n"
            rendered_human = render_human_event(event)
            human = rendered_human + "\n" if rendered_human else ""
            self._machine.write(machine)
            if human:
                self._human.write(human)
            self._machine.flush()
            self._human.flush()
            if self.stream is not None and human:
                self.stream.write(human)
                self.stream.flush()
        except Exception as exc:
            try:
                sys.__stderr__.write(
                    f"telepiplex diagnostics write failed: {type(exc).__name__}\n"
                )
                sys.__stderr__.flush()
            except Exception:
                pass

    def close(self):
        for stream in (getattr(self, "_human", None), getattr(self, "_machine", None)):
            if stream is not None:
                try:
                    stream.flush()
                    stream.close()
                except Exception:
                    pass
        super().close()


def configure_root_logger(
    *,
    level=logging.INFO,
    session: LogSession | None = None,
    log_path: str | Path | None = None,
    logger_name: str = DEFAULT_HOST_LOG_NAME,
) -> logging.Logger:
    normalized_level = _normalize_level(level)
    active = session or _CURRENT_LOG_SESSION
    if active is None:
        if log_path is None:
            raise ValueError("a log session or config-root log path is required")
        legacy = Path(log_path)
        config_root = legacy.parent.parent if legacy.parent.name == "logs" else legacy.parent
        active = create_log_session(config_root)
    root = logging.getLogger()
    root.setLevel(normalized_level)
    _remove_marked_handlers(root, kinds={"stream", "rotating_file", "dual"})
    handler = DualDiagnosticHandler(active, stream=sys.stdout)
    handler.setLevel(normalized_level)
    root.addHandler(handler)
    logger = logging.getLogger(str(logger_name or DEFAULT_HOST_LOG_NAME))
    logger.setLevel(normalized_level)
    return logger


def _legacy_formatter() -> logging.Formatter:
    return logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )


def configure_named_file_logger(
    name: str,
    *,
    log_path: str | Path,
    level=logging.INFO,
    propagate: bool = True,
) -> logging.Logger:
    """Compatibility adapter; Feature session fan-out replaces this in the supervisor."""
    normalized_level = _normalize_level(level)
    logger = logging.getLogger(str(name))
    logger.setLevel(normalized_level)
    logger.propagate = bool(propagate)
    target = str(Path(log_path))
    keep = []
    for handler in list(logger.handlers):
        if getattr(handler, _TELEPIPLEX_HANDLER_MARKER, "") != "rotating_file":
            keep.append(handler)
            continue
        if getattr(handler, _TELEPIPLEX_HANDLER_PATH, "") == target:
            handler.setLevel(normalized_level)
            keep.append(handler)
            continue
        logger.removeHandler(handler)
        handler.close()
    if not any(
        getattr(handler, _TELEPIPLEX_HANDLER_MARKER, "") == "rotating_file"
        and getattr(handler, _TELEPIPLEX_HANDLER_PATH, "") == target
        for handler in keep
    ):
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=DEFAULT_FEATURE_LOG_MAX_BYTES,
            backupCount=DEFAULT_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(normalized_level)
        handler.setFormatter(_legacy_formatter())
        setattr(handler, _TELEPIPLEX_HANDLER_MARKER, "rotating_file")
        setattr(handler, _TELEPIPLEX_HANDLER_PATH, target)
        logger.addHandler(handler)
    return logger


def configure_feature_session_logger(
    name: str,
    *,
    plugin_id: str,
    session: LogSession,
    level=logging.INFO,
    propagate: bool = True,
) -> logging.Logger:
    normalized_level = _normalize_level(level)
    logger = logging.getLogger(str(name))
    logger.setLevel(normalized_level)
    logger.propagate = bool(propagate)
    human_path, machine_path = session.feature_paths(plugin_id)
    target = str(human_path)
    matching = None
    for handler in list(logger.handlers):
        kind = getattr(handler, _TELEPIPLEX_HANDLER_MARKER, "")
        if kind == "dual" and getattr(handler, _TELEPIPLEX_HANDLER_PATH, "") == target:
            matching = handler
            handler.setLevel(normalized_level)
            continue
        if kind in {"dual", "rotating_file"}:
            logger.removeHandler(handler)
            handler.close()
    if matching is None:
        handler = DualDiagnosticHandler(
            session,
            human_path=human_path,
            machine_path=machine_path,
        )
        handler.setLevel(normalized_level)
        logger.addHandler(handler)
    return logger


class Logger:
    def __init__(
        self,
        level=logging.INFO,
        debug_model=False,
        *,
        config_root: str | Path | None = None,
        log_path: str | Path | None = None,
        logger_name: str = DEFAULT_HOST_LOG_NAME,
        session_id: str | None = None,
        now: datetime | None = None,
        host_version: str | None = None,
    ):
        if config_root is None:
            if log_path is not None:
                legacy = Path(log_path)
                config_root = (
                    legacy.parent.parent
                    if legacy.parent.name == "logs"
                    else legacy.parent
                )
            elif not debug_model:
                from app.init import CONFIG

                config_root = CONFIG
            else:
                config_root = "config"
        self.session = create_log_session(
            config_root,
            now=now,
            session_id=session_id,
            runtime={"host_version": host_version},
        )
        self.logger = configure_root_logger(
            level=level,
            session=self.session,
            logger_name=logger_name,
        )

    def debug(self, message, **kwargs):
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message, **kwargs):
        self._log(logging.INFO, message, **kwargs)

    def warn(self, message, **kwargs):
        self._log(logging.WARNING, message, **kwargs)

    def warning(self, message, **kwargs):
        self._log(logging.WARNING, message, **kwargs)

    def error(self, message, **kwargs):
        self._log(logging.ERROR, message, **kwargs)

    def exception(self, message, **kwargs):
        kwargs["exc_info"] = kwargs.get("exc_info", True)
        self._log(logging.ERROR, message, **kwargs)

    def cri(self, message, **kwargs):
        self._log(logging.CRITICAL, message, **kwargs)

    def _log(self, level: int, message, **kwargs):
        event_name = kwargs.pop("event_name", None)
        fields = kwargs.pop("diagnostic_fields", None)
        extra = dict(kwargs.pop("extra", {}) or {})
        if event_name is not None:
            extra["event_name"] = str(event_name)
        if fields is not None:
            extra["diagnostic_fields"] = dict(fields)
        self.logger.log(level, message, extra=extra, **kwargs)
