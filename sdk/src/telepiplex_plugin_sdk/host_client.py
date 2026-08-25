from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

from .types import FeatureError
from .diagnostics import bounded_diagnostic_value, outbound_diagnostic_context


class HostClient:
    def __init__(self, socket_path: Path, token: str, max_frame_bytes: int = 1024 * 1024):
        self.socket_path = Path(socket_path)
        self.token = str(token)
        self.max_frame_bytes = int(max_frame_bytes)

    async def call_capability(
        self,
        capability: str,
        method: str,
        payload: dict,
        *,
        deadline: float = 30,
        idempotency_key: str = "",
    ) -> dict:
        return await self._request(
            "capability.call",
            {
                "capability": str(capability),
                "method": str(method),
                "payload": payload,
            },
            deadline=deadline,
            idempotency_key=idempotency_key,
        )

    async def publish_event(
        self,
        event_type: str,
        payload: dict,
        *,
        idempotency_key: str = "",
        deadline: float = 10,
    ) -> dict:
        return await self._request(
            "event.publish",
            {"event_type": str(event_type), "payload": payload},
            deadline=deadline,
            idempotency_key=idempotency_key,
        )

    async def notify_user(
        self,
        user_id: int,
        text: str,
        *,
        deadline: float = 10,
        idempotency_key: str = "",
    ) -> dict:
        return await self._request(
            "notification.send",
            {"user_id": user_id, "text": str(text)},
            deadline=deadline,
            idempotency_key=idempotency_key,
        )

    async def report_operation(
        self,
        report: dict,
        *,
        segment: dict | None = None,
        deadline: float = 10,
    ) -> dict:
        if not isinstance(report, dict):
            raise FeatureError("invalid_request", "operation report must be an object")
        payload = dict(report)
        if segment is not None:
            if not isinstance(segment, dict):
                raise FeatureError(
                    "invalid_request",
                    "operation segment must be an object",
                )
            payload["segment"] = dict(segment)
        return await self._request(
            "operation.report",
            payload,
            deadline=deadline,
        )

    async def seal_operation_segment(
        self,
        operation_id: str,
        role: str,
        *,
        deadline: float = 10,
    ) -> dict:
        return await self._request(
            "operation.seal",
            {
                "operation_id": str(operation_id),
                "role": str(role),
            },
            deadline=deadline,
            idempotency_key=f"{str(operation_id)}:{str(role)}:seal",
        )

    async def get_operation_snapshot(
        self,
        operation_id: str,
        *,
        deadline: float = 10,
    ) -> dict:
        return await self._request(
            "operation.get",
            {"operation_id": str(operation_id)},
            deadline=deadline,
        )

    async def publish_operation_milestone(
        self,
        operation_id: str,
        milestone_id: str,
        text: str,
        *,
        mode: str = "identity",
        photo_url: str = "",
        deadline: float = 10,
    ) -> dict:
        return await self._request(
            "operation.milestone",
            {
                "operation_id": str(operation_id),
                "milestone_id": str(milestone_id),
                "mode": str(mode),
                "text": str(text),
                "photo_url": str(photo_url or ""),
            },
            deadline=deadline,
            idempotency_key=(
                f"{str(operation_id)}:{str(milestone_id)}"
            ),
        )

    async def seal_operation_stage(
        self,
        operation_id: str,
        milestone_id: str,
        text: str,
        *,
        deadline: float = 10,
    ) -> dict:
        return await self.publish_operation_milestone(
            operation_id,
            milestone_id,
            text,
            mode="stage",
            deadline=deadline,
        )

    async def _request(
        self,
        method: str,
        params: dict,
        *,
        deadline: float,
        idempotency_key: str = "",
    ) -> dict:
        if deadline <= 0:
            raise FeatureError("deadline_exceeded", "Host RPC deadline must be positive")
        request_id = uuid.uuid4().hex
        diagnostics = outbound_diagnostic_context(
            request_id=request_id,
            operation_id=params.get("operation_id") if isinstance(params, dict) else None,
        )
        envelope = {
            "type": "request",
            "id": request_id,
            "method": str(method),
            "params": params,
            "token": self.token,
            "deadline_at": time.time() + float(deadline),
            "idempotency_key": str(idempotency_key or ""),
            "diagnostics": diagnostics,
        }
        started_ns = time.monotonic_ns()
        rpc_logger = logging.getLogger("telepiplex.rpc.host")
        transport = {
            "direction": "feature_to_host",
            "method": str(method),
            "request_id": request_id,
            "deadline_ms": float(deadline) * 1000,
            "idempotency_key_present": bool(idempotency_key),
        }
        rpc_logger.info(
            "Feature 开始调用 Host RPC",
            extra={
                "event_name": "rpc.host.started",
                "diagnostic_fields": {
                    "stage": "rpc",
                    "status": "started",
                    "input": {
                        "params": bounded_diagnostic_value(params),
                    },
                    "transport": transport,
                },
            },
        )
        try:
            frame = (json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise FeatureError("invalid_request", type(exc).__name__) from None
        if len(frame) > self.max_frame_bytes:
            raise FeatureError("frame_too_large", "Host RPC request exceeds frame limit")

        writer = None
        try:
            async with asyncio.timeout(float(deadline)):
                reader, writer = await asyncio.open_unix_connection(
                    str(self.socket_path),
                    limit=self.max_frame_bytes + 1,
                )
                writer.write(frame)
                await writer.drain()
                response_frame = await reader.readline()
                if not response_frame or len(response_frame) > self.max_frame_bytes:
                    raise FeatureError("invalid_response", "Host RPC response is empty or too large")
                response = json.loads(response_frame.decode("utf-8"))
        except TimeoutError:
            rpc_logger.error(
                "Host RPC 调用超时",
                extra={
                    "event_name": "rpc.host.failed",
                    "diagnostic_fields": {
                        "stage": "rpc",
                        "status": "failed",
                        "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                        "transport": transport,
                        "output": {"error_code": "deadline_exceeded"},
                    },
                },
            )
            raise FeatureError("deadline_exceeded", "Host RPC deadline exceeded") from None
        except FeatureError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            rpc_logger.error(
                "Host RPC 不可用",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "event_name": "rpc.host.failed",
                    "diagnostic_fields": {
                        "stage": "rpc",
                        "status": "failed",
                        "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                        "transport": transport,
                        "output": {"error_code": "host_unavailable"},
                    },
                },
            )
            raise FeatureError("host_unavailable", f"Host RPC unavailable: {type(exc).__name__}") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

        if not isinstance(response, dict) or response.get("id") != request_id:
            raise FeatureError("invalid_response", "Host RPC response ID mismatch")
        if response.get("ok") is True:
            result = response.get("result")
            if not isinstance(result, dict):
                raise FeatureError("invalid_response", "Host RPC result must be an object")
            rpc_logger.info(
                "Host RPC 调用完成",
                extra={
                    "event_name": "rpc.host.completed",
                    "diagnostic_fields": {
                        "stage": "rpc",
                        "status": "completed",
                        "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                        "transport": transport,
                        "output": {
                            "result": bounded_diagnostic_value(result),
                        },
                    },
                },
            )
            return result
        error = response.get("error") or {}
        rpc_logger.error(
            "Host RPC 返回失败",
            extra={
                "event_name": "rpc.host.failed",
                "diagnostic_fields": {
                    "stage": "rpc",
                    "status": "failed",
                    "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                    "transport": transport,
                    "output": {
                        "error_code": str(error.get("code") or "internal_error"),
                        "error_message": str(error.get("message") or "Host request failed"),
                    },
                },
            },
        )
        raise FeatureError(
            str(error.get("code") or "internal_error"),
            str(error.get("message") or "Host request failed"),
        )
