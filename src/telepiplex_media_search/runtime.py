from __future__ import annotations

import yaml

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext

from .context import runtime_context
from .entity_registry import CanonicalEntityRegistry
from .service import MediaSearchFeature


def main(context: RuntimeContext) -> FeatureRuntime:
    config = yaml.safe_load(context.config_path.read_text(encoding="utf-8")) or {}
    runtime_context.configure(config)
    registry = CanonicalEntityRegistry(context.state_path / "media_entities.db")
    feature = MediaSearchFeature(config=config, core=context.core, registry=registry)
    runtime = FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        capabilities={"media.search": feature.metadata_capability},
        commands={
            "search": feature.command,
            "s": feature.command,
            "media_search_config": feature.command,
        },
        callbacks={"media-search": feature.callback},
        messages=feature.message,
        operation_control=feature.operation_control,
        operation_snapshot=feature.operation_snapshot,
    )
    feature.bind_runtime(runtime)
    return runtime
