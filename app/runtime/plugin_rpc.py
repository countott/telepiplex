from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from app.runtime.plugin_contract import ContractError


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
            raise ContractError("deadline_exceeded", "RPC deadline exceeded") from None
        except ContractError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
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
            return result
        error = response.get("error") or {}
        raise ContractError(
            str(error.get("code") or "internal_error"),
            str(error.get("message") or "Feature request failed"),
        )

