from __future__ import annotations

import asyncio
import json
import inspect
import time
from dataclasses import dataclass
from pathlib import Path

from app.runtime.capability_router import CapabilityRouter, RoutingError
from app.runtime.event_journal import EventJournal, EventJournalError
from app.runtime.interaction_coordinator import InteractionError
from app.runtime.plugin_manifest import PluginManifest
from telepiplex_plugin_sdk.diagnostics import (
    bind_diagnostic_context,
    bounded_diagnostic_value,
)


class BrokerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class BrokerIdentity:
    plugin_id: str
    manifest: PluginManifest


class RuntimeBroker:
    def __init__(
        self,
        router: CapabilityRouter,
        journal: EventJournal,
        socket_path: Path,
        *,
        dispatcher=None,
        notification_sink=None,
        milestone_sink=None,
        operation_sink=None,
        operation_coordinator=None,
        logger=None,
        max_frame_bytes: int = 1024 * 1024,
        max_deadline: float = 300,
    ):
        self.router = router
        self.journal = journal
        self.socket_path = Path(socket_path)
        self.dispatcher = dispatcher
        self.notification_sink = notification_sink
        self.milestone_sink = milestone_sink
        self.operation_sink = operation_sink
        self.operation_coordinator = operation_coordinator
        self.logger = logger
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
        identity = None
        method = ""
        started_ns = time.monotonic_ns()
        transport = None
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
            method = str(request.get("method") or "")
            transport = {
                "direction": "feature_to_host",
                "plugin_id": identity.plugin_id,
                "method": method,
                "request_id": request_id,
                "deadline_ms": call_deadline * 1000,
                "idempotency_key_present": bool(request.get("idempotency_key")),
            }
            self._diagnostic_log(
                "info",
                "Host 收到 Feature RPC",
                event_name="rpc.host.received",
                diagnostic_fields={
                    "stage": "rpc",
                    "status": "received",
                    "input": {
                        "params": bounded_diagnostic_value(params),
                    },
                    "transport": transport,
                },
            )
            diagnostics = request.get("diagnostics")
            if not isinstance(diagnostics, dict):
                diagnostics = {}
            bound_diagnostics = dict(diagnostics)
            bound_diagnostics["request_id"] = request_id
            bound_diagnostics["operation_id"] = (
                params.get("operation_id")
                or diagnostics.get("operation_id")
            )
            with bind_diagnostic_context(**bound_diagnostics):
                async with asyncio.timeout(call_deadline):
                    result = await self._dispatch(identity, request, params, call_deadline)
            self._diagnostic_log(
                "info",
                "Host 完成 Feature RPC",
                event_name="rpc.host.completed",
                diagnostic_fields={
                    "stage": "rpc",
                    "status": "completed",
                    "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                    "transport": transport,
                    "output": {
                        "result": bounded_diagnostic_value(result),
                    },
                },
            )
            response = {"type": "response", "id": request_id, "ok": True, "result": result}
        except BrokerError as exc:
            response = {
                "type": "response",
                "id": request_id,
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            }
        except InteractionError as exc:
            response = {
                "type": "response",
                "id": request_id,
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            }
        except TimeoutError:
            response = {
                "type": "response", "id": request_id, "ok": False,
                "error": {"code": "deadline_exceeded", "message": "Host request deadline exceeded"},
            }
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            response = {
                "type": "response", "id": request_id, "ok": False,
                "error": {"code": "invalid_request", "message": "invalid request"},
            }
        except Exception as exc:
            if self.logger is not None:
                error = getattr(self.logger, "error", None)
                if error is not None:
                    safe_message = (
                        "event=runtime_broker.internal_error "
                        f"request_id={request_id or '-'} "
                        f"plugin_id={getattr(identity, 'plugin_id', '-') or '-'} "
                        f"method={method or '-'} "
                        f"error_type={type(exc).__name__}"
                    )
                    fields = {
                        "stage": "rpc",
                        "status": "failed",
                        "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                        "transport": transport or {
                            "direction": "feature_to_host",
                            "plugin_id": getattr(identity, "plugin_id", None),
                            "method": method or None,
                            "request_id": request_id or None,
                        },
                        "output": {
                            "error_code": "internal_error",
                            "error_type": type(exc).__name__,
                        },
                    }
                    try:
                        error(
                            safe_message,
                            exc_info=(type(exc), exc, exc.__traceback__),
                            event_name="runtime_broker.internal_error",
                            diagnostic_fields=fields,
                        )
                    except TypeError:
                        error(
                            safe_message,
                            exc_info=(type(exc), exc, exc.__traceback__),
                            extra={
                                "event_name": "runtime_broker.internal_error",
                                "diagnostic_fields": fields,
                            },
                        )
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

    def _diagnostic_log(
        self,
        level: str,
        message: str,
        *,
        event_name: str,
        diagnostic_fields: dict,
    ):
        if self.logger is None:
            return
        emit = getattr(self.logger, str(level), None)
        if emit is None:
            return
        try:
            emit(
                message,
                event_name=event_name,
                diagnostic_fields=diagnostic_fields,
            )
        except TypeError:
            emit(
                message,
                extra={
                    "event_name": event_name,
                    "diagnostic_fields": diagnostic_fields,
                },
            )

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
            coordinator = self.operation_coordinator
            operation_id = str(payload.get("operation_id") or "").strip()
            handoff = (
                coordinator.capture_handoff(operation_id, identity.plugin_id)
                if coordinator is not None and operation_id
                else None
            )
            durable_handoff = None
            if handoff is not None:
                durable_handoff = {
                    "operation_id": handoff.operation_id,
                    "handoff_key": handoff.handoff_key,
                    "source_plugin_id": handoff.source_plugin_id,
                    "source_revision": handoff.source_revision,
                    "target_plugin_id": handoff.target_plugin_id,
                }
            try:
                event_id = self.journal.publish(
                    event_type,
                    payload,
                    idempotency_key,
                    handoff_binding=durable_handoff,
                )
            except EventJournalError as exc:
                raise BrokerError(exc.code, exc.message) from None
            try:
                binding = self.journal.handoff_binding(event_id)
                if binding is not None:
                    if (
                        binding.operation_id != operation_id
                        or binding.source_plugin_id != identity.plugin_id
                    ):
                        raise BrokerError(
                            "handoff_event_conflict",
                            "event delivery belongs to another handoff",
                        )
                    if coordinator is None:
                        raise BrokerError(
                            "operation_unavailable",
                            "Host operation coordinator is unavailable",
                        )
                    coordinator.record_handoff_event(
                        binding.operation_id,
                        event_id,
                        binding.target_plugin_id,
                        handoff_key=binding.handoff_key,
                    )
            finally:
                if self.dispatcher is not None:
                    self.dispatcher.wake()
            return {"event_id": event_id}
        if method == "notification.send":
            try:
                user_id = int(params.get("user_id"))
            except (TypeError, ValueError):
                user_id = 0
            text = str(params.get("text") or "")
            if user_id <= 0 or not text or len(text) > 4096:
                raise BrokerError(
                    "invalid_notification",
                    "notification requires a valid user and at most 4096 characters",
                )
            if self.notification_sink is None:
                raise BrokerError("notification_unavailable", "Host notification sink is unavailable")
            accepted = self.notification_sink(user_id, text)
            if inspect.isawaitable(accepted):
                accepted = await accepted
            return {"accepted": accepted is not False}
        if method == "operation.report":
            if self.operation_sink is None:
                raise BrokerError(
                    "operation_unavailable", "Host operation coordinator is unavailable"
                )
            result = self.operation_sink(identity.plugin_id, dict(params))
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                raise BrokerError(
                    "internal_error", "Host operation sink must return an object"
                )
            return result
        if method == "operation.milestone":
            operation_id = str(params.get("operation_id") or "").strip()
            milestone_id = str(params.get("milestone_id") or "").strip()
            mode = str(params.get("mode") or "identity").strip().casefold()
            text = str(params.get("text") or "").strip()
            photo_url = str(params.get("photo_url") or "").strip()
            text_limit = 1024 if photo_url else 4096
            if (
                not operation_id
                or not milestone_id
                or mode not in {"identity", "stage"}
                or not text
                or len(text) > text_limit
                or len(photo_url) > 2048
                or (photo_url and not photo_url.startswith("https://"))
                or (mode == "stage" and photo_url)
            ):
                raise BrokerError(
                    "invalid_milestone",
                    "operation milestone payload is invalid",
                )
            if self.milestone_sink is None:
                raise BrokerError(
                    "operation_unavailable",
                    "Host operation milestone sink is unavailable",
                )
            payload = {
                "operation_id": operation_id,
                "milestone_id": milestone_id,
                "mode": mode,
                "text": text,
                "photo_url": photo_url,
            }
            accepted = self.milestone_sink(identity.plugin_id, payload)
            if inspect.isawaitable(accepted):
                accepted = await accepted
            if isinstance(accepted, dict):
                return accepted
            return {"accepted": accepted is not False}
        raise BrokerError("not_found", f"unknown Host RPC method: {method}")
