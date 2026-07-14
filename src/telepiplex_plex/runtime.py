from __future__ import annotations

import yaml

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext

from .feature import PlexFeature


def main(context: RuntimeContext) -> FeatureRuntime:
    config = yaml.safe_load(context.config_path.read_text(encoding="utf-8")) or {}
    feature = PlexFeature(
        config=config,
        core=context.core,
        state_path=context.state_path,
    )
    runtime = FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        capabilities={"plex.management": feature.management_capability},
        events={"media.organized": feature.media_organized},
        commands={"plex": feature.command, "plex_config": feature.command},
        callbacks={"plex": feature.callback},
        messages=feature.message,
    )
    feature.bind_runtime(runtime)
    return runtime
