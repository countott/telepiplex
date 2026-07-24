"""Request-scoped AI source orchestration with hard tool budgets."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass

from .ai import (
    chat_completion_messages,
    check_ai_api_available,
    extract_ai_message,
)
from .context import runtime_context
from .entity_graph import SearchGraph, build_search_graph
from .evidence_verifier import (
    EvidenceVerificationError,
    VerifiedAiDecision,
    validate_orchestrator_output,
)
from .source_tools import (
    FIRST_ROUND_TOOL,
    TARGETED_TOOLS,
    SourceToolGateway,
    ToolValidationError,
)


SOURCE_ORCHESTRATOR_SYSTEM_PROMPT = """你是媒体来源查询编排器。
首个动作必须调用 search_media_sources。
你只能根据本次工具返回的 fact_id、来源字段和当前请求图判断。
你可以提出标题纠错、简称、跨语言别名和同实体关联假设。
不得凭自身知识生成稳定 ID、官方标题、年份、海报、TVDB inventory、media_metadata 或 Prowlarr query。
如果 raw query 只有单集标题，先把可能的父剧集名称作为待验证查询假设；
父剧集取得 TVDB Series ID 后必须调用 lookup_tvdb_episodes 获取 TVDB episode inventory，
最终季集编号只能来自该 inventory，不能凭模型记忆填写。
不得请求任意 URL、Header、API Key、Token、Cookie 或 Base URL。
存在多个合格候选时不得自动选择。
证据充分、达到查询上限或继续查询不会增加可验证信息时必须停止。
最终只能返回规定 JSON。"""


@dataclass(frozen=True)
class OrchestrationOutcome:
    status: str
    intent: dict
    sources: tuple[dict, ...]
    decision: VerifiedAiDecision | None
    targeted_rounds: int
    fallback_reason: str = ""


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _config(value: dict | None) -> dict:
    if value is not None:
        config = dict(value)
    else:
        ai = ((runtime_context.config or {}).get("ai") or {})
        config = dict(ai.get("source_orchestration") or {})
    thinking_mode = _text(
        config.get("thinking_mode") or "enabled"
    ).casefold()
    if thinking_mode not in {"enabled", "disabled"}:
        thinking_mode = "enabled"
    tool_choice_mode = _text(
        config.get("tool_choice_mode") or "omit"
    ).casefold()
    if tool_choice_mode not in {"omit", "forced"}:
        tool_choice_mode = "omit"
    return {
        "enable": bool(config.get("enable", True)),
        "protocol": _text(
            config.get("protocol") or "openai_tools_v1"
        ),
        "thinking_mode": thinking_mode,
        "tool_choice_mode": tool_choice_mode,
        "max_targeted_rounds": min(
            2,
            max(0, int(config.get("max_targeted_rounds", 2))),
        ),
        "max_tools_per_round": min(
            3,
            max(1, int(config.get("max_tools_per_round", 3))),
        ),
    }


def _fallback(
    reason: str,
    *,
    intent: dict | None = None,
    sources: list[dict] | tuple[dict, ...] = (),
    targeted_rounds: int = 0,
) -> OrchestrationOutcome:
    return OrchestrationOutcome(
        "fallback",
        dict(intent or {}),
        tuple(sources or ()),
        None,
        targeted_rounds,
        reason,
    )


async def _call_ai(ai_call, messages, **kwargs):
    if inspect.iscoroutinefunction(ai_call):
        return await ai_call(messages, **kwargs)
    return await asyncio.to_thread(ai_call, messages, **kwargs)


def _provider_error(result) -> dict:
    if not isinstance(result, dict):
        return {}
    error = result.get("error")
    return dict(error) if isinstance(error, dict) else {}


def _thinking_tool_choice_error(result) -> bool:
    error = _provider_error(result)
    param = _text(error.get("param")).casefold()
    message = _text(error.get("message")).casefold()
    return bool(
        param in {"", "tool_choice"}
        and "thinking" in message
        and "tool_choice" in message
    )


def _provider_fallback_reason(result) -> str:
    error = _provider_error(result)
    if not error:
        return ""
    if _thinking_tool_choice_error(result):
        return "thinking_tool_choice_unsupported"
    if _unsupported_result(result):
        return "tooling_unsupported"
    return _text(error.get("kind")).casefold() or "provider_error"


async def _call_ai_with_compat(
    ai_call,
    messages,
    *,
    tools,
    tool_choice,
    thinking_mode: str,
    max_tokens: int,
):
    kwargs = {
        "tools": tools,
        "thinking_mode": thinking_mode,
        "max_tokens": max_tokens,
    }
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    result = await _call_ai(ai_call, messages, **kwargs)
    if tool_choice is None or not _thinking_tool_choice_error(result):
        return result
    if runtime_context.logger:
        runtime_context.logger.info(
            "source_orchestration "
            "compat_retry=omit_tool_choice "
            "reason=thinking_tool_choice_unsupported"
        )
    kwargs.pop("tool_choice", None)
    return await _call_ai(ai_call, messages, **kwargs)


def _assistant_message(result) -> dict | None:
    if isinstance(result, dict) and result.get("role") == "assistant":
        return dict(result)
    return extract_ai_message(result)


def _assistant_history_message(message: dict) -> dict:
    result = dict(message or {})
    if _tool_calls(result) and result.get("content") is None:
        result["content"] = ""
    return result


def _unsupported_result(result) -> bool:
    if not isinstance(result, dict):
        return False
    error = result.get("error")
    if not isinstance(error, dict):
        return False
    code = _text(error.get("code")).casefold()
    message = _text(error.get("message")).casefold()
    return any(
        signal in f"{code} {message}"
        for signal in ("tool", "function", "unsupported")
    )


def _tool_calls(message: dict | None) -> list[dict]:
    if not isinstance(message, dict):
        return []
    calls = message.get("tool_calls")
    return list(calls) if isinstance(calls, list) else []


def _parsed_tool_call(call: dict) -> tuple[str, str, dict]:
    if not isinstance(call, dict):
        raise ToolValidationError("tool_protocol_invalid")
    call_id = _text(call.get("id"))
    function = call.get("function")
    if not call_id or not isinstance(function, dict):
        raise ToolValidationError("tool_protocol_invalid")
    name = _text(function.get("name"))
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ToolValidationError("tool_arguments_invalid")
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ToolValidationError("tool_arguments_invalid") from exc
    if not isinstance(parsed, dict):
        raise ToolValidationError("tool_arguments_invalid")
    return call_id, name, parsed


def _tool_message(call_id: str, name: str, result: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def _merge_sources(existing: list[dict], incoming: list[dict]) -> list[dict]:
    by_source: dict[str, dict] = {}
    order = []
    for item in [*(existing or []), *(incoming or [])]:
        if not isinstance(item, dict):
            continue
        source = _text(item.get("source")).casefold()
        if not source:
            continue
        if source not in by_source:
            order.append(source)
            by_source[source] = {
                "source": source,
                "status": "not_found",
                "query_summaries": [],
                "facts": [],
                "source_urls": [],
                "error_code": "",
                "credential_state": item.get("credential_state")
                or "not_required",
            }
        target = by_source[source]
        status = _text(item.get("status")).casefold() or "server_down"
        if status == "ok" or target["status"] != "ok":
            target["status"] = status
        for field in ("query_summaries", "facts", "source_urls"):
            values = item.get(field)
            if not isinstance(values, list):
                continue
            known = {
                json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                for value in target[field]
            }
            for value in values:
                key = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                if key not in known:
                    target[field].append(value)
                    known.add(key)
        if item.get("error_code"):
            target["error_code"] = _text(item["error_code"])
        if item.get("credential_state"):
            target["credential_state"] = item["credential_state"]
    return [by_source[source] for source in order]


def _fact_payload(fact) -> dict:
    return {
        "fact_id": fact.fact_id,
        "provider": fact.provider,
        "titles": list(fact.titles),
        "year": fact.year,
        "media_type": fact.media_type,
        "external_ids": dict(fact.external_ids),
        "source_url": fact.source_url,
        "poster_url": fact.poster_url,
        "original_language": fact.original_language,
        "official_english_title": fact.official_english_title,
        "romanized_original_title": fact.romanized_original_title,
        "relation_signals": list(fact.complex_signals),
    }


def _graph_payload(graph: SearchGraph) -> dict:
    return {
        "candidates": [{
            "candidate_key": candidate.candidate_key,
            "facts": [_fact_payload(fact) for fact in candidate.facts],
        } for candidate in graph.candidates],
    }


def _known_facts(graph: SearchGraph) -> dict:
    return {
        fact.fact_id: _fact_payload(fact)
        for candidate in graph.candidates
        for fact in candidate.facts
    }


def _parse_final_content(message: dict) -> dict | None:
    content = message.get("content")
    if not isinstance(content, str):
        return None
    content = content.strip()
    if content.startswith("```"):
        content = content.replace("```json", "", 1).replace("```", "").strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _source_family(tool_name: str) -> str:
    if "wikipedia" in tool_name:
        return "wikipedia"
    if "douban" in tool_name:
        return "douban"
    if "tvdb" in tool_name:
        return "tvdb"
    return ""


async def orchestrate_sources(
    raw_query: str,
    gateway: SourceToolGateway,
    *,
    ai_call=chat_completion_messages,
    config: dict | None = None,
) -> OrchestrationOutcome:
    """Run one mandatory first source round and at most two targeted rounds."""

    settings = _config(config)
    if not settings["enable"]:
        return _fallback("ai_orchestration_disabled")
    if settings["protocol"] != "openai_tools_v1":
        return _fallback("tooling_unsupported")
    if ai_call is chat_completion_messages and not check_ai_api_available():
        return _fallback("ai_unavailable")

    messages = [
        {"role": "system", "content": SOURCE_ORCHESTRATOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "raw_query": _text(raw_query),
                    "required_first_tool": "search_media_sources",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]
    forced_choice = {
        "type": "function",
        "function": {"name": "search_media_sources"},
    }
    first_choice = (
        forced_choice
        if settings["tool_choice_mode"] == "forced"
        else None
    )
    first_result = None
    first_message = None
    first_call = None
    for attempt in range(2):
        result = await _call_ai_with_compat(
            ai_call,
            messages,
            tools=[FIRST_ROUND_TOOL],
            tool_choice=first_choice,
            thinking_mode=settings["thinking_mode"],
            max_tokens=3000,
        )
        if result is None:
            return _fallback("ai_unavailable")
        provider_reason = _provider_fallback_reason(result)
        if provider_reason:
            return _fallback(provider_reason)
        message = _assistant_message(result)
        calls = _tool_calls(message)
        try:
            if len(calls) != 1:
                raise ToolValidationError("tool_protocol_invalid")
            call_id, name, arguments = _parsed_tool_call(calls[0])
            if name != "search_media_sources":
                raise ToolValidationError("tool_protocol_invalid")
            first_result = await gateway.search_media_sources(
                raw_query,
                arguments,
            )
        except ToolValidationError:
            if attempt == 0:
                if message:
                    messages.append(_assistant_history_message(message))
                messages.append({
                    "role": "user",
                    "content": (
                        "protocol_correction: 首个动作只能调用 "
                        "search_media_sources，并且参数必须符合 Schema。"
                    ),
                })
                continue
            return _fallback("tool_protocol_invalid")
        first_message = message
        first_call = (call_id, name)
        break
    if first_result is None or first_message is None or first_call is None:
        return _fallback("tool_protocol_invalid")

    messages.append(_assistant_history_message(first_message))
    messages.append(_tool_message(
        first_call[0],
        first_call[1],
        first_result,
    ))
    sources = _merge_sources([], list(first_result.get("sources") or []))
    intent = dict(first_result.get("intent") or {})
    targeted_rounds = 0

    while True:
        graph = build_search_graph(sources)
        messages.append({
            "role": "user",
            "content": json.dumps(
                {
                    "current_request_graph": _graph_payload(graph),
                    "targeted_rounds_used": targeted_rounds,
                    "targeted_rounds_remaining": (
                        settings["max_targeted_rounds"] - targeted_rounds
                    ),
                    "instruction": (
                        "需要更多证据时调用受控工具，否则返回最终 JSON。"
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        })
        targeted_choice = (
            "auto"
            if settings["tool_choice_mode"] == "forced"
            else None
        )
        result = await _call_ai_with_compat(
            ai_call,
            messages,
            tools=TARGETED_TOOLS,
            tool_choice=targeted_choice,
            thinking_mode=settings["thinking_mode"],
            max_tokens=4000,
        )
        if result is None:
            return _fallback(
                "ai_unavailable",
                intent=intent,
                sources=sources,
                targeted_rounds=targeted_rounds,
            )
        provider_reason = _provider_fallback_reason(result)
        if provider_reason:
            return _fallback(
                provider_reason,
                intent=intent,
                sources=sources,
                targeted_rounds=targeted_rounds,
            )
        message = _assistant_message(result)
        if message is None:
            return _fallback(
                "tool_protocol_invalid",
                intent=intent,
                sources=sources,
                targeted_rounds=targeted_rounds,
            )
        calls = _tool_calls(message)
        if not calls:
            payload = _parse_final_content(message)
            if payload is None:
                return _fallback(
                    "tool_protocol_invalid",
                    intent=intent,
                    sources=sources,
                    targeted_rounds=targeted_rounds,
                )
            try:
                decision = validate_orchestrator_output(payload, graph)
            except EvidenceVerificationError as exc:
                return _fallback(
                    exc.code,
                    intent=intent,
                    sources=sources,
                    targeted_rounds=targeted_rounds,
                )
            return OrchestrationOutcome(
                decision.status,
                dict(decision.intent),
                tuple(sources),
                decision,
                targeted_rounds,
                "",
            )
        if targeted_rounds >= settings["max_targeted_rounds"]:
            return _fallback(
                "tool_budget_exceeded",
                intent=intent,
                sources=sources,
                targeted_rounds=targeted_rounds,
            )
        if len(calls) > settings["max_tools_per_round"]:
            return _fallback(
                "tool_budget_exceeded",
                intent=intent,
                sources=sources,
                targeted_rounds=targeted_rounds,
            )
        parsed_calls = []
        families = []
        try:
            for call in calls:
                call_id, name, arguments = _parsed_tool_call(call)
                family = _source_family(name)
                if not family or family in families:
                    raise ToolValidationError("tool_budget_exceeded")
                families.append(family)
                parsed_calls.append((call_id, name, arguments))
        except ToolValidationError as exc:
            return _fallback(
                exc.code,
                intent=intent,
                sources=sources,
                targeted_rounds=targeted_rounds,
            )
        messages.append(_assistant_history_message(message))
        known_facts = _known_facts(graph)
        try:
            tool_results = await asyncio.gather(*[
                gateway.execute_targeted(
                    name,
                    arguments,
                    known_facts=known_facts,
                )
                for _call_id, name, arguments in parsed_calls
            ])
        except ToolValidationError as exc:
            return _fallback(
                exc.code,
                intent=intent,
                sources=sources,
                targeted_rounds=targeted_rounds,
            )
        for (call_id, name, _arguments), tool_result in zip(
            parsed_calls,
            tool_results,
        ):
            messages.append(_tool_message(call_id, name, tool_result))
            sources = _merge_sources(
                sources,
                list(tool_result.get("sources") or []),
            )
        targeted_rounds += 1
        if runtime_context.logger:
            runtime_context.logger.info(
                "source_orchestration "
                f"targeted_round={targeted_rounds} "
                f"tools={','.join(name for _id, name, _args in parsed_calls)}"
            )
