from __future__ import annotations

import yaml

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext

from .feature import SyncFeature


def main(context: RuntimeContext) -> FeatureRuntime:
    config = yaml.safe_load(context.config_path.read_text(encoding="utf-8")) or {}
    feature = SyncFeature(
        config=config,
        host=context.host,
        state_path=context.state_path,
    )
    runtime = FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        capabilities={"library.sync": feature.management_capability},
        events={"media.organized": feature.media_organized},
        commands={
            "sync": feature.command,
            "scan": feature.command,
            "sync_config": feature.command,
        },
        callbacks={"sync": feature.callback},
        messages=feature.message,
        operation_control=feature.operation_control,
        operation_snapshot=feature.operation_snapshot,
    )
    feature.bind_runtime(runtime)
    return runtime
