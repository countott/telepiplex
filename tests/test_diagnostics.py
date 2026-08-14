from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "sdk"
    / "src"
    / "telepiplex_plugin_sdk"
    / "diagnostic-event-v1.schema.json"
)


def _fixed_event(**overrides):
    from telepiplex_plugin_sdk.diagnostics import build_diagnostic_event

    values = {
        "level": "INFO",
        "event_name": "search.source.completed",
        "message": "TVDB 搜索完成",
        "session_id": "session-a83f2c",
        "logger_name": "telepiplex.feature.search",
        "component": "search",
        "context": {
            "trace_id": "trace-8f31d2",
            "span_id": "span-b91a70",
            "parent_span_id": "span-parent",
            "operation_id": "operation-019",
            "request_id": "request-c316",
            "incident_id": None,
        },
        "fields": {
            "stage": "source_resolution",
            "status": "matched",
            "duration_ms": 621,
            "input": {"title": "蜂蜜与四叶草", "source": "tvdb"},
            "output": {"tvdb_id": 79044, "inventory_count": 38},
            "user_surface": {
                "action": "edit_message",
                "text": "已找到 38 条候选",
            },
        },
        "runtime": {
            "host_version": "v3.5.0-host",
            "plugin_id": "search",
            "plugin_version": "1.9.7",
            "instance_id": "search@1.9.7-a1b2c3d4",
            "pid": 42,
        },
        "event_id": "event-004218",
        "sequence": 7,
        "ingest_sequence": 11,
        "timestamp": datetime(2026, 8, 14, 15, 15, 42, 381000, tzinfo=timezone.utc),
        "monotonic_ns": 123456789,
    }
    values.update(overrides)
    return build_diagnostic_event(**values)


def test_machine_event_has_a_stable_schema_and_complete_typed_facts():
    event = _fixed_event()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    jsonschema.validate(event, schema)
    assert event["schema_version"] == "1.0"
    assert event["identity"] == {
        "session_id": "session-a83f2c",
        "trace_id": "trace-8f31d2",
        "span_id": "span-b91a70",
        "parent_span_id": "span-parent",
        "operation_id": "operation-019",
        "request_id": "request-c316",
        "incident_id": None,
    }
    assert event["event"]["name"] == "search.source.completed"
    assert event["event"]["stage"] == "source_resolution"
    assert event["event"]["status"] == "matched"
    assert event["event"]["duration_ms"] == 621
    assert event["facts"]["input"]["title"] == "蜂蜜与四叶草"
    assert event["facts"]["output"] == {"tvdb_id": 79044, "inventory_count": 38}
    assert event["runtime"]["plugin_version"] == "1.9.7"
    assert event["sequence"] == {"producer": 7, "ingest": 11}
    assert event["time"]["utc"] == "2026-08-14T15:15:42.381000+00:00"
    assert event["time"]["unix_ns"] == 1786720542381000000
    assert event["time"]["monotonic_ns"] == 123456789


def test_human_renderer_preserves_every_populated_diagnostic_fact_without_json_noise():
    from telepiplex_plugin_sdk.diagnostics import render_human_event

    output = render_human_event(_fixed_event())

    for expected in (
        "搜索来源查询已完成",
        "级别：INFO",
        "TVDB 搜索完成",
        "search",
        "source_resolution",
        "matched",
        "621 ms",
        "蜂蜜与四叶草",
        "tvdb",
        "79044",
        "38",
        "已找到 38 条候选",
        "trace-8f31d2",
        "operation-019",
        "request-c316",
        "event-004218",
        "search@1.9.7-a1b2c3d4",
    ):
        assert expected in output
    assert '"schema_version"' not in output
    assert "trace_id=" not in output


def test_nested_secrets_and_exception_stack_are_redacted_before_rendering():
    from telepiplex_plugin_sdk.diagnostics import render_human_event, render_machine_event

    try:
        raise RuntimeError(
            "request failed access_token=secret-token https://private.example/item"
        )
    except RuntimeError as exc:
        event = _fixed_event(
            level="ERROR",
            event_name="search.source.failed",
            fields={
                "stage": "source_resolution",
                "status": "failed",
                "input": {
                    "headers": {"Authorization": "Bearer very-secret"},
                    "api_key": "abc123",
                    "title": "蜂蜜与四叶草",
                },
                "output": {},
            },
            error=exc,
        )

    serialized = render_machine_event(event)
    human = render_human_event(event)
    for secret in (
        "secret-token",
        "private.example",
        "very-secret",
        "abc123",
    ):
        assert secret not in serialized
        assert secret not in human
    assert "***redacted***" in serialized
    assert "***redacted***" in human
    assert event["error"]["type"] == "RuntimeError"
    assert "Traceback" in event["error"]["stack"]
    assert event["privacy"]["redaction_count"] >= 4
    assert "/facts/input/headers/Authorization" in event["privacy"]["redacted_paths"]
    assert "/facts/input/api_key" in event["privacy"]["redacted_paths"]


def test_diagnostic_context_is_scoped_and_restored():
    from telepiplex_plugin_sdk.diagnostics import (
        bind_diagnostic_context,
        current_diagnostic_context,
    )

    assert current_diagnostic_context()["trace_id"] is None
    with bind_diagnostic_context(trace_id="outer", operation_id="operation-1"):
        assert current_diagnostic_context()["trace_id"] == "outer"
        with bind_diagnostic_context(span_id="child", request_id="request-1"):
            current = current_diagnostic_context()
            assert current["trace_id"] == "outer"
            assert current["operation_id"] == "operation-1"
            assert current["span_id"] == "child"
            assert current["request_id"] == "request-1"
        assert current_diagnostic_context()["span_id"] is None
    assert current_diagnostic_context()["trace_id"] is None


def test_oversized_sanitized_payload_is_losslessly_reconstructable_from_ordered_chunks():
    from telepiplex_plugin_sdk.diagnostics import payload_chunks

    raw = "A" * 10 + " access_token=secret-value " + "B" * 13
    chunks = payload_chunks(raw, payload_ref="PAYLOAD-1", chunk_chars=8)

    assert [chunk["index"] for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk["count"] == len(chunks) for chunk in chunks)
    assert all(chunk["payload_ref"] == "PAYLOAD-1" for chunk in chunks)
    reconstructed = "".join(chunk["content"] for chunk in chunks)
    assert reconstructed == "A" * 10 + " access_token=***redacted*** " + "B" * 13
    assert "secret-value" not in reconstructed
    assert all(chunk["sanitized_length"] == len(reconstructed) for chunk in chunks)
    assert len({chunk["sha256"] for chunk in chunks}) == 1
