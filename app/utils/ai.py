# -*- coding: utf-8 -*-
import requests
import sys
import os
import json
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
sys.path.append(current_dir)
import init


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

def check_ai_api_available():
    url = init.bot_config.get("ai", {}).get("api_url", "")
    if not url:
        if getattr(init, "logger", None):
            init.logger.warn("AI API URL 未定义.")
        return False
    model = init.bot_config.get("ai", {}).get("model", "")
    if not model:
        if getattr(init, "logger", None):
            init.logger.warn("AI 模型未定义.")
        return False
    
    api_key = init.bot_config.get("ai", {}).get("api_key", "")
    if not api_key:
        if getattr(init, "logger", None):
            init.logger.warn("AI API Key 未定义.")
        return False
    return True

def chat_completion(tip_words, max_tokens=8192):
    url = init.bot_config.get("ai").get("api_url")
    # 智能判断是否需要拼接 /chat/completions
    # 如果URL中不包含 chat/completions 也不包含 messages (适配Anthropic风格)，且不以 / 结尾，则尝试拼接
    if "chat/completions" not in url and "messages" not in url:
        if url.endswith("/"):
            url = url[:-1] + "/chat/completions"
        else:
            url = url + "/chat/completions"
            
    payload = {
        "model": init.bot_config.get("ai").get("model"),
        "messages": [{"role": "user", "content": tip_words}],
        "max_tokens": max_tokens
    }
    headers = {
        "Authorization": f"Bearer {init.bot_config.get('ai').get('api_key')}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            init.logger.warn(f"AI API请求失败: {response.text}")
            return None
            
        result = response.json()
        return result
        
    except Exception as e:
        init.logger.error(f"调用AI接口出错: {e}")
        return None


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
        logger = getattr(init, "logger", None)
        if logger:
            logger.warn(f"AI返回的不是有效的JSON格式: {text_content}")
        return None


def infer_tvdb_episode_plan_with_ai(context: dict):
    if not check_ai_api_available():
        return None

    prompt = TVDB_EPISODE_PLAN_PROMPT + json.dumps(context or {}, ensure_ascii=False, indent=2)
    result = chat_completion(prompt, max_tokens=4096)
    if getattr(init, "logger", None):
        init.logger.info(f"AI TVDB映射原始响应: {result}")
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


def normalize_search_query_with_ai(raw_query: str):
    if not check_ai_api_available():
        return None

    result = chat_completion(SEARCH_QUERY_NORMALIZATION_PROMPT + str(raw_query or ""), max_tokens=2048)
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
    return _without_prowlarr_query(plan)


def infer_verified_search_match_with_ai(raw_query: str):
    if not check_ai_api_available():
        return None

    result = chat_completion(SEARCH_VERIFIED_MATCH_PROMPT + str(raw_query or ""), max_tokens=2048)
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
    return _without_prowlarr_query(plan)

def get_movie_tmdb_name_with_ai(movie_desc):
    
    if not check_ai_api_available():
        return None
    
    tip_words = f"'{movie_desc}' 请根据这个字符串，推断出可能的电影名称，然后根据电影名称，去TMDB网站(https://www.themoviedb.org)找到电影的TMDB ID，最后根据TMDB ID找到其对应的完整中文名称。注意：1. 优先匹配年份和英文原名。2. 如果有多个中文译名，请优先选择TMDB上的官方中文译名或最通用的译名。3. 有些系列电影可能会包含序号，比如：“侏罗纪公园2” 对应完整的中文名称应该是“侏罗纪公园2：失落的世界”。请返回json格式{{\"name\": \"完整的中文电影名称\"}} 。不要包含任何多余文字，如果找不到对应的中文名称请返回 {{\"name\": \"\"}}"
    try:
        result = chat_completion(tip_words)
        init.logger.info(f"AI原始响应: {result}")
        
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
                init.logger.warn(f"AI返回的不是有效的JSON格式: {text_content}")
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
        init.logger.error(f"调用AI接口出错: {e}")
        return None


if __name__ == "__main__":
    init.init_log()
    init.load_yaml_config()
    test_desc = "Die My Love (2025) iTA-ENG.WEBDL.1080p.x264-Dr4gon.mkv"
    movie_name = get_movie_tmdb_name_with_ai(test_desc)
    print(f"识别到的电影名称: {movie_name}")
