from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

from app.runtime.plugin_contract import ContractError
from telepiplex_plugin_sdk.diagnostics import outbound_diagnostic_context


class RpcClient:
    def __init__(
        self,
        socket_path: Path,
        token: str,
        max_frame_bytes: int = 1024 * 1024,
    ):
        self.socket_path = Path(socket_path)
        self.token = str(token)
        self.max_frame_bytes = int(max_frame_bytes)

    async def request(
        self,
        method: str,
        params: dict,
        *,
        deadline: float,
        idempotency_key: str = "",
    ) -> dict:
        if deadline <= 0:
            raise ContractError("deadline_exceeded", "RPC deadline must be positive")
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
        rpc_logger = logging.getLogger("telepiplex.rpc.feature")
        transport = {
            "direction": "host_to_feature",
            "method": str(method),
            "request_id": request_id,
            "deadline_ms": float(deadline) * 1000,
            "idempotency_key_present": bool(idempotency_key),
        }
        rpc_logger.info(
            "Host 开始调用 Feature RPC",
            extra={
                "event_name": "rpc.feature.started",
                "diagnostic_fields": {
                    "stage": "rpc",
                    "status": "started",
                    "input": {"params": params},
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
            raise ContractError("invalid_request", type(exc).__name__) from None
        if len(frame) > self.max_frame_bytes:
            raise ContractError("frame_too_large", "RPC request exceeds frame limit")

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
                    raise ContractError("invalid_response", "RPC response is empty or too large")
                response = json.loads(response_frame.decode("utf-8"))
        except TimeoutError:
            rpc_logger.error(
                "Feature RPC 调用超时",
                extra={
                    "event_name": "rpc.feature.failed",
                    "diagnostic_fields": {
                        "stage": "rpc",
                        "status": "failed",
                        "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                        "transport": transport,
                        "output": {"error_code": "deadline_exceeded"},
                    },
                },
            )
            raise ContractError("deadline_exceeded", "RPC deadline exceeded") from None
        except ContractError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            rpc_logger.error(
                "Feature RPC 不可用",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={
                    "event_name": "rpc.feature.failed",
                    "diagnostic_fields": {
                        "stage": "rpc",
                        "status": "failed",
                        "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                        "transport": transport,
                        "output": {"error_code": "unavailable"},
                    },
                },
            )
            raise ContractError("unavailable", f"RPC unavailable: {type(exc).__name__}") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

        if not isinstance(response, dict) or response.get("id") != request_id:
            raise ContractError("invalid_response", "RPC response ID mismatch")
        if response.get("ok") is True:
            result = response.get("result")
            if not isinstance(result, dict):
                raise ContractError("invalid_response", "RPC result must be an object")
            rpc_logger.info(
                "Feature RPC 调用完成",
                extra={
                    "event_name": "rpc.feature.completed",
                    "diagnostic_fields": {
                        "stage": "rpc",
                        "status": "completed",
                        "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                        "transport": transport,
                        "output": {"result": result},
                    },
                },
            )
            return result
        error = response.get("error") or {}
        rpc_logger.error(
            "Feature RPC 返回失败",
            extra={
                "event_name": "rpc.feature.failed",
                "diagnostic_fields": {
                    "stage": "rpc",
                    "status": "failed",
                    "duration_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
                    "transport": transport,
                    "output": {
                        "error_code": str(error.get("code") or "internal_error"),
                        "error_message": str(error.get("message") or "Feature request failed"),
                    },
                },
            },
        )
        raise ContractError(
            str(error.get("code") or "internal_error"),
            str(error.get("message") or "Feature request failed"),
        )
