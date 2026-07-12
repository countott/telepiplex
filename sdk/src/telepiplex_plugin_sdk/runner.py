from __future__ import annotations

import asyncio
import importlib
import inspect
import os
from pathlib import Path

from .core_client import CoreClient
from .runtime import FeatureRuntime
from .types import RuntimeContext


def _context_from_environment() -> tuple[str, RuntimeContext]:
    plugin_id = os.environ["TPX_PLUGIN_ID"]
    version = os.environ["TPX_PLUGIN_VERSION"]
    entry_point = os.environ["TPX_ENTRY_POINT"]
    token = os.environ["TPX_STARTUP_TOKEN"]
    core_socket_path = Path(os.environ["TPX_CORE_SOCKET_PATH"])
    context = RuntimeContext(
        manifest={
            "plugin_id": plugin_id,
            "version": version,
            "core_api": ">=1.0,<2.0",
        },
        token=token,
        socket_path=Path(os.environ["TPX_SOCKET_PATH"]),
        core_socket_path=core_socket_path,
        config_path=Path(os.environ["TPX_CONFIG_PATH"]),
        state_path=Path(os.environ["TPX_STATE_PATH"]),
        core=CoreClient(core_socket_path, token),
    )
    return entry_point, context


async def run():
    entry_point, context = _context_from_environment()
    module_name, function_name = entry_point.split(":", 1)
    factory = getattr(importlib.import_module(module_name), function_name)
    runtime = factory(context)
    if inspect.isawaitable(runtime):
        runtime = await runtime
    if not isinstance(runtime, FeatureRuntime):
        raise TypeError("Feature entry point must return FeatureRuntime")
    await runtime.serve(context.socket_path)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
