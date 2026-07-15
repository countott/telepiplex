from __future__ import annotations

import yaml

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext

from .context import runtime_context
from .service import RenamingFeature
from .jobs import RenamingJobStore


def main(context: RuntimeContext) -> FeatureRuntime:
    config = yaml.safe_load(context.config_path.read_text(encoding="utf-8")) or {}
    runtime_context.configure({
        "ai": config.get("ai") or {},
        "metadata": config.get("metadata") or {},
        "media": {"unorganized_path": config.get("unorganized_path") or ""},
        "selection": config.get("selection") or {},
    })
    feature = RenamingFeature(
        config=config, core=context.core,
        jobs=RenamingJobStore(context.state_path / "renaming.db"),
    )
    runtime = FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        events={"download.completed": feature.download_completed},
        commands={"renaming_config": feature.command},
        callbacks={"renaming": feature.callback},
        messages=feature.message,
        operation_control=feature.operation_control,
        operation_snapshot=feature.operation_snapshot,
    )
    feature.bind_runtime(runtime)
    return runtime
