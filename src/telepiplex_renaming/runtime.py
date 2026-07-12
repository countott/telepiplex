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
    })
    feature = RenamingFeature(
        config=config, core=context.core,
        jobs=RenamingJobStore(context.state_path / "renaming.db"),
    )
    return FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        events={"download.completed": feature.download_completed},
    )
