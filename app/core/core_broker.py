from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.capability_router import CapabilityRouter, RoutingError
from app.core.event_journal import EventJournal
from app.core.plugin_manifest import PluginManifest


class BrokerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class BrokerIdentity:
    plugin_id: str
    manifest: PluginManifest


class CoreBroker:
    def __init__(
        self,
        router: CapabilityRouter,
        journal: EventJournal,
        socket_path: Path,
        *,
        dispatcher=None,
        max_frame_bytes: int = 1024 * 1024,
        max_deadline: float = 300,
    ):
        self.router = router
        self.journal = journal
        self.socket_path = Path(socket_path)
        self.dispatcher = dispatcher
        self.max_frame_bytes = int(max_frame_bytes)
        self.max_deadline = max(1, float(max_deadline))
        self._identities: dict[str, BrokerIdentity] = {}
        self._server = None

    def register(self, plugin_id: str, token: str, manifest: PluginManifest):
        if str(plugin_id) != manifest.plugin_id or not str(token):
            raise BrokerError("identity_mismatch", "Feature broker identity is invalid")
        self._identities[str(token)] = BrokerIdentity(str(plugin_id), manifest)

    def unregister(self, token: str):
        self._identities.pop(str(token), None)

    async def start(self):
        if self._server is not None:
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(self.socket_path),
            limit=self.max_frame_bytes + 1,
        )
        self.socket_path.chmod(0o600)
        if self.dispatcher is not None:
            await self.dispatcher.start()

    async def close(self):
        if self.dispatcher is not None:
            await self.dispatcher.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.socket_path.unlink(missing_ok=True)
        self._identities.clear()

    async def _handle_connection(self, reader, writer):
        request_id = ""
        try:
            frame = await reader.readline()
            if not frame or len(frame) > self.max_frame_bytes:
                raise BrokerError("invalid_request", "request frame is empty or too large")
            request = json.loads(frame.decode("utf-8"))
            if not isinstance(request, dict) or request.get("type") != "request":
                raise BrokerError("invalid_request", "request envelope is invalid")
            request_id = str(request.get("id") or "")
            if not request_id:
                raise BrokerError("invalid_request", "request ID is required")
            identity = self._identities.get(str(request.get("token") or ""))
            if identity is None:
                raise BrokerError("unauthorized", "Feature token is not registered")
            remaining = float(request.get("deadline_at") or 0) - time.time()
            if remaining <= 0:
                raise BrokerError("deadline_exceeded", "request deadline has expired")
            params = request.get("params")
            if not isinstance(params, dict):
                raise BrokerError("invalid_request", "request params must be an object")
            call_deadline = min(remaining, self.max_deadline)
            async with asyncio.timeout(call_deadline):
                result = await self._dispatch(identity, request, params, call_deadline)
            response = {"type": "response", "id": request_id, "ok": True, "result": result}
        except BrokerError as exc:
            response = {
                "type": "response",
                "id": request_id,
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            }
        except TimeoutError:
            response = {
                "type": "response", "id": request_id, "ok": False,
                "error": {"code": "deadline_exceeded", "message": "Core request deadline exceeded"},
            }
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            response = {
                "type": "response", "id": request_id, "ok": False,
                "error": {"code": "invalid_request", "message": "invalid request"},
            }
        except Exception as exc:
            response = {
                "type": "response", "id": request_id, "ok": False,
                "error": {"code": "internal_error", "message": type(exc).__name__},
            }
        try:
            encoded = (json.dumps(
                response, ensure_ascii=False, separators=(",", ":"), allow_nan=False
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

    async def _dispatch(
        self,
        identity: BrokerIdentity,
        request: dict,
        params: dict,
        call_deadline: float,
    ) -> dict:
        method = str(request.get("method") or "")
        idempotency_key = str(request.get("idempotency_key") or "")
        if method == "capability.call":
            capability = str(params.get("capability") or "")
            if capability not in identity.manifest.requires:
                raise BrokerError(
                    "capability_not_declared",
                    f"Feature did not declare required capability: {capability}",
                )
            try:
                return await self.router.call(
                    capability,
                    str(params.get("method") or ""),
                    params.get("payload") if isinstance(params.get("payload"), dict) else {},
                    {
                        "caller_plugin_id": identity.plugin_id,
                        "deadline": call_deadline,
                        "idempotency_key": idempotency_key,
                    },
                )
            except RoutingError as exc:
                raise BrokerError(exc.code, exc.message) from None
        if method == "event.publish":
            event_type = str(params.get("event_type") or "")
            if event_type not in identity.manifest.publishes:
                raise BrokerError(
                    "event_not_declared",
                    f"Feature did not declare published event: {event_type}",
                )
            payload = params.get("payload")
            if not isinstance(payload, dict):
                raise BrokerError("invalid_request", "event payload must be an object")
            event_id = self.journal.publish(event_type, payload, idempotency_key)
            if self.dispatcher is not None:
                self.dispatcher.wake()
            return {"event_id": event_id}
        raise BrokerError("not_found", f"unknown Core RPC method: {method}")
