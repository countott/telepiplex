# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


DEFAULT_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_HOST_LOG_NAME = "telepiplex"
DEFAULT_HOST_LOG_FILENAME = "telepiplex.log"
DEFAULT_FEATURE_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
_TELEPIPLEX_HANDLER_MARKER = "_telepiplex_handler_kind"
_TELEPIPLEX_HANDLER_PATH = "_telepiplex_handler_path"


def host_log_path(config_root: str | Path) -> Path:
    return Path(config_root) / "logs" / DEFAULT_HOST_LOG_FILENAME


def feature_runtime_log_path(plugin_root: str | Path) -> Path:
    return Path(plugin_root) / "state" / "logs" / "runtime.log"


def _formatter() -> logging.Formatter:
    return logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT)


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


def _stream_handler(level: int) -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(_formatter())
    setattr(handler, _TELEPIPLEX_HANDLER_MARKER, "stream")
    return handler


def _rotating_file_handler(path: Path, level: int) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=DEFAULT_FEATURE_LOG_MAX_BYTES,
        backupCount=DEFAULT_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(_formatter())
    setattr(handler, _TELEPIPLEX_HANDLER_MARKER, "rotating_file")
    setattr(handler, _TELEPIPLEX_HANDLER_PATH, str(path))
    return handler


def configure_root_logger(
    *,
    level=logging.INFO,
    log_path: str | Path | None = None,
    logger_name: str = DEFAULT_HOST_LOG_NAME,
) -> logging.Logger:
    normalized_level = _normalize_level(level)
    root = logging.getLogger()
    root.setLevel(normalized_level)
    _remove_marked_handlers(root, kinds={"stream", "rotating_file"})
    root.addHandler(_stream_handler(normalized_level))
    if log_path is not None:
        root.addHandler(_rotating_file_handler(Path(log_path), normalized_level))
    logger = logging.getLogger(str(logger_name or DEFAULT_HOST_LOG_NAME))
    logger.setLevel(normalized_level)
    return logger


def configure_named_file_logger(
    name: str,
    *,
    log_path: str | Path,
    level=logging.INFO,
    propagate: bool = True,
) -> logging.Logger:
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
            handler.setFormatter(_formatter())
            keep.append(handler)
            continue
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    if not any(
        getattr(handler, _TELEPIPLEX_HANDLER_MARKER, "") == "rotating_file"
        and getattr(handler, _TELEPIPLEX_HANDLER_PATH, "") == target
        for handler in keep
    ):
        logger.addHandler(_rotating_file_handler(Path(target), normalized_level))
    return logger


class Logger:
    def __init__(
        self,
        level=logging.INFO,
        debug_model=False,
        *,
        log_path: str | Path | None = None,
        logger_name: str = DEFAULT_HOST_LOG_NAME,
    ):
        if log_path is None and not debug_model:
            from app.init import CONFIG

            log_path = host_log_path(CONFIG)
        self.logger = configure_root_logger(
            level=level,
            log_path=log_path,
            logger_name=logger_name,
        )

    def debug(self, message):
        self.logger.debug(message)

    def info(self, message):
        self.logger.info(message)

    def warn(self, message):
        self.logger.warning(message)

    def warning(self, message):
        self.logger.warning(message)

    def error(self, message):
        self.logger.error(message)

    def cri(self, message):
        self.logger.critical(message)
