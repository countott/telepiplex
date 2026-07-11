# -*- coding: utf-8 -*-

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


plex_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="plex-management")
_service = None
_mcp_handle = None


def _queue_notifier(user_id, message, confirmation=None):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from app.utils.message_queue import add_task_to_queue

    keyboard = None
    if confirmation:
        rows = []
        kind = confirmation.get("kind")
        for candidate in confirmation.get("candidates") or []:
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
                    callback_data=f"plex_match_confirm:{confirmation.get('job_id', '')}:{value}",
                )
            ])
        if rows:
            keyboard = InlineKeyboardMarkup(rows)
    return add_task_to_queue(user_id, None, message, keyboard=keyboard)


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
    return _service


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


def start_plex_module_services(_application=None):
    global _mcp_handle
    service = get_plex_management_service()
    if service is None:
        return None
    service.resume_incomplete_jobs(plex_executor)
    if service.mcp_enabled:
        from app.plex_mcp.server import start_plex_mcp_server

        _mcp_handle = start_plex_mcp_server(service, service.mcp_config)
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
