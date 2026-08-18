import json
from unittest.mock import patch

from telepiplex_rename import ai as ai_module
from telepiplex_rename.ai import (
    chat_completion,
    get_movie_tmdb_name_with_ai,
    infer_tvdb_episode_plan_with_ai,
    parse_ai_json_response,
    request_structured_json,
)
from telepiplex_rename.context import runtime_context


class _Response:
    status_code = 200
    text = "ok"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _RecordingLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(str(message))

    def warning(self, message):
        self.messages.append(str(message))

    warn = warning

    def error(self, message):
        self.messages.append(str(message))


def _configure(*, model="deepseek-reasoner", provider="deepseek", url=None):
    runtime_context.configure({
        "ai": {
            "enable": True,
            "api_url": url or "https://api.deepseek.com/v1",
            "api_key": "secret",
            "model": model,
            "provider": provider,
            "timeout": 1,
        },
    })


def _openai_result(content, *, finish_reason="stop", reasoning=""):
    return {
        "choices": [{
            "finish_reason": finish_reason,
            "message": {
                "content": content,
                "reasoning_content": reasoning,
            },
        }],
        "usage": {
            "completion_tokens": 100,
            "completion_tokens_details": {"reasoning_tokens": 80},
        },
    }


@patch("telepiplex_rename.ai.requests.post")
def test_deepseek_request_enables_thinking_and_json_final_output(post):
    _configure()
    post.return_value = _Response(_openai_result('{"status":"ok"}'))

    result = chat_completion("只返回 JSON", max_tokens=16384)

    assert result["choices"][0]["message"]["content"] == '{"status":"ok"}'
    payload = post.call_args.kwargs["json"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["max_tokens"] == 16384


@patch("telepiplex_rename.ai.requests.post")
def test_anthropic_messages_endpoint_does_not_receive_openai_only_fields(post):
    _configure(
        model="claude-test",
        provider="anthropic",
        url="https://anthropic.example/v1/messages",
    )
    post.return_value = _Response({
        "content": [{"type": "text", "text": '{"status":"ok"}'}],
        "stop_reason": "end_turn",
    })

    chat_completion("只返回 JSON", max_tokens=2048)

    payload = post.call_args.kwargs["json"]
    assert "response_format" not in payload
    assert "thinking" not in payload


def test_parser_uses_final_content_and_never_reasoning_content():
    result = _openai_result(
        '{"status":"ok","answer":"final"}',
        reasoning='{"status":"ok","answer":"reasoning"}',
    )

    assert parse_ai_json_response(result) == {
        "status": "ok",
        "answer": "final",
    }
    assert parse_ai_json_response(_openai_result(
        "",
        reasoning='{"status":"ok","answer":"reasoning-only"}',
    )) is None


@patch("telepiplex_rename.ai.chat_completion")
def test_44_file_deepseek_mapping_gets_reasoning_plus_answer_budget(chat):
    _configure()
    chat.return_value = _openai_result(json.dumps({
        "episode_map": [],
        "subtitle_map": [],
        "warnings": [],
    }))
    context = {
        "file_tree": [
            {"relative_path": f"Show.S01E{index:02d}.mkv", "is_dir": False}
            for index in range(1, 45)
        ],
    }

    plan = infer_tvdb_episode_plan_with_ai(context)

    assert isinstance(plan, dict)
    assert chat.call_count == 1
    assert chat.call_args.kwargs["max_tokens"] == 16384


@patch("telepiplex_rename.ai.chat_completion")
def test_length_response_retries_once_with_doubled_bounded_budget(chat):
    _configure()
    chat.side_effect = [
        _openai_result('{"partial":true}', finish_reason="length"),
        _openai_result('{"status":"ok"}'),
    ]

    result = request_structured_json(
        "只返回 JSON\n" + ("facts " * 100),
        max_tokens=16384,
        task="test",
    )

    assert result.status == "ok"
    assert result.value == {"status": "ok"}
    assert result.attempts == 2
    assert [call.kwargs["max_tokens"] for call in chat.call_args_list] == [
        16384,
        32768,
    ]


@patch("telepiplex_rename.ai.chat_completion")
def test_empty_or_invalid_final_content_retries_once_then_is_typed_failure(chat):
    _configure()
    chat.side_effect = [
        _openai_result("", reasoning="SECRET_REASONING_SENTINEL"),
        _openai_result("not-json", reasoning="SECRET_REASONING_SENTINEL"),
    ]

    result = request_structured_json(
        "只返回 JSON",
        max_tokens=16384,
        task="test",
    )

    assert result.status == "ai_output_unavailable"
    assert result.value is None
    assert result.attempts == 2
    assert chat.call_count == 2


@patch("telepiplex_rename.ai.chat_completion")
def test_reasoning_text_is_never_written_to_logs(chat):
    _configure()
    chat.return_value = _openai_result(
        '{"name":"Movie"}',
        reasoning="SECRET_REASONING_SENTINEL",
    )
    original_logger = runtime_context.logger
    logger = _RecordingLogger()
    runtime_context.logger = logger
    try:
        name = get_movie_tmdb_name_with_ai("Movie.2024")
    finally:
        runtime_context.logger = original_logger

    assert name == "Movie"
    assert "SECRET_REASONING_SENTINEL" not in "\n".join(logger.messages)


@patch("telepiplex_rename.ai.chat_completion")
def test_ambiguity_explanation_accepts_advice_but_discards_mapping_fields(chat):
    explanation_fn = getattr(
        ai_module,
        "explain_unresolved_episode_files_with_ai",
        None,
    )
    assert callable(explanation_fn)

    _configure()
    chat.return_value = _openai_result(json.dumps({
        "summary": "文件编号可能采用不同分集顺序。",
        "possible_causes": ["DVD 顺序", "自定义打包顺序"],
        "user_checks": ["核对发行说明", "核对 TVDB alternate order"],
        "episode_map": [{
            "source_file": "Honey.S01E25.mkv",
            "season_number": 0,
            "episode_number": 1,
        }],
        "target_path": "/Series/Unsafe.mkv",
    }))

    explanation = explanation_fn({
        "confirmed_work": {"english_title": "Honey and Clover"},
        "unresolved_files": [{
            "source_name": "Honey.S01E25.mkv",
            "reason_codes": ["target_unresolved"],
        }],
    })

    assert explanation == {
        "source": "ai",
        "summary": "文件编号可能采用不同分集顺序。",
        "possible_causes": ["DVD 顺序", "自定义打包顺序"],
        "user_checks": ["核对发行说明", "核对 TVDB alternate order"],
    }
    assert "episode_map" not in explanation
    assert "target_path" not in explanation
    assert chat.call_count == 1
