from __future__ import annotations

from types import SimpleNamespace

import yaml

from telepiplex_caption.runtime import main
from telepiplex_plugin_sdk import FeatureRuntime


def test_caption_starts_with_external_subtitle_workflows(tmp_path):
    manifest = yaml.safe_load(
        open("manifest.yaml", encoding="utf-8")
    )

    config = tmp_path / "config.yaml"
    config.write_text("{}")
    runtime = main(SimpleNamespace(manifest=manifest, token="test-token", config_path=config, state_path=tmp_path, host=object()))

    assert isinstance(runtime, FeatureRuntime)
    assert set(runtime.capabilities) == {"subtitle.caption"}
    assert runtime.events == {}
    assert set(runtime.commands) == {"caption", "caption_config"}
    assert set(runtime.callbacks) == {"caption"}
    assert runtime.messages is not None
