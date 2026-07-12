#!/usr/bin/env python3
import asyncio
import hmac
import json
import os
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

    stop = asyncio.Event()
    state = {"value": "healthy"}

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
                    result = {"state": state["value"], "active_tasks": 0}
                elif method == "drain":
                    state["value"] = "draining"
                    result = {
                        "state": "draining",
                        "active_tasks": 1,
                        "interrupted_task_ids": ["task-1"],
                    }
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

