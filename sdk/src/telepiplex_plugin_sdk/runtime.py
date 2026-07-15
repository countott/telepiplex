from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import logging
import os
import time
from pathlib import Path
from typing import Awaitable, Callable

from .logging_utils import (
    log_dispatch_error,
    log_dispatch_finish,
    log_dispatch_start,
)
from .types import FeatureError


Handler = Callable[[dict], dict | Awaitable[dict]]


class FeatureRuntime:
    def __init__(
        self,
        manifest: dict,
        token: str,
        *,
        capabilities: dict[str, Handler] | None = None,
        events: dict[str, Handler] | None = None,
        commands: dict[str, Handler] | None = None,
        callbacks: dict[str, Handler] | None = None,
        messages: Handler | None = None,
        config_validator: Handler | None = None,
        operation_control: Handler | None = None,
        operation_snapshot: Handler | None = None,
        max_frame_bytes: int = 1024 * 1024,
    ):
        self.manifest = dict(manifest)
        self.token = str(token)
        self.capabilities = dict(capabilities or {})
        self.events = dict(events or {})
        self.commands = dict(commands or {})
        self.callbacks = dict(callbacks or {})
        self.messages = messages
        self.config_validator = config_validator
        self.operation_control = operation_control
        self.operation_snapshot = operation_snapshot
        self.max_frame_bytes = int(max_frame_bytes)
        self.state = "starting"
        self._active_requests = 0
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._server = None
        self._socket_path = None
        self._shutdown = asyncio.Event()
        self.logger = logging.getLogger("telepiplex.runtime")

    @property
    def active_tasks(self) -> int:
        return self._active_requests + len(self._background_tasks)

    def spawn(self, awaitable, *, task_id: str) -> asyncio.Task:
        task_id = str(task_id or "").strip()
        if not task_id:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise FeatureError("invalid_task", "background task ID is required")
        if self.state == "draining":
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise FeatureError("busy", "Feature is draining")
        if task_id in self._background_tasks:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise FeatureError("duplicate_task", f"background task already exists: {task_id}")
        task = asyncio.create_task(awaitable, name=f"feature:{task_id}")
        self._background_tasks[task_id] = task

        def finished(completed):
            self._background_tasks.pop(task_id, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(finished)
        return task

    async def serve(self, socket_path: Path):
        self._socket_path = Path(socket_path)
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self._socket_path),
            limit=self.max_frame_bytes + 1,
        )
        os.chmod(self._socket_path, 0o600)
        self.state = "healthy"
        try:
            await self._shutdown.wait()
        finally:
            self._server.close()
            await self._server.wait_closed()
            self.state = "stopped"
            self._socket_path.unlink(missing_ok=True)

    async def close(self):
        self._shutdown.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._socket_path is not None:
            self._socket_path.unlink(missing_ok=True)

    async def _handle_connection(self, reader, writer):
        request_id = ""
        try:
            frame = await reader.readline()
            if not frame or len(frame) > self.max_frame_bytes:
                raise FeatureError("invalid_request", "request frame is empty or too large")
            request = json.loads(frame.decode("utf-8"))
            if not isinstance(request, dict) or request.get("type") != "request":
                raise FeatureError("invalid_request", "request envelope is invalid")
            request_id = str(request.get("id") or "")
            if not request_id:
                raise FeatureError("invalid_request", "request ID is required")
            if not hmac.compare_digest(str(request.get("token") or ""), self.token):
                raise FeatureError("unauthorized", "startup token does not match")
            remaining = float(request.get("deadline_at") or 0) - time.time()
            if remaining <= 0:
                raise FeatureError("deadline_exceeded", "request deadline has expired")
            params = request.get("params")
            if not isinstance(params, dict):
                raise FeatureError("invalid_request", "request params must be an object")
            try:
                async with asyncio.timeout(remaining):
                    result = await self._dispatch(str(request.get("method") or ""), params)
            except TimeoutError:
                raise FeatureError("deadline_exceeded", "request deadline exceeded") from None
            response = {"type": "response", "id": request_id, "ok": True, "result": result}
        except FeatureError as exc:
            response = {
                "type": "response",
                "id": request_id,
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            }
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
            response = {
                "type": "response",
                "id": request_id,
                "ok": False,
                "error": {"code": "invalid_request", "message": type(exc).__name__},
            }
        except Exception as exc:
            response = {
                "type": "response",
                "id": request_id,
                "ok": False,
                "error": {"code": "internal_error", "message": type(exc).__name__},
            }
        try:
            encoded = (json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ) + "\n").encode("utf-8")
            if len(encoded) <= self.max_frame_bytes:
                writer.write(encoded)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _dispatch(self, method: str, params: dict) -> dict:
        if method == "handshake":
            return {
                "plugin_id": str(self.manifest.get("plugin_id") or ""),
                "version": str(self.manifest.get("version") or ""),
                "core_api": str(self.manifest.get("core_api") or ""),
                "state": self.state,
                "echo": params.get("message"),
            }
        if method == "health":
            return {"state": self.state, "active_tasks": self.active_tasks}
        if method == "drain":
            self.state = "draining"
            return {
                "state": self.state,
                "active_tasks": self.active_tasks,
                "interrupted_task_ids": sorted(self._background_tasks),
            }
        if method == "resume":
            if self.active_tasks:
                raise FeatureError("busy", "Feature still has active tasks")
            self.state = "healthy"
            return {"state": self.state, "active_tasks": 0}
        if method == "shutdown":
            self.state = "stopped"
            asyncio.get_running_loop().call_soon(self._shutdown.set)
            return {"state": "stopped", "active_tasks": self.active_tasks}
        if method == "capability.call":
            return await self._business_call(
                self.capabilities,
                str(params.get("capability") or ""),
                params,
                method,
            )
        if method == "event.deliver":
            return await self._business_call(
                self.events,
                str(params.get("event_type") or ""),
                params,
                method,
            )
        if method == "command.dispatch":
            return await self._business_call(
                self.commands,
                str(params.get("command") or ""),
                params,
                method,
            )
        if method == "callback.dispatch":
            return await self._business_call(
                self.callbacks,
                str(params.get("namespace") or ""),
                params,
                method,
            )
        if method == "message.dispatch" and self.messages is not None:
            return await self._business_call(
                {"message": self.messages},
                "message",
                params,
                method,
            )
        if method == "config.validate" and self.config_validator is not None:
            return await self._business_call(
                {"config.validate": self.config_validator},
                "config.validate",
                params,
                method,
            )
        if method == "operation.control" and self.operation_control is not None:
            return await self._business_call(
                {"operation.control": self.operation_control},
                "operation.control",
                params,
                method,
            )
        if method == "operation.snapshot" and self.operation_snapshot is not None:
            return await self._business_call(
                {"operation.snapshot": self.operation_snapshot},
                "operation.snapshot",
                params,
                method,
            )
        raise FeatureError("not_found", f"unknown RPC method: {method}")

    async def _business_call(
        self,
        handlers: dict,
        key: str,
        params: dict,
        method: str,
    ) -> dict:
        if self.state == "draining":
            raise FeatureError("busy", "Feature is draining")
        handler = handlers.get(key)
        if handler is None:
            raise FeatureError("not_found", f"handler is not registered: {key}")
        log_dispatch_start(method, key, params)
        self._active_requests += 1
        try:
            result = await self._invoke(handler, params)
        except FeatureError as exc:
            log_dispatch_error(method, key, exc.code, exc.message)
            raise
        except Exception as exc:
            log_dispatch_error(method, key, type(exc).__name__, exc)
            raise
        finally:
            self._active_requests -= 1
        log_dispatch_finish(method, key, result)
        return result

    @staticmethod
    async def _invoke(handler: Handler, params: dict) -> dict:
        result = handler(params)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise FeatureError("internal_error", "Feature handler must return an object")
        return result
