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
            "host_version": "v3.5.1-host",
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


def test_human_renderer_is_a_compact_business_timeline_without_machine_metadata():
    from telepiplex_plugin_sdk.diagnostics import render_human_event

    event = _fixed_event()
    event["time"]["local"] = "2026-08-14T23:15:42.381000+08:00"
    event["time"]["timezone"] = "CST"
    output = render_human_event(event)

    for expected in (
        "[2026-08-14 23:15:42]",
        "搜索来源查询已完成",
        "组件 search｜级别 INFO｜记录器 telepiplex.feature.search",
        "TVDB 搜索完成",
        "source_resolution",
        "matched",
        "621 ms",
        "蜂蜜与四叶草",
        "tvdb",
        "79044",
        "38",
        "回复内容：已找到 38 条候选",
    ):
        assert expected in output
    for machine_only in (
        ".381",
        "UTC",
        "时区",
        "运行位置",
        "关联信息",
        "事件顺序",
        "诊断时钟",
        "session-a83f2c",
        "trace-8f31d2",
        "operation-019",
        "request-c316",
        "event-004218",
        "search@1.9.7-a1b2c3d4",
        "Unix纳秒",
        "单调纳秒",
    ):
        assert machine_only not in output


def test_human_renderer_labels_real_incoming_commands_and_callbacks():
    from telepiplex_plugin_sdk.diagnostics import render_human_event

    command = _fixed_event(
        event_name="telegram.interaction.received",
        message="收到 Telegram 交互",
        fields={
            "user_surface": {
                "direction": "incoming",
                "kind": "command",
                "text": "/search 蜂蜜与四叶草",
            },
        },
    )
    callback = _fixed_event(
        event_name="telegram.interaction.received",
        message="收到 Telegram 交互",
        fields={
            "user_surface": {
                "direction": "incoming",
                "kind": "callback",
                "callback_data": "search:select:p1:0",
            },
        },
    )

    assert "收到指令：/search 蜂蜜与四叶草" in render_human_event(command)
    assert "收到回调：search:select:p1:0" in render_human_event(callback)


def test_human_renderer_shows_only_the_confirmed_api_delivery_copy():
    from telepiplex_plugin_sdk.diagnostics import render_human_event

    delivery_contract = _fixed_event(
        event_name="telegram.feature_action.delivered",
        message="Feature 前台消息已送达",
        fields={
            "user_surface": {
                "direction": "outgoing",
                "action": "send_message",
                "text": "完整前台回复",
            },
        },
    )
    confirmed_delivery = _fixed_event(
        event_name="telegram.api.delivered",
        message="Telegram API 内容已送达",
        fields={
            "user_surface": {
                "direction": "outgoing",
                "action": "send_message",
                "text": "完整前台回复",
            },
        },
    )

    assert render_human_event(delivery_contract) == ""
    assert "回复内容：完整前台回复" in render_human_event(confirmed_delivery)


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
