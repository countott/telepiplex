from __future__ import annotations

import yaml

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext

from .service import CaptionFeature


def main(context: RuntimeContext) -> FeatureRuntime:
    config = yaml.safe_load(context.config_path.read_text(encoding="utf-8")) or {}
    feature = CaptionFeature(config=config, host=context.host, state_path=context.state_path)
    runtime = FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        capabilities={"subtitle.caption": feature.capability},
        commands={"caption": feature.command, "caption_config": feature.command},
        callbacks={"caption": feature.callback},
        messages=feature.message,
        config_validator=feature.validate_config,
        operation_control=feature.operation_control,
        operation_snapshot=feature.operation_snapshot,
    )
    feature.bind_runtime(runtime)
    return runtime
