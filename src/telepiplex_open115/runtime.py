from __future__ import annotations

import yaml

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext

from .client import Open115Client
from .service import Open115Feature
from .jobs import DownloadJobStore


def main(context: RuntimeContext) -> FeatureRuntime:
    config = yaml.safe_load(context.config_path.read_text(encoding="utf-8")) or {}
    feature = Open115Feature(
        config=config,
        core=context.core,
        client=Open115Client(config),
        jobs=DownloadJobStore(context.state_path / "downloads.db"),
    )
    runtime = FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        capabilities={
            "download.provider": feature.download_capability,
            "storage.provider": feature.storage_capability,
        },
        commands={
            "magnet": feature.command,
            "m": feature.command,
            "auth": feature.command,
            "config": feature.command,
            "q": feature.command,
        },
        callbacks={"open115": feature.callback},
        messages=feature.message,
    )
    feature.bind_runtime(runtime)
    return runtime
