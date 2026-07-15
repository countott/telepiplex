from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from .types import FeatureError


class CoreClient:
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
        deadline: float = 10,
    ) -> dict:
        if not isinstance(report, dict):
            raise FeatureError("invalid_request", "operation report must be an object")
        return await self._request(
            "operation.report",
            dict(report),
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
            raise FeatureError("deadline_exceeded", "Core RPC deadline must be positive")
        request_id = uuid.uuid4().hex
        envelope = {
            "type": "request",
            "id": request_id,
            "method": str(method),
            "params": params,
            "token": self.token,
            "deadline_at": time.time() + float(deadline),
            "idempotency_key": str(idempotency_key or ""),
        }
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
            raise FeatureError("frame_too_large", "Core RPC request exceeds frame limit")

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
                    raise FeatureError("invalid_response", "Core RPC response is empty or too large")
                response = json.loads(response_frame.decode("utf-8"))
        except TimeoutError:
            raise FeatureError("deadline_exceeded", "Core RPC deadline exceeded") from None
        except FeatureError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise FeatureError("core_unavailable", f"Core RPC unavailable: {type(exc).__name__}") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

        if not isinstance(response, dict) or response.get("id") != request_id:
            raise FeatureError("invalid_response", "Core RPC response ID mismatch")
        if response.get("ok") is True:
            result = response.get("result")
            if not isinstance(result, dict):
                raise FeatureError("invalid_response", "Core RPC result must be an object")
            return result
        error = response.get("error") or {}
        raise FeatureError(
            str(error.get("code") or "internal_error"),
            str(error.get("message") or "Core request failed"),
        )
