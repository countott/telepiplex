# -*- coding: utf-8 -*-

import json
import os
import sys

import requests

current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
sys.path.append(current_dir)

import init
from app.utils.log_sanitizer import sanitize_log_value


DEFAULT_AI_REQUEST_TIMEOUT_SECONDS = 60

SEARCH_QUERY_NORMALIZATION_PROMPT = """你是影视搜索请求清洗助手。

任务：把用户输入拆解成可用于豆瓣/TVDB回查的候选查询。

要求：
1. 只返回JSON，不要返回Markdown、解释或额外文字。
2. 不要编造豆瓣、TVDB、IMDb、TMDB或MovieDB ID。
3. 可以修正明显口误、错别字、拼写错误和中英文混输。
4. 保留季集意图，例如 S09E07、9x07、第九季第七集。
5. 返回 1-3 个候选，按可信度从高到低排列。

JSON结构：
{
  "candidates": [
    {
      "title": "string",
      "year": "string",
      "media_type": "movie|series|",
      "season_number": 0,
      "episode_number": 0,
      "scope": "movie|whole_series|season|episode|",
      "reason": "string"
    }
  ]
}

输入：
"""

VERIFIED_SEARCH_MATCH_PROMPT = """你是影视条目核验助手。

任务：当豆瓣/TVDB API两轮回查都失败时，尝试给出可验证的外部ID。

要求：
1. 只返回JSON，不要返回Markdown、解释或额外文字。
2. 只有在你能明确识别影视条目时才返回 candidate。
3. 不要编造不确定的外部ID；不确定就返回空对象。
4. AI 只是兜底，返回结果仍会重新进入豆瓣/TVDB验证链。

JSON结构：
{
  "candidate": {
    "title": "string",
    "year": "string",
    "media_type": "movie|series",
    "external_ids": {
      "douban_subject": "string",
      "imdb": "string",
      "tvdb": "string",
      "tmdb": "string"
    },
    "reason": "string"
  }
}

输入：
"""

METADATA_BACKFILL_PROMPT = """你是影视元数据补全助手。

任务：根据已经确认的英文名、年份和外部ID，补全缺失中文名或媒体类型。

要求：
1. 只返回JSON，不要返回Markdown、解释或额外文字。
2. 不要覆盖输入中已经确认的字段。
3. 不确定时留空。

JSON结构：
{
  "chinese_title": "string",
  "english_title": "string",
  "year": "string",
  "media_type": "movie|series",
  "external_ids": {
    "douban_subject": "string",
    "imdb": "string",
    "tvdb": "string",
    "tmdb": "string"
  }
}

输入：
"""


def check_ai_api_available():
    ai_config = init.bot_config.get("ai") or {}
    return bool(
        str(ai_config.get("api_url") or "").strip()
        and str(ai_config.get("api_key") or "").strip()
        and str(ai_config.get("model") or "").strip()
    )


def _log_ai_info(message: str):
    logger = getattr(init, "logger", None)
    if logger:
        logger.info(message)


def _compact_json_for_log(value, max_chars=6000) -> str:
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > max_chars:
        return text[:max_chars] + "...<truncated>"
    return text


def _ai_request_timeout():
    ai_config = init.bot_config.get("ai") or {}
    try:
        return float(ai_config.get("timeout", DEFAULT_AI_REQUEST_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_AI_REQUEST_TIMEOUT_SECONDS


def chat_completion(tip_words, max_tokens=8192):
    if not check_ai_api_available():
        return ""

    ai_config = init.bot_config.get("ai") or {}
    url = str(ai_config.get("api_url") or "").rstrip("/")
    api_key = str(ai_config.get("api_key") or "")
    model = str(ai_config.get("model") or "")
    endpoint = f"{url}/chat/completions" if not url.endswith("/chat/completions") else url
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": tip_words}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=_ai_request_timeout())
        response.raise_for_status()
        data = response.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        if getattr(init, "logger", None):
            init.logger.warn(f"AI请求失败: {sanitize_log_value(e)}")
        return ""


def _strip_json_markdown(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text


def parse_ai_json_response(result):
    result = _strip_json_markdown(result)
    if not result:
        return {}
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        start = result.find("{")
        end = result.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(result[start : end + 1])
            except json.JSONDecodeError:
                return {}
    return {}


def normalize_search_query_with_ai(raw_query: str):
    if not check_ai_api_available():
        return None
    prompt = SEARCH_QUERY_NORMALIZATION_PROMPT + str(raw_query or "")
    _log_ai_info(f"AI搜索清洗输入 raw_query={sanitize_log_value(raw_query)}")
    result = chat_completion(prompt)
    _log_ai_info(f"AI搜索清洗原始响应: {sanitize_log_value(result)}")
    data = parse_ai_json_response(result)
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list):
        return None
    return {"candidates": [item for item in candidates if isinstance(item, dict)]}


def infer_verified_search_match_with_ai(raw_query: str):
    if not check_ai_api_available():
        return None
    prompt = VERIFIED_SEARCH_MATCH_PROMPT + str(raw_query or "")
    _log_ai_info(f"AI条目核验输入 raw_query={sanitize_log_value(raw_query)}")
    result = chat_completion(prompt)
    _log_ai_info(f"AI条目核验原始响应: {sanitize_log_value(result)}")
    data = parse_ai_json_response(result)
    candidate = data.get("candidate") if isinstance(data, dict) else None
    return candidate if isinstance(candidate, dict) else None


def infer_metadata_backfill_with_ai(context: dict):
    if not check_ai_api_available():
        return None
    prompt = METADATA_BACKFILL_PROMPT + json.dumps(context or {}, ensure_ascii=False, indent=2)
    _log_ai_info(f"AI元数据补全输入 context={_compact_json_for_log(context)}")
    result = chat_completion(prompt)
    _log_ai_info(f"AI元数据补全原始响应: {sanitize_log_value(result)}")
    data = parse_ai_json_response(result)
    return data if isinstance(data, dict) else None
