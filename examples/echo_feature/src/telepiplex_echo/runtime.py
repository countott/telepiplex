from __future__ import annotations

import asyncio

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext


def main(context: RuntimeContext) -> FeatureRuntime:
    async def echo(request: dict) -> dict:
        payload = request.get("payload") or {}
        delay = float(payload.get("delay") or 0)
        if delay > 0:
            await asyncio.sleep(delay)
        return {
            "text": str(payload.get("text") or ""),
            "version": str(context.manifest["version"]),
        }

    async def command(request: dict) -> dict:
        text = " ".join(str(value) for value in request.get("args") or [])
        return {
            "actions": [{
                "kind": "send_message",
                "text": f"{context.manifest['version']}: {text}",
            }]
        }

    async def callback(request: dict) -> dict:
        return {
            "actions": [{
                "kind": "send_message",
                "text": f"{context.manifest['version']}: {request.get('payload') or ''}",
            }]
        }

    return FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        capabilities={"demo.echo": echo},
        commands={"echo": command},
        callbacks={"echo": callback},
    )

