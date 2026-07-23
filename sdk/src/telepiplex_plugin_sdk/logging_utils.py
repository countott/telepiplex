from __future__ import annotations

import logging
import os
import sys

from .log_sanitizer import sanitize_log_text, sanitize_log_value


DEFAULT_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_HANDLER_MARKER = "_telepiplex_sdk_handler"


class _SanitizingFilter(logging.Filter):
    def filter(self, record):
        if getattr(record, "_telepiplex_sanitized", False):
            return True
        record.msg = sanitize_log_text(record.getMessage())
        record.args = ()
        record._telepiplex_sanitized = True
        return True


def configure_feature_logging(context) -> logging.Logger:
    level_name = str(os.environ.get("TPX_LOG_LEVEL") or "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT, DEFAULT_DATE_FORMAT))
    handler.addFilter(_SanitizingFilter())
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)
    logger = logging.getLogger(f"telepiplex.feature.{context.manifest['plugin_id']}")
    logger.setLevel(level)
    logger.info(
        "feature_runtime_bootstrap "
        f"plugin_id={sanitize_log_text(context.manifest['plugin_id'])} "
        f"version={sanitize_log_text(context.manifest['version'])} "
        f"config_path={sanitize_log_text(context.config_path)} "
        f"state_path={sanitize_log_text(context.state_path)} "
        f"runtime_log={sanitize_log_text(os.environ.get('TPX_RUNTIME_LOG_PATH') or '')}"
    )
    return logger


def log_dispatch_start(method: str, key: str, params: dict):
    logging.getLogger("telepiplex.runtime").info(
        "feature_dispatch_start "
        f"method={sanitize_log_text(method)} "
        f"key={sanitize_log_text(key)} "
        f"params={sanitize_log_value(params, max_chars=4000)}"
    )


def log_dispatch_finish(method: str, key: str, result: dict):
    logging.getLogger("telepiplex.runtime").info(
        "feature_dispatch_finish "
        f"method={sanitize_log_text(method)} "
        f"key={sanitize_log_text(key)} "
        f"result={sanitize_log_value(result, max_chars=4000)}"
    )


def log_dispatch_error(method: str, key: str, code: str, detail):
    logging.getLogger("telepiplex.runtime").error(
        "feature_dispatch_error "
        f"method={sanitize_log_text(method)} "
        f"key={sanitize_log_text(key)} "
        f"code={sanitize_log_text(code)} "
        f"detail={sanitize_log_text(detail)}"
    )
