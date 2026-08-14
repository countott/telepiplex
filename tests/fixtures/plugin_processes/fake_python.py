#!/usr/bin/env python3
import asyncio
import hmac
import json
import os
import sys
import time
from pathlib import Path


PLUGIN_ID = os.environ["TPX_PLUGIN_ID"]
VERSION = os.environ["TPX_PLUGIN_VERSION"]
TOKEN = os.environ["TPX_STARTUP_TOKEN"]
SOCKET = Path(os.environ["TPX_SOCKET_PATH"])


async def main():
    if PLUGIN_ID == "nosocket":
        await asyncio.sleep(60)
        return
    if PLUGIN_ID == "secretlog":
        print(f"startup token={TOKEN}", flush=True)
    if PLUGIN_ID == "severitylogs":
        print("[2026-07-26 08:00:00] [WARNING] [feature.example] structured warning", flush=True)
        print("[2026-07-26 08:00:01] [ERROR] [feature.example] structured error", flush=True)
        print("[2026-07-26 08:00:02] [CRITICAL] [feature.example] structured critical", flush=True)
        print("plain stdout", flush=True)
        print("plain stderr", file=sys.stderr, flush=True)
    if PLUGIN_ID == "diagnosticlog":
        event = {
            "schema_version": "1.0",
            "event_id": "EVT-FEATURE-TRANSPORT-1",
            "sequence": {"producer": 9, "ingest": None},
            "time": {
                "utc": "2026-08-14T15:15:42.381000+00:00",
                "local": "2026-08-14T23:15:42.381000+08:00",
                "timezone": "CST",
                "unix_ns": 1786720542381000000,
                "monotonic_ns": 123456789,
            },
            "level": "INFO",
            "logger": "telepiplex.feature.diagnosticlog",
            "component": "diagnosticlog",
            "identity": {
                "session_id": "feature-placeholder",
                "trace_id": "TRC-FEATURE-1",
                "span_id": "SPN-FEATURE-1",
                "parent_span_id": None,
                "operation_id": "operation-feature-1",
                "request_id": "request-feature-1",
                "incident_id": None,
            },
            "event": {
                "name": "feature.dispatch.completed",
                "message": "Feature 诊断传输完成",
                "stage": "dispatch",
                "status": "completed",
                "duration_ms": 12.5,
            },
            "runtime": {
                "host_version": None,
                "plugin_id": "diagnosticlog",
                "plugin_version": "1.0.0",
                "instance_id": "diagnosticlog",
                "pid": os.getpid(),
                "thread_name": "MainThread",
                "thread_id": 1,
                "async_task": "fixture",
            },
            "facts": {
                "input": {"args": ["access_token=transport-secret-value"]},
                "output": {"value": "safe"},
            },
            "error": {
                "code": None,
                "type": None,
                "message": None,
                "retryable": None,
                "stack": None,
                "causes": [],
            },
            "privacy": {
                "redacted_paths": [],
                "redaction_count": 0,
                "sanitized": True,
            },
        }
        print("@tpx-event-v1 " + json.dumps(event, ensure_ascii=False), flush=True)

    stop = asyncio.Event()
    state = {"value": "healthy", "drain_started": 0.0}

    async def handle(reader, writer):
        try:
            request = json.loads((await reader.readline()).decode("utf-8"))
            request_id = request.get("id", "")
            if not hmac.compare_digest(str(request.get("token") or ""), TOKEN):
                response = {
                    "id": request_id,
                    "ok": False,
                    "error": {"code": "unauthorized", "message": "bad token"},
                }
            else:
                method = request.get("method")
                if method == "handshake":
                    result = {"plugin_id": PLUGIN_ID, "version": VERSION, "state": state["value"]}
                elif method == "health":
                    active_tasks = int(
                        state["value"] == "draining"
                        and time.monotonic() - state["drain_started"] < 0.05
                    )
                    result = {"state": state["value"], "active_tasks": active_tasks}
                elif method == "drain":
                    state["value"] = "draining"
                    state["drain_started"] = time.monotonic()
                    result = {
                        "state": "draining",
                        "active_tasks": 1,
                        "interrupted_task_ids": [],
                    }
                elif method == "resume":
                    state["value"] = "healthy"
                    result = {"state": "healthy", "active_tasks": 0}
                elif method == "shutdown":
                    state["value"] = "stopped"
                    result = {"state": "stopped", "active_tasks": 0}
                    asyncio.get_running_loop().call_soon(stop.set)
                else:
                    result = {"ok": True}
                response = {"id": request_id, "ok": True, "result": result}
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(SOCKET))
    if PLUGIN_ID == "crashy":
        asyncio.get_running_loop().call_later(0.05, stop.set)
    await stop.wait()
    server.close()
    await server.wait_closed()
    SOCKET.unlink(missing_ok=True)


asyncio.run(main())
