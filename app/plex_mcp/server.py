# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import secrets
import threading
from dataclasses import dataclass

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import PlainTextResponse


READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, idempotentHint=True, destructiveHint=True)


class PlexMcpConfigError(ValueError):
    """Raised when the MCP listener configuration is unsafe."""


def validate_mcp_config(config):
    config = dict(config or {})
    host = str(config.get("host") or "127.0.0.1")
    token = str(config.get("auth_token") or "")
    if host not in {"127.0.0.1", "localhost", "::1"} and not token:
        raise PlexMcpConfigError("Non-loopback MCP listeners require auth_token")
    return {
        "host": host,
        "port": int(config.get("port") or 8765),
        "path": "/" + str(config.get("path") or "/mcp").strip("/"),
        "auth_token": token,
    }


def _prepare_or_apply(service, action, payload, confirmation_token=""):
    if confirmation_token:
        return service.apply_operation(action, payload, confirmation_token)
    return service.prepare_operation(action, payload)


def create_plex_mcp(service, config):
    normalized = validate_mcp_config(config)
    mcp = FastMCP(
        "Telepiplex Plex",
        stateless_http=True,
        json_response=True,
        streamable_http_path=normalized["path"],
    )

    @mcp.tool(name="plex_server_status", annotations=READ_ONLY)
    def plex_server_status():
        """Return Plex server connectivity and identity."""
        return service.server_status()

    @mcp.tool(name="plex_list_libraries", annotations=READ_ONLY)
    def plex_list_libraries():
        """List Plex libraries and their IDs."""
        return service.list_libraries()

    @mcp.tool(name="plex_inspect_item", annotations=READ_ONLY)
    def plex_inspect_item(rating_key: str):
        """Inspect normalized metadata and streams for one Plex item."""
        return service.inspect_item(rating_key)

    @mcp.tool(name="plex_list_match_candidates", annotations=READ_ONLY)
    def plex_list_match_candidates(rating_key: str):
        """List metadata match candidates for a Plex item."""
        return service.list_match_candidates(rating_key)

    @mcp.tool(name="plex_list_artwork_candidates", annotations=READ_ONLY)
    def plex_list_artwork_candidates(rating_key: str):
        """List textless TMDB/Fanart candidates and existing Plex posters."""
        return service.list_artwork_candidates(rating_key)

    @mcp.tool(name="plex_get_job", annotations=READ_ONLY)
    def plex_get_job(job_id: int):
        """Get one Plex management job."""
        return service.get_job(job_id)

    @mcp.tool(name="plex_list_jobs", annotations=READ_ONLY)
    def plex_list_jobs(limit: int = 50):
        """List recent Plex management jobs."""
        return service.list_jobs(limit)

    @mcp.tool(name="plex_scan_library", annotations=WRITE)
    def plex_scan_library(library_id: str, confirmation_token: str = ""):
        """Prepare or confirm a Plex library scan."""
        payload = {"library_id": library_id}
        return _prepare_or_apply(service, "plex_scan_library", payload, confirmation_token)

    @mcp.tool(name="plex_fix_match", annotations=WRITE)
    def plex_fix_match(
        job_id: int,
        rating_key: str,
        candidate_guid: str,
        confirmation_token: str = "",
    ):
        """Prepare or confirm applying one explicit metadata match."""
        payload = {
            "job_id": int(job_id),
            "rating_key": rating_key,
            "candidate_guid": candidate_guid,
        }
        return _prepare_or_apply(service, "plex_fix_match", payload, confirmation_token)

    @mcp.tool(name="plex_refresh_chinese_metadata", annotations=WRITE)
    def plex_refresh_chinese_metadata(rating_key: str, confirmation_token: str = ""):
        """Prepare or confirm an item-level zh-CN metadata refresh."""
        payload = {"rating_key": rating_key}
        return _prepare_or_apply(
            service, "plex_refresh_chinese_metadata", payload, confirmation_token
        )

    @mcp.tool(name="plex_set_textless_poster", annotations=WRITE)
    def plex_set_textless_poster(
        rating_key: str,
        url: str,
        confirmation_token: str = "",
    ):
        """Prepare or confirm setting a selected textless poster URL."""
        payload = {"rating_key": rating_key, "url": url}
        return _prepare_or_apply(service, "plex_set_textless_poster", payload, confirmation_token)

    @mcp.tool(name="plex_select_original_audio", annotations=WRITE)
    def plex_select_original_audio(
        rating_key: str,
        part_id: int,
        stream_id: int,
        confirmation_token: str = "",
    ):
        """Prepare or confirm selecting an original-language audio stream."""
        payload = {
            "rating_key": rating_key,
            "part_id": int(part_id),
            "stream_id": int(stream_id),
        }
        return _prepare_or_apply(service, "plex_select_original_audio", payload, confirmation_token)

    @mcp.tool(name="plex_select_chi_subtitle", annotations=WRITE)
    def plex_select_chi_subtitle(
        rating_key: str,
        part_id: int,
        stream_id: int,
        confirmation_token: str = "",
    ):
        """Prepare or confirm selecting an existing chi subtitle stream."""
        payload = {
            "rating_key": rating_key,
            "part_id": int(part_id),
            "stream_id": int(stream_id),
        }
        return _prepare_or_apply(service, "plex_select_chi_subtitle", payload, confirmation_token)

    @mcp.tool(name="plex_run_management_pipeline", annotations=WRITE)
    def plex_run_management_pipeline(job_id: int, confirmation_token: str = ""):
        """Prepare or confirm running a queued Plex management job."""
        payload = {"job_id": int(job_id)}
        return _prepare_or_apply(
            service, "plex_run_management_pipeline", payload, confirmation_token
        )

    @mcp.tool(name="plex_retry_job", annotations=WRITE)
    def plex_retry_job(job_id: int, confirmation_token: str = ""):
        """Prepare or confirm retrying a Plex management job."""
        payload = {"job_id": int(job_id)}
        return _prepare_or_apply(service, "plex_retry_job", payload, confirmation_token)

    return mcp


class PlexToolDispatcher:
    """Expose the exact MCP tool schemas and dispatch semantics to local AI."""

    def __init__(self, service):
        self.mcp = create_plex_mcp(service, {})
        self._schemas = None

    def tool_schemas(self):
        if self._schemas is None:
            tools = asyncio.run(self.mcp.list_tools())
            self._schemas = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema,
                    },
                }
                for tool in tools
            ]
        return list(self._schemas)

    def dispatch(self, name, arguments):
        return asyncio.run(
            self.mcp._tool_manager.call_tool(
                str(name),
                dict(arguments or {}),
                convert_result=False,
            )
        )


class BearerAuthMiddleware:
    def __init__(self, app, expected_token):
        self.app = app
        self.expected = f"Bearer {expected_token}"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            if not secrets.compare_digest(headers.get("authorization", ""), self.expected):
                response = PlainTextResponse("Unauthorized", status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_plex_mcp_app(service, config):
    normalized = validate_mcp_config(config)
    app = create_plex_mcp(service, normalized).streamable_http_app()
    if normalized["auth_token"]:
        return BearerAuthMiddleware(app, normalized["auth_token"])
    return app


@dataclass
class McpServerHandle:
    server: uvicorn.Server
    thread: threading.Thread


def start_plex_mcp_server(service, config):
    normalized = validate_mcp_config(config)
    app = create_plex_mcp_app(service, normalized)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=normalized["host"],
            port=normalized["port"],
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, name="plex-mcp", daemon=True)
    thread.start()
    return McpServerHandle(server=server, thread=thread)
