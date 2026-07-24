# -*- coding: utf-8 -*-
"""OpenAI-compatible planning client configured by this Feature."""

import requests
import json
from .context import runtime_context
from .log_sanitizer import sanitize_log_value


DEFAULT_AI_REQUEST_TIMEOUT_SECONDS = 60


TVDB_EPISODE_PLAN_PROMPT = """你是媒体库剧集整理助手。根据输入的多源事实，推断 TVDB 剧集和文件重命名映射。

要求：
1. 只返回JSON，不要返回Markdown、解释或额外文字。
2. 不要编造输入中不存在的文件名。
3. 如果无法可靠匹配，返回空的 episode_map，并在 warnings 中说明原因。
4. evidence 必须来自输入事实，用于后续代码交叉校验，不要输出自评置信分。
5. target_relative_path 使用 "Series Name Season 01/Series Name S01E01.ext"；Special 使用 Season 00；三位数集数使用 E100。
6. 单个视频文件也可能是剧集单集；如果 release_title、resource_name、file_tree 或文件名包含 S01E01、1x02、第1季第2集等集数线索，应优先按剧集单集匹配 TVDB 候选和剧集列表。
7. 不要仅因为 file_tree 只有一个视频文件就判定为电影；只有在标题、年份、TVDB 候选和集数线索都无法支持剧集匹配时，才返回空 episode_map。

JSON结构：
{
  "tvdb_series_id": "string",
  "series_name": "string",
  "season_type": "official|default|dvd|absolute|alternate|regional",
  "evidence": {
    "title_match": true,
    "year_match": true,
    "episode_count_match": true,
    "notes": ["string"]
  },
  "episode_map": [
    {
      "source_file": "string",
      "target_relative_path": "Series Name Season 01/Series Name S01E01.ext",
      "target_name": "string",
      "tvdb_episode_id": 0,
      "season_number": 1,
      "episode_number": 1
    }
  ],
  "warnings": ["string"]
}

输入事实如下：
"""

SEARCH_QUERY_NORMALIZATION_PROMPT = """你是影视搜索请求清洗器。只返回JSON，不要返回Markdown、解释或额外文字。

任务：把用户输入拆解成可用于豆瓣/TVDB回查的候选查询。

硬性规则：
1. 这一步不验证影视条目。
2. 不要编造豆瓣、TVDB、IMDb、TMDB或MovieDB ID。
3. 不要编造季数、集数、播出日期或播出状态。
4. 不要输出Prowlarr query。
5. 只保留用户明确表达的季/集/全集/整季意图。
6. 可以修正常见错别字、去掉清晰度/字幕组/平台/资源格式等噪声。
7. 先在内部区分两类情况：同义表达归一化（如 S09E07、Season 9 Episode 7、第九季第七集）和口误修正（如连续层级表达写错）。
8. 可以纠正明显口误的连续层级表达：例如“第九集第七集”通常应理解为“第九季第七集”，输出 scope=episode、season_number=9、episode_number=7。
9. 输出前自检：如果季/集来自明显连续层级口误，必须能用用户原文解释为“前一个数字是季，后一个数字是集”；不确定时不要补季数或集数，也不要从单独“第七集”推断季数。

JSON结构：
{
  "status": "ok|blocked",
  "lookup_candidates": [
    {
      "query": "string",
      "title": "string",
      "year": "string",
      "scope": "movie_or_series|whole_series|season|episode",
      "season_number": 1,
      "episode_number": 1
    }
  ],
  "warnings": ["string"]
}

用户输入：
"""

SEARCH_VERIFIED_MATCH_PROMPT = """你是影视条目验证助手。只返回JSON，不要返回Markdown、解释或额外文字。

任务：当豆瓣/TVDB API两轮回查都失败时，尝试给出可验证的外部ID。

硬性规则：
1. 没有可验证外部ID的结果不可接受。
2. 不要编造ID。
3. 不要编造播出状态、季数、集数或播出日期。
4. 不要输出Prowlarr query。
5. 如果没有可验证匹配，返回 blocked_no_verified_match。
6. 如果明确知道请求集数尚未播出，返回 blocked_unreleased。

JSON结构：
{
  "status": "ok|blocked_no_verified_match|blocked_unreleased",
  "candidates": [
    {
      "media_type": "movie|series",
      "title": "string",
      "year": "string",
      "external_ids": {
        "douban_subject": "string",
        "tvdb": "string",
        "imdb": "string",
        "tmdb": "string",
        "moviedb": "string"
      },
      "scope": "movie|whole_series|season|episode",
      "season_number": 1,
      "episode_number": 1
    }
  ],
  "reason": "string"
}

用户输入：
"""

METADATA_BACKFILL_PROMPT = """你是影视元数据补全助手。只返回JSON，不要返回Markdown、解释或额外文字。

任务：根据已验证的英文标题、年份和外部ID，补全媒体库重命名元数据，尤其是中文片名。

硬性规则：
1. 不要编造外部ID；只能复用输入中已有的外部ID，或在确定来自真实公开资料时返回。
2. 如果无法确认中文片名和英文片名，返回 status=blocked。
3. 不要输出Prowlarr query。
4. 不要输出剧情、评分、演员或解释。

JSON结构：
{
  "status": "ok|blocked",
  "media_type": "movie|series",
  "chinese_title": "string",
  "english_title": "string",
  "year": "string",
  "external_ids": {
    "douban_subject": "string",
    "tvdb": "string",
    "imdb": "string",
    "tmdb": "string"
  },
  "reason": "string"
}

输入事实如下：
"""

SEARCH_HYPOTHESIS_PROMPT = """你是影视搜索意图解释器。只返回JSON。
只在规则无法理解非标准自然语言或首轮零候选时使用。你的输出只是待外部来源验证的意图提示。
不得输出豆瓣/TVDB/IMDb/TMDB稳定ID、用户未提供的年份、官方英文名结论、罗马字结论、TVDB库存、Prowlarr query、路径、media_metadata或Season 00编号。
title_hints 按建议检索优先级排列：纠正或规范化标题优先，用户原文最后。
JSON结构：
{"status":"parsed|needs_clarification|unsupported","title_hints":["string"],"media_type_hint":"movie|series|unknown","scope_hint":"work|whole_series|season|episode|latest_aired|unknown","season_number":null,"episode_number":null,"numeric_tokens":[{"value":1,"role":"year|official_title_part|season|episode|ambiguous"}],"relation_hint":"none|prequel|sequel|special|movie_version|unknown","clarification_reason":"string"}
用户输入：
"""

RELATION_SCOUT_PROMPT = """你是影视作品关系审查员。只返回 JSON，不要返回 Markdown。
你只能引用输入中的 candidate_key 和 fact_id，不得编造标题、年份、稳定 ID、集号或来源事实。
最多输出 3 个关系假设；假设不是事实，后续必须由程序定向复查。
结构：{"hypotheses":[{"candidate_key":"string","relation_type":"standalone|prequel|sequel|spin_off|special|extension_movie","target_candidate_key":"string","fact_ids":["string"],"reason":"string"}]}
输入事实：
"""

CANDIDATE_SCORECARD_PROMPT = """你是影视候选评分员。只返回 JSON，不要返回 Markdown。
你不是数据库，也不能补全事实。你只能引用输入中的 candidate_key 和 fact_id。
不得输出或修改标题、年份、媒体类型、外部 ID、检索范围、Prowlarr query 或候选集合。
必须为输入中的每个候选返回且只返回一项评分；不得遗漏、增加或合并候选。
固定评分维度：
1. title_equivalence：0-20，用户作品表达与已验证标题事实的语义等价程度。
2. intent_relevance：0-10，候选媒体类型和年份事实与用户明确意图的相关程度。
3. relation_consistency：0-10，候选已验证关系事实与用户关系表达的一致程度；没有关系表达时按事实一致性评分。
每项必须列出实际使用的 fact_ids。total 可省略，程序会自行重算。
结构：{"scores":[{"candidate_key":"string","title_equivalence":0,"intent_relevance":0,"relation_consistency":0,"fact_ids":["string"]}]}
输入事实：
"""

def check_ai_api_available():
    url = runtime_context.config.get("ai", {}).get("api_url", "")
    if not url:
        if runtime_context.logger:
            runtime_context.logger.info("AI API URL 未定义，跳过可选 AI 阶段。")
        return False
    model = runtime_context.config.get("ai", {}).get("model", "")
    if not model:
        if runtime_context.logger:
            runtime_context.logger.info("AI 模型未定义，跳过可选 AI 阶段。")
        return False
    
    api_key = runtime_context.config.get("ai", {}).get("api_key", "")
    if not api_key:
        if runtime_context.logger:
            runtime_context.logger.info("AI API Key 未定义，跳过可选 AI 阶段。")
        return False
    return True


def _log_ai_info(message: str):
    logger = runtime_context.logger
    if logger:
        logger.info(message)


def _compact_json_for_log(value, max_chars=6000) -> str:
    return sanitize_log_value(value, max_chars=max_chars)


def _ai_request_timeout():
    ai_config = (runtime_context.config or {}).get("ai") or {}
    value = ai_config.get("timeout", DEFAULT_AI_REQUEST_TIMEOUT_SECONDS)
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = DEFAULT_AI_REQUEST_TIMEOUT_SECONDS
    return max(timeout, 1)


def _ai_config() -> dict:
    return (runtime_context.config or {}).get("ai") or {}


def _chat_completion_url() -> str:
    url = str(_ai_config().get("api_url") or "").strip()
    # 智能判断是否需要拼接 /chat/completions
    # 如果URL中不包含 chat/completions 也不包含 messages (适配Anthropic风格)，且不以 / 结尾，则尝试拼接
    if "chat/completions" not in url and "messages" not in url:
        if url.endswith("/"):
            url = url[:-1] + "/chat/completions"
        else:
            url = url + "/chat/completions"
    return url


def _provider_error_kind(http_status: int) -> tuple[str, bool]:
    if http_status == 400:
        return "provider_invalid_request", False
    if http_status == 401:
        return "authentication_failed", False
    if http_status == 403:
        return "permission_denied", False
    if http_status == 404:
        return "model_or_endpoint_not_found", False
    if http_status == 408:
        return "provider_timeout", True
    if http_status == 429:
        return "rate_limited", True
    if http_status >= 500:
        return "provider_unavailable", True
    return "provider_client_error", False


def _provider_error_result(
    *,
    kind: str,
    http_status: int = 0,
    code="",
    error_type="",
    param="",
    message="",
    retryable: bool = False,
    request_id="",
) -> dict:
    return {
        "error": {
            "kind": str(kind or "provider_client_error"),
            "http_status": int(http_status or 0),
            "code": str(code or ""),
            "type": str(error_type or ""),
            "param": str(param or ""),
            "message": sanitize_log_value(message, max_chars=1000),
            "retryable": bool(retryable),
            "request_id": str(request_id or ""),
        },
    }


def _response_request_id(response) -> str:
    headers = getattr(response, "headers", None)
    if not hasattr(headers, "get"):
        return ""
    return str(
        headers.get("x-request-id")
        or headers.get("request-id")
        or ""
    )


def _response_error_result(response) -> dict:
    try:
        payload = response.json()
    except Exception:
        payload = {}
    raw_error = (
        payload.get("error")
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict)
        else {}
    )
    http_status = int(getattr(response, "status_code", 0) or 0)
    kind, retryable = _provider_error_kind(http_status)
    message = (
        raw_error.get("message")
        or getattr(response, "text", "")
        or f"HTTP {http_status}"
    )
    return _provider_error_result(
        kind=kind,
        http_status=http_status,
        code=raw_error.get("code"),
        error_type=raw_error.get("type"),
        param=raw_error.get("param"),
        message=message,
        retryable=retryable,
        request_id=_response_request_id(response),
    )


def _post_chat_completion(payload: dict):
    config = _ai_config()
    headers = {
        "Authorization": f"Bearer {config.get('api_key') or ''}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            _chat_completion_url(),
            json=payload,
            headers=headers,
            timeout=_ai_request_timeout(),
        )
        if response.status_code != 200:
            result = _response_error_result(response)
            logger = runtime_context.logger
            if logger:
                logger.warning(
                    "AI provider request failed "
                    f"error={sanitize_log_value(result)}"
                )
            return result

        return response.json()
    except requests.Timeout as exc:
        result = _provider_error_result(
            kind="provider_timeout",
            message=str(exc),
            retryable=True,
        )
        logger = runtime_context.logger
        if logger:
            logger.warning(
                "AI provider request failed "
                f"error={sanitize_log_value(result)}"
            )
        return result
    except requests.RequestException as exc:
        result = _provider_error_result(
            kind="provider_unavailable",
            message=str(exc),
            retryable=True,
        )
        logger = runtime_context.logger
        if logger:
            logger.warning(
                "AI provider request failed "
                f"error={sanitize_log_value(result)}"
            )
        return result
    except Exception as exc:
        result = _provider_error_result(
            kind="provider_client_error",
            message=f"{type(exc).__name__}: {exc}",
        )
        logger = runtime_context.logger
        if logger:
            logger.error(
                "AI provider client error "
                f"error={sanitize_log_value(result)}"
            )
        return result


def chat_completion_messages(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    tool_choice=None,
    thinking_mode: str | None = None,
    max_tokens: int = 4096,
):
    """Send one OpenAI-compatible chat request without exposing credentials."""

    payload = {
        "model": _ai_config().get("model"),
        "messages": list(messages or []),
        "max_tokens": max_tokens,
    }
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if thinking_mode is not None:
        thinking_mode = str(thinking_mode).strip().casefold()
        if thinking_mode not in {"enabled", "disabled"}:
            raise ValueError("invalid thinking mode")
        payload["thinking"] = {"type": thinking_mode}
    return _post_chat_completion(payload)


def chat_completion(tip_words, max_tokens=8192):
    return chat_completion_messages(
        [{"role": "user", "content": tip_words}],
        max_tokens=max_tokens,
    )


def extract_ai_message(result) -> dict | None:
    """Return the first assistant message from an OpenAI-compatible response."""

    if not isinstance(result, dict):
        return None
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message")
    return dict(message) if isinstance(message, dict) else None


def _strip_json_markdown(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.replace("```json", "", 1).replace("```", "").strip()
    return text


def parse_ai_json_response(result):
    if not isinstance(result, dict):
        return None

    text_content = ""
    if isinstance(result.get("content"), list) and result["content"]:
        text_content = result["content"][0].get("text", "")
    elif isinstance(result.get("choices"), list) and result["choices"]:
        message = result["choices"][0].get("message") or {}
        text_content = message.get("content", "")

    text_content = _strip_json_markdown(text_content)
    if not text_content:
        return None

    try:
        return json.loads(text_content)
    except json.JSONDecodeError:
        logger = runtime_context.logger
        if logger:
            logger.warn(f"AI返回的不是有效的JSON格式: {text_content}")
        return None


def infer_tvdb_episode_plan_with_ai(context: dict):
    if not check_ai_api_available():
        return None

    prompt = TVDB_EPISODE_PLAN_PROMPT + json.dumps(context or {}, ensure_ascii=False, indent=2)
    _log_ai_info(f"AI TVDB映射输入 context={_compact_json_for_log(context)}")
    result = chat_completion(prompt, max_tokens=4096)
    if runtime_context.logger:
        runtime_context.logger.info(f"AI TVDB映射原始响应: {sanitize_log_value(result)}")
    plan = parse_ai_json_response(result)
    if not isinstance(plan, dict):
        return None

    episode_map = plan.get("episode_map")
    if not isinstance(episode_map, list):
        plan["episode_map"] = []
    warnings = plan.get("warnings")
    if not isinstance(warnings, list):
        plan["warnings"] = []
    evidence = plan.get("evidence")
    if not isinstance(evidence, dict):
        plan["evidence"] = {}
    return plan


def _without_prowlarr_query(value):
    if isinstance(value, dict):
        value.pop("prowlarr_query", None)
        for nested in value.values():
            _without_prowlarr_query(nested)
    elif isinstance(value, list):
        for item in value:
            _without_prowlarr_query(item)
    return value


def infer_search_hypotheses_with_ai(context):
    if not check_ai_api_available():
        return None

    prompt_input = (
        json.dumps(context, ensure_ascii=False, indent=2)
        if isinstance(context, dict)
        else str(context or "").strip()
    )
    prompt = SEARCH_HYPOTHESIS_PROMPT + prompt_input
    _log_ai_info(f"AI搜索假设输入 context={_compact_json_for_log(context)}")
    result = chat_completion(prompt, max_tokens=4096)
    _log_ai_info(f"AI搜索假设原始响应 result={_compact_json_for_log(result)}")
    parsed = parse_ai_json_response(result)
    if not isinstance(parsed, dict):
        return None
    allowed = {
        "status",
        "title_hints",
        "media_type_hint",
        "scope_hint",
        "season_number",
        "episode_number",
        "numeric_tokens",
        "relation_hint",
        "clarification_reason",
    }
    if set(parsed) != allowed:
        return None
    if parsed.get("status") not in {
        "parsed", "needs_clarification", "unsupported"
    }:
        return None
    titles = parsed.get("title_hints")
    if (
        not isinstance(titles, list)
        or any(not isinstance(item, str) for item in titles)
    ):
        return None
    titles = [
        " ".join(item.split())
        for item in titles[:3]
        if " ".join(item.split())
    ]
    if parsed["status"] == "unsupported" or not titles:
        return None
    media_type = parsed.get("media_type_hint")
    scope = parsed.get("scope_hint")
    if media_type not in {"movie", "series", "unknown"}:
        return None
    if scope not in {
        "work", "whole_series", "season", "episode", "latest_aired", "unknown"
    }:
        return None
    for key in ("season_number", "episode_number"):
        value = parsed.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            return None
    numeric_tokens = parsed.get("numeric_tokens")
    if not isinstance(numeric_tokens, list) or any(
        not isinstance(item, dict)
        or set(item) != {"value", "role"}
        or isinstance(item.get("value"), bool)
        or not isinstance(item.get("value"), int)
        or item.get("role") not in {
            "year", "official_title_part", "season", "episode", "ambiguous"
        }
        for item in numeric_tokens
    ):
        return None
    mapped_scope = {
        "work": "movie_or_series",
        "unknown": "movie_or_series",
        "latest_aired": "movie_or_series",
    }.get(scope, scope)
    hypotheses = [{
        "title": title,
        "year": "",
        "content_identity": media_type,
        "scope": mapped_scope,
        "season_number": parsed.get("season_number"),
        "episode_number": parsed.get("episode_number"),
        "possible_related_series": [],
        "explicit_facts": [],
        "inferred_facts": ["ai_intent_hint"],
    } for title in titles]
    return {
        "status": (
            "needs_clarification"
            if parsed["status"] == "needs_clarification"
            else "ok"
        ),
        "hypotheses": hypotheses,
        "source_queries": {
            name: list(titles) for name in ("wikipedia", "douban", "tvdb")
        },
        "warnings": ["ai_intent_hint_requires_source_verification"],
        "intent_hint": parsed,
        "clarification_reason": (
            str(parsed.get("clarification_reason") or "").strip()
            if parsed["status"] == "needs_clarification"
            else ""
        ),
    }


def infer_relation_hypotheses_with_ai(context: dict):
    if not check_ai_api_available():
        return None
    prompt = RELATION_SCOUT_PROMPT + json.dumps(
        context or {}, ensure_ascii=False, separators=(",", ":")
    )
    result = chat_completion(prompt, max_tokens=1800)
    parsed = parse_ai_json_response(result)
    if not isinstance(parsed, dict) or set(parsed) != {"hypotheses"}:
        return None
    hypotheses = parsed.get("hypotheses")
    if not isinstance(hypotheses, list) or any(
        not isinstance(item, dict) for item in hypotheses
    ):
        return None
    return {"hypotheses": hypotheses[:3]}


def infer_candidate_scorecard_with_ai(context: dict):
    if not check_ai_api_available():
        return None
    prompt = CANDIDATE_SCORECARD_PROMPT + json.dumps(
        context or {},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    _log_ai_info(
        f"AI候选评分输入 context={_compact_json_for_log(context)}"
    )
    result = chat_completion(prompt, max_tokens=2400)
    _log_ai_info(
        f"AI候选评分原始响应 result={_compact_json_for_log(result)}"
    )
    parsed = parse_ai_json_response(result)
    if not isinstance(parsed, dict) or set(parsed) != {"scores"}:
        return None
    scores = parsed.get("scores")
    if not isinstance(scores, list) or any(
        not isinstance(item, dict) for item in scores
    ):
        return None
    return {"scores": scores}


def normalize_search_query_with_ai(raw_query: str):
    if not check_ai_api_available():
        return None

    _log_ai_info(f"AI搜索清洗输入 raw={raw_query}")
    result = chat_completion(SEARCH_QUERY_NORMALIZATION_PROMPT + str(raw_query or ""), max_tokens=2048)
    _log_ai_info(f"AI搜索清洗原始响应 raw={raw_query} response={_compact_json_for_log(result)}")
    plan = parse_ai_json_response(result)
    if not isinstance(plan, dict):
        return None

    candidates = plan.get("lookup_candidates")
    if not isinstance(candidates, list):
        plan["lookup_candidates"] = []
    warnings = plan.get("warnings")
    if not isinstance(warnings, list):
        plan["warnings"] = []
    plan["status"] = plan.get("status") or "ok"
    _log_ai_info(
        f"AI搜索清洗解析结果 raw={raw_query} status={plan.get('status')} "
        f"candidates={len(plan.get('lookup_candidates') or [])} warnings={len(plan.get('warnings') or [])}"
    )
    return _without_prowlarr_query(plan)


def infer_verified_search_match_with_ai(raw_query: str):
    if not check_ai_api_available():
        return None

    _log_ai_info(f"AI验证兜底输入 raw={raw_query}")
    result = chat_completion(SEARCH_VERIFIED_MATCH_PROMPT + str(raw_query or ""), max_tokens=2048)
    _log_ai_info(f"AI验证兜底原始响应 raw={raw_query} response={_compact_json_for_log(result)}")
    plan = parse_ai_json_response(result)
    if not isinstance(plan, dict):
        return None

    candidates = plan.get("candidates")
    if not isinstance(candidates, list):
        plan["candidates"] = []

    verified_candidates = []
    for candidate in plan["candidates"]:
        if not isinstance(candidate, dict):
            continue
        external_ids = candidate.get("external_ids") if isinstance(candidate.get("external_ids"), dict) else {}
        if any(str(value or "").strip() for value in external_ids.values()):
            verified_candidates.append(_without_prowlarr_query(candidate))
    plan["candidates"] = verified_candidates
    if not verified_candidates and plan.get("status") == "ok":
        plan["status"] = "blocked_no_verified_match"
    plan["status"] = plan.get("status") or "blocked_no_verified_match"
    _log_ai_info(
        f"AI验证兜底解析结果 raw={raw_query} status={plan.get('status')} "
        f"candidates={len(plan.get('candidates') or [])}"
    )
    return _without_prowlarr_query(plan)


def infer_metadata_backfill_with_ai(context: dict):
    if not check_ai_api_available():
        return None

    prompt = METADATA_BACKFILL_PROMPT + json.dumps(context or {}, ensure_ascii=False, indent=2)
    _log_ai_info(f"AI元数据补全输入 context={_compact_json_for_log(context)}")
    result = chat_completion(prompt, max_tokens=2048)
    _log_ai_info(f"AI元数据补全原始响应 response={_compact_json_for_log(result)}")
    plan = parse_ai_json_response(result)
    if not isinstance(plan, dict) or plan.get("status") != "ok":
        return None

    chinese_title = str(plan.get("chinese_title") or "").strip()
    english_title = str(plan.get("english_title") or "").strip()
    if not chinese_title or not english_title:
        return None

    external_ids = plan.get("external_ids") if isinstance(plan.get("external_ids"), dict) else {}
    return {
        "source": "ai_metadata_backfill",
        "media_type": str(plan.get("media_type") or (context or {}).get("media_type") or "").strip(),
        "chinese_title": chinese_title,
        "english_title": english_title,
        "year": str(plan.get("year") or (context or {}).get("year") or "").strip(),
        "external_ids": {
            str(key): str(value).strip()
            for key, value in external_ids.items()
            if str(value or "").strip()
        },
    }

def get_movie_tmdb_name_with_ai(movie_desc):
    
    if not check_ai_api_available():
        return None
    
    tip_words = f"'{movie_desc}' 请根据这个字符串，推断出可能的电影名称，然后根据电影名称，去TMDB网站(https://www.themoviedb.org)找到电影的TMDB ID，最后根据TMDB ID找到其对应的完整中文名称。注意：1. 优先匹配年份和英文原名。2. 如果有多个中文译名，请优先选择TMDB上的官方中文译名或最通用的译名。3. 有些系列电影可能会包含序号，比如：“侏罗纪公园2” 对应完整的中文名称应该是“侏罗纪公园2：失落的世界”。请返回json格式{{\"name\": \"完整的中文电影名称\"}} 。不要包含任何多余文字，如果找不到对应的中文名称请返回 {{\"name\": \"\"}}"
    try:
        result = chat_completion(tip_words)
        runtime_context.logger.info(f"AI原始响应: {sanitize_log_value(result)}")
        
        # 解析返回结果
        # 针对Anthropic/SiliconFlow messages接口: {'content': [{'text': '{"name": "..."}'...} ...}
        if isinstance(result, dict) and 'content' in result and isinstance(result['content'], list) and len(result['content']) > 0:
            text_content = result['content'][0].get('text', '')
            # 清理可能存在的markdown标记
            if "```" in text_content:
                text_content = text_content.replace("```json", "").replace("```", "").strip()
            
            try:
                json_data = json.loads(text_content)
                return json_data.get('name')
            except json.JSONDecodeError:
                runtime_context.logger.warn(f"AI返回的不是有效的JSON格式: {text_content}")
                return None

        # 兼容OpenAI格式: choices[0].message.content
        if isinstance(result, dict) and 'choices' in result and len(result['choices']) > 0:
            content = result['choices'][0]['message']['content']
            if "```" in content:
                content = content.replace("```json", "").replace("```", "").strip()
            try:
                json_data = json.loads(content)
                return json_data.get('name')
            except json.JSONDecodeError:
                return None
                
        return None
        
    except Exception as e:
        runtime_context.logger.error(f"调用AI接口出错: {e}")
        return None
