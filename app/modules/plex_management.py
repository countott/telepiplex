# -*- coding: utf-8 -*-

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


plex_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="plex-management")
_service = None
_ai = None
_ai_error = ""
_mcp_handle = None


def _queue_notifier(user_id, message, confirmation=None):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.helpers import escape_markdown
    from app.utils.message_queue import add_task_to_queue

    keyboard = None
    if confirmation:
        rows = []
        kind = confirmation.get("kind")
        for index, candidate in enumerate(confirmation.get("candidates") or []):
            value = candidate.get("rating_key") if kind == "location" else candidate.get("guid")
            if not value:
                continue
            label = candidate.get("title") or candidate.get("name") or str(value)
            year = candidate.get("year")
            if year:
                label = f"{label} ({year})"
            rows.append([
                InlineKeyboardButton(
                    str(label)[:50],
                    callback_data=f"plex_match_confirm:{confirmation.get('job_id', '')}:{index}",
                )
            ])
        if rows:
            keyboard = InlineKeyboardMarkup(rows)
    return add_task_to_queue(
        user_id,
        None,
        escape_markdown(str(message), version=2),
        keyboard=keyboard,
    )


def _service_config():
    import init

    config = init.bot_config or {}
    plex_config = ((config.get("media") or {}).get("plex") or {})
    management = plex_config.get("management") or {}
    return config, plex_config, management


def get_plex_management_service():
    global _service
    if _service is not None:
        return _service
    config, plex_config, management = _service_config()
    if not management.get("enabled", True):
        return None
    base_url = str(plex_config.get("base_url") or "").strip()
    token = str(plex_config.get("token") or "").strip()
    if not base_url or not token:
        return None

    from app.adapters.fanart import FanartAdapter
    from app.adapters.plex import PlexAdapter
    from app.adapters.tmdb import TmdbAdapter
    from app.repositories.plex_jobs import PlexJobRepository
    from app.services.plex_management import PlexManagementService

    tmdb_config = ((config.get("metadata") or {}).get("tmdb") or {})
    fanart_config = ((config.get("artwork") or {}).get("fanart") or {})
    tmdb = TmdbAdapter(tmdb_config["api_key"], tmdb_config.get("timeout", 15)) if tmdb_config.get("api_key") else None
    fanart = FanartAdapter(fanart_config["api_key"], fanart_config.get("timeout", 15)) if fanart_config.get("api_key") else None
    _service = PlexManagementService(
        PlexJobRepository(management.get("database_path") or "/config/plex_management.db"),
        PlexAdapter(base_url, token, plex_config.get("timeout", 30)),
        tmdb=tmdb,
        fanart=fanart,
        notifier=_queue_notifier,
        category_folders=config.get("category_folder") or [],
        scan_poll_interval=management.get("scan_poll_interval", 5),
        scan_timeout=management.get("scan_timeout", 300),
    )
    _service.enabled = True
    _service.mcp_config = dict(plex_config.get("mcp") or {})
    _service.mcp_enabled = bool(_service.mcp_config.get("enabled"))
    _service.ai_config = dict(plex_config.get("ai") or {})
    global_ai = dict(config.get("ai") or {})
    requested_ai = bool(_service.ai_config.get("enabled"))
    has_ai_credentials = all(
        str(global_ai.get(key) or "").strip()
        for key in ("api_url", "api_key", "model")
    )
    _service.ai_enabled = requested_ai and has_ai_credentials
    _service.global_ai_config = global_ai
    _service.ai = None
    _service.ai_error = ""
    return _service


def get_plex_ai_orchestrator():
    global _ai, _ai_error
    service = get_plex_management_service()
    if service is None or not getattr(service, "ai_enabled", False):
        return None
    existing = getattr(service, "ai", None)
    if existing is not None:
        _ai = existing
        return existing
    if _ai is not None:
        service.ai = _ai
        return _ai

    try:
        from app.plex_mcp.server import PlexToolDispatcher
        from app.services.plex_ai import PlexAIOrchestrator

        _ai = PlexAIOrchestrator(
            getattr(service, "global_ai_config", {}) or {},
            PlexToolDispatcher(service),
            max_tool_rounds=(getattr(service, "ai_config", {}) or {}).get(
                "max_tool_rounds",
                3,
            ),
        )
    except Exception as exc:
        _ai = None
        _ai_error = _safe_startup_error(exc)
        service.ai_error = _ai_error
        raise
    _ai_error = ""
    service.ai_error = ""
    service.ai = _ai
    return _ai


def on_download_completed(completion):
    if not str(completion.terminal_processor or "").startswith("renaming."):
        return None
    service = get_plex_management_service()
    if service is None or not getattr(service, "enabled", False):
        return None
    job = service.enqueue_completion(completion)
    if job:
        plex_executor.submit(service.run_job, job["id"])
    return job


def _safe_startup_error(exc):
    try:
        from app.services.plex_management import PlexManagementService

        return PlexManagementService._safe_error(exc)
    except Exception:
        return type(exc).__name__


def _log_startup_failure(component, exc):
    try:
        import init

        if init.logger:
            init.logger.error(
                f"{component}启动失败：{_safe_startup_error(exc)}"
            )
    except Exception:
        pass


def start_plex_module_services(_application=None):
    global _mcp_handle
    try:
        service = get_plex_management_service()
    except Exception as exc:
        _log_startup_failure("Plex service", exc)
        return None
    if service is None:
        return None
    try:
        service.resume_incomplete_jobs(plex_executor)
    except Exception as exc:
        _log_startup_failure("Plex 任务恢复", exc)
    if service.mcp_enabled:
        from app.plex_mcp.server import start_plex_mcp_server

        try:
            _mcp_handle = start_plex_mcp_server(service, service.mcp_config)
        except Exception as exc:
            _mcp_handle = None
            _log_startup_failure("Plex MCP", exc)
    return _mcp_handle


def _register_handlers(application):
    from app.handlers.plex_handler import register_plex_handlers

    register_plex_handlers(application)


def register_module(registry):
    registry.add_commands([("plex", "管理 Plex 媒体库")])
    registry.add_handlers(_register_handlers)
    registry.add_config_sections([
        "media.plex",
        "media.plex.management",
        "media.plex.mcp",
        "media.plex.ai",
        "metadata.tmdb",
        "artwork.fanart",
    ])
    registry.add_download_completion_hook(on_download_completed, "plex.management")
    registry.add_startup_hook(start_plex_module_services)
