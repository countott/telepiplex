from __future__ import annotations

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext


def main(context: RuntimeContext) -> FeatureRuntime:
    return FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
    )
