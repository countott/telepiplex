from __future__ import annotations

import yaml

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext

from .context import runtime_context
from .service import MediaSearchFeature


def main(context: RuntimeContext) -> FeatureRuntime:
    config = yaml.safe_load(context.config_path.read_text(encoding="utf-8")) or {}
    runtime_context.configure(config)
    feature = MediaSearchFeature(config=config, core=context.core)
    return FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        capabilities={"media.search": feature.metadata_capability},
        commands={"search": feature.command, "s": feature.command},
        callbacks={"media-search": feature.callback},
        messages=feature.message,
    )
