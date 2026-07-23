from __future__ import annotations

from types import SimpleNamespace

import yaml

from telepiplex_caption.runtime import main
from telepiplex_plugin_sdk import FeatureRuntime


def test_placeholder_starts_without_exposing_unimplemented_features():
    manifest = yaml.safe_load(
        open("manifest.yaml", encoding="utf-8")
    )

    runtime = main(SimpleNamespace(manifest=manifest, token="test-token"))

    assert isinstance(runtime, FeatureRuntime)
    assert runtime.capabilities == {}
    assert runtime.events == {}
    assert runtime.commands == {}
    assert runtime.callbacks == {}
    assert runtime.messages is None
