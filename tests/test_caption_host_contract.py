"""Caption's Host integration must remain optional and keep payloads out of logs."""
from pathlib import Path
import json

import yaml

from app.runtime.operation_segments import validate_segment_declaration
from app.runtime.plugin_manifest import PluginManifest
from app.utils.log_sanitizer import sanitize_log_value
from telepiplex_plugin_sdk.diagnostics import sanitize_diagnostic_value


ROOT = Path(__file__).resolve().parents[1]


def test_current_manifests_form_installable_caption_dependency_chain():
    from app.runtime.capability_router import CapabilityRouter

    router = CapabilityRouter()
    for name in ("download", "search", "rename", "caption"):
        manifest = PluginManifest.from_mapping(yaml.safe_load((ROOT / "features" / name / "manifest.yaml").read_text()))
        assert manifest.supports_host("1.9")
        router.activate(name, manifest, object())
    assert not router.snapshot.blocked
    router.deactivate("caption")
    assert not router.snapshot.blocked
    assert "media.rename" in router.snapshot.capabilities
    assert validate_segment_declaration({"role": "caption", "presentation_kind": "text"}) == ("caption", "text")


def test_subtitle_payload_and_credentials_are_redacted_before_logging():
    payload = {"payload": {"chunk_base64": "PRIVATE_SUBTITLE_CONTENT", "content_base64": "PRIVATE_SUBTITLE_CONTENT",
                           "normalized_text": "PRIVATE_SUBTITLE_CONTENT", "api_key": "PRIVATE_API_KEY", "chunk_index": 2}}
    assert "PRIVATE_SUBTITLE_CONTENT" not in sanitize_log_value(payload)
    assert "PRIVATE_API_KEY" not in sanitize_log_value(payload)
    sanitized, paths = sanitize_diagnostic_value(payload)
    diagnostic = json.dumps(sanitized, ensure_ascii=False)
    assert "PRIVATE_SUBTITLE_CONTENT" not in diagnostic
    assert "PRIVATE_API_KEY" not in diagnostic
    assert "chunk_index" in diagnostic
    assert "/payload/chunk_base64" in paths


def test_caption_config_defaults_validate_and_legacy_empty_config_remains_valid():
    import jsonschema

    root = ROOT / "features/caption"
    schema = json.loads((root / "config.schema.json").read_text())
    config = yaml.safe_load((root / "config.default.yaml").read_text())
    jsonschema.validate(config, schema)
    jsonschema.validate({}, schema)
    assert config["automatic"] is True
    assert config["providers"]["assrt"]["token"] == ""
