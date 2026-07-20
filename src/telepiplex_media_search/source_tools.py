"""Bounded tool schemas and server-side source execution."""

from __future__ import annotations

import asyncio
import inspect
import re
from copy import deepcopy
from typing import Callable


MAX_SOURCE_QUERIES = 3
MAX_TARGETS_PER_TOOL = 3
MAX_QUERY_LENGTH = 160

SOURCE_STATUSES = {
    "ok",
    "not_found",
    "disabled",
    "credential_missing",
    "authentication_failed",
    "timeout",
    "rate_limited",
    "blocked",
    "server_down",
}

_SENSITIVE_ARGUMENT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "base_url",
    "cookie",
    "cookies",
    "header",
    "headers",
    "raw_query",
    "subscriber_pin",
    "token",
    "url",
}
_REDACTED_RESULT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "base_url",
    "cookie",
    "cookies",
    "extract",
    "header",
    "headers",
    "html",
    "overview",
    "raw",
    "raw_response",
    "subscriber_pin",
    "token",
}
_URL_PATTERN = re.compile(r"(?i)https?://")


class ToolValidationError(ValueError):
    def __init__(self, code: str):
        self.code = str(code or "tool_arguments_invalid")
        super().__init__(self.code)


def _closed_object(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_STRING_ARRAY = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_LENGTH},
    "maxItems": MAX_TARGETS_PER_TOOL,
}

FIRST_ROUND_TOOL = {
    "type": "function",
    "function": {
        "name": "search_media_sources",
        "description": (
            "Run the mandatory first evidence round across Wikipedia, Douban, "
            "and TVDB. Credentials are injected by the server."
        ),
        "parameters": _closed_object(
            {
                "intent": _closed_object(
                    {
                        "title_hints": _STRING_ARRAY,
                        "media_type_hint": {
                            "type": "string",
                            "enum": ["movie", "series", "unknown"],
                        },
                        "year_hint": {"type": "string", "maxLength": 4},
                        "scope": {
                            "type": "string",
                            "enum": [
                                "work",
                                "whole_series",
                                "season",
                                "episode",
                                "unknown",
                            ],
                        },
                        "season_number": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                        },
                        "episode_number": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                        },
                    },
                    [
                        "title_hints",
                        "media_type_hint",
                        "year_hint",
                        "scope",
                        "season_number",
                        "episode_number",
                    ],
                ),
                "source_queries": _closed_object(
                    {
                        "wikipedia_zh": _STRING_ARRAY,
                        "wikipedia_en": _STRING_ARRAY,
                        "douban": _STRING_ARRAY,
                        "tvdb": _STRING_ARRAY,
                    },
                    ["wikipedia_zh", "wikipedia_en", "douban", "tvdb"],
                ),
            },
            ["intent", "source_queries"],
        ),
    },
}

TARGETED_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_wikipedia_entity",
            "description": "Re-query Wikipedia using titles from current evidence.",
            "parameters": _closed_object(
                {
                    "fact_ids": _STRING_ARRAY,
                    "queries": _STRING_ARRAY,
                },
                ["fact_ids", "queries"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_douban_subject",
            "description": "Fetch known Douban subject IDs from current evidence.",
            "parameters": _closed_object(
                {
                    "fact_ids": _STRING_ARRAY,
                    "subject_ids": _STRING_ARRAY,
                },
                ["fact_ids", "subject_ids"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_tvdb_entity",
            "description": "Fetch or re-query TVDB entities referenced by evidence.",
            "parameters": _closed_object(
                {
                    "fact_ids": _STRING_ARRAY,
                    "queries": {
                        "type": "array",
                        "maxItems": MAX_TARGETS_PER_TOOL,
                        "items": _closed_object(
                            {
                                "title": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_QUERY_LENGTH,
                                },
                                "year": {"type": "string", "maxLength": 4},
                                "media_type": {
                                    "type": "string",
                                    "enum": ["movie", "series", "unknown"],
                                },
                            },
                            ["title", "year", "media_type"],
                        ),
                    },
                    "entity_ids": {
                        "type": "array",
                        "maxItems": MAX_TARGETS_PER_TOOL,
                        "items": _closed_object(
                            {
                                "tvdb_id": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 40,
                                },
                                "media_type": {
                                    "type": "string",
                                    "enum": ["movie", "series"],
                                },
                            },
                            ["tvdb_id", "media_type"],
                        ),
                    },
                },
                ["fact_ids", "queries", "entity_ids"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_tvdb_episodes",
            "description": "Fetch episode inventory for known TVDB Series IDs.",
            "parameters": _closed_object(
                {
                    "fact_ids": _STRING_ARRAY,
                    "series_ids": _STRING_ARRAY,
                },
                ["fact_ids", "series_ids"],
            ),
        },
    },
]

_TARGETED_BY_NAME = {
    item["function"]["name"]: item for item in TARGETED_TOOLS
}


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _validate_no_sensitive_arguments(value) -> None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = _text(raw_key).casefold()
            if key in _SENSITIVE_ARGUMENT_KEYS:
                raise ToolValidationError("sensitive_tool_argument")
            _validate_no_sensitive_arguments(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _validate_no_sensitive_arguments(nested)


def _validate_query_list(value, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_SOURCE_QUERIES:
        raise ToolValidationError("tool_query_limit_exceeded")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ToolValidationError("tool_query_invalid")
        item = _text(item)
        if (
            not item
            or len(item) > MAX_QUERY_LENGTH
            or "\n" in item
            or "\r" in item
            or _URL_PATTERN.search(item)
        ):
            raise ToolValidationError("tool_query_invalid")
        if item not in result:
            result.append(item)
    if not result and not allow_empty:
        raise ToolValidationError("tool_query_required")
    return result


def _validate_optional_number(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolValidationError("tool_intent_invalid")
    return value


def _validate_first_arguments(arguments: dict) -> tuple[dict, dict]:
    if not isinstance(arguments, dict):
        raise ToolValidationError("tool_arguments_invalid")
    _validate_no_sensitive_arguments(arguments)
    if set(arguments) != {"intent", "source_queries"}:
        raise ToolValidationError("tool_arguments_invalid")
    intent = arguments.get("intent")
    queries = arguments.get("source_queries")
    if not isinstance(intent, dict) or not isinstance(queries, dict):
        raise ToolValidationError("tool_arguments_invalid")
    expected_intent = {
        "title_hints",
        "media_type_hint",
        "year_hint",
        "scope",
        "season_number",
        "episode_number",
    }
    if set(intent) != expected_intent:
        raise ToolValidationError("tool_intent_invalid")
    title_hints = _validate_query_list(intent.get("title_hints"), allow_empty=False)
    media_type = _text(intent.get("media_type_hint")).casefold()
    scope = _text(intent.get("scope")).casefold()
    year = _text(intent.get("year_hint"))
    if media_type not in {"movie", "series", "unknown"}:
        raise ToolValidationError("tool_intent_invalid")
    if scope not in {"work", "whole_series", "season", "episode", "unknown"}:
        raise ToolValidationError("tool_intent_invalid")
    if year and not re.fullmatch(r"(?:19|20)\d{2}", year):
        raise ToolValidationError("tool_intent_invalid")
    normalized_intent = {
        "title_hints": title_hints,
        "media_type_hint": media_type,
        "year_hint": year,
        "scope": scope,
        "season_number": _validate_optional_number(intent.get("season_number")),
        "episode_number": _validate_optional_number(intent.get("episode_number")),
    }
    query_keys = {"wikipedia_zh", "wikipedia_en", "douban", "tvdb"}
    if set(queries) != query_keys:
        raise ToolValidationError("tool_arguments_invalid")
    normalized_queries = {
        key: _validate_query_list(queries.get(key))
        for key in sorted(query_keys)
    }
    return normalized_intent, normalized_queries


def _unique(values) -> list[str]:
    result = []
    for value in values:
        value = _text(value)
        if value and value not in result:
            result.append(value)
    return result


def _sanitize_result(value, *, depth: int = 0):
    if depth > 8:
        return None
    if isinstance(value, dict):
        result = {}
        for raw_key, nested in value.items():
            key = _text(raw_key)
            if not key or key.casefold() in _REDACTED_RESULT_KEYS:
                continue
            sanitized = _sanitize_result(nested, depth=depth + 1)
            if sanitized is not None:
                result[key] = sanitized
        return result
    if isinstance(value, (list, tuple)):
        return [
            sanitized
            for nested in list(value)[:100]
            if (sanitized := _sanitize_result(nested, depth=depth + 1))
            is not None
        ]
    if isinstance(value, str):
        return value[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _text(value)[:500]


def _credential_state(source: str, config: dict) -> str:
    if source != "tvdb":
        return "not_required"
    tvdb = ((config.get("metadata") or {}).get("tvdb") or {})
    return "configured" if _text(tvdb.get("api_key")) else "missing"


def _error_code(status: str, result: dict) -> str:
    explicit = _text(result.get("error_code"))
    if explicit:
        return explicit
    return {
        "credential_missing": "credential_missing",
        "authentication_failed": "source_authentication_failed",
        "timeout": "source_timeout",
        "rate_limited": "source_rate_limited",
        "blocked": "source_blocked",
        "server_down": "source_server_down",
    }.get(status, "")


def _normalize_source_result(
    source: str,
    result,
    *,
    queries: list[str],
    config: dict,
) -> dict:
    if not isinstance(result, dict):
        result = {"status": "server_down", "facts": []}
    status = _text(result.get("status")).casefold()
    if status not in SOURCE_STATUSES:
        status = "server_down"
    facts = result.get("facts")
    if not isinstance(facts, list):
        facts = []
    source_urls = result.get("source_urls")
    if not isinstance(source_urls, list):
        source_urls = []
    normalized = {
        "source": source,
        "status": status,
        "query_summaries": list(queries),
        "facts": _sanitize_result(facts) or [],
        "source_urls": _sanitize_result(source_urls) or [],
        "error_code": _error_code(status, result),
        "credential_state": _credential_state(source, config),
    }
    return normalized


def _provider_payload(
    raw_query: str,
    intent: dict,
    source_queries: dict,
    source: str,
) -> dict:
    if source == "wikipedia":
        queries = _unique(
            source_queries["wikipedia_zh"] + source_queries["wikipedia_en"]
        )
    else:
        queries = list(source_queries[source])
    if not queries:
        queries = list(intent["title_hints"]) or [_text(raw_query)]
    mapped_scope = {
        "work": "movie_or_series",
        "unknown": "movie_or_series",
    }.get(intent["scope"], intent["scope"])
    hypotheses = [{
        "title": query,
        "year": intent["year_hint"],
        "content_identity": intent["media_type_hint"],
        "scope": mapped_scope,
        "season_number": intent["season_number"],
        "episode_number": intent["episode_number"],
        "explicit_facts": [],
        "inferred_facts": ["ai_tool_intent"],
    } for query in queries]
    return {
        "status": "ok",
        "raw_query": _text(raw_query),
        "intent": {
            "title": intent["title_hints"][0],
            "year": intent["year_hint"],
            "media_type": intent["media_type_hint"],
            "scope": mapped_scope,
            "season_number": intent["season_number"],
            "episode_number": intent["episode_number"],
        },
        "hypotheses": hypotheses,
        "source_queries": {
            **deepcopy(source_queries),
            "wikipedia": _unique(
                source_queries["wikipedia_zh"]
                + source_queries["wikipedia_en"]
            ),
        },
        "warnings": ["ai_tool_intent_requires_source_verification"],
    }


async def _call_handler(handler: Callable, payload):
    if inspect.iscoroutinefunction(handler):
        return await handler(payload)
    return await asyncio.to_thread(handler, payload)


class SourceToolGateway:
    """Execute model-selected source tools without exposing runtime secrets."""

    def __init__(
        self,
        providers: dict[str, Callable],
        *,
        targeted_handlers: dict[str, Callable] | None = None,
        config: dict | None = None,
        logger=None,
    ):
        self.providers = dict(providers or {})
        self.targeted_handlers = dict(targeted_handlers or {})
        self.config = config or {}
        self.logger = logger

    async def search_media_sources(
        self,
        raw_query: str,
        arguments: dict,
    ) -> dict:
        intent, source_queries = _validate_first_arguments(arguments)
        names = ("wikipedia", "douban", "tvdb")
        payloads = {
            name: _provider_payload(
                raw_query,
                intent,
                source_queries,
                name,
            )
            for name in names
        }
        tasks = []
        for name in names:
            handler = self.providers.get(name)
            if handler is None:
                tasks.append(asyncio.sleep(0, result={
                    "source": name,
                    "status": "disabled",
                    "facts": [],
                }))
            else:
                tasks.append(_call_handler(handler, payloads[name]))
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        sources = []
        for name, result in zip(names, raw_results):
            if isinstance(result, Exception):
                result = {
                    "source": name,
                    "status": "server_down",
                    "facts": [],
                    "error_code": "source_server_down",
                }
            if name == "wikipedia":
                queries = _unique(
                    source_queries["wikipedia_zh"]
                    + source_queries["wikipedia_en"]
                )
            else:
                queries = source_queries[name]
            sources.append(_normalize_source_result(
                name,
                result,
                queries=queries,
                config=self.config,
            ))
        if self.logger:
            self.logger.info(
                "source_tool round=1 "
                + " ".join(
                    f"{item['source']}={item['status']}:{len(item['facts'])}"
                    for item in sources
                )
            )
        return {
            "round": 1,
            "raw_query": _text(raw_query),
            "intent": intent,
            "sources": sources,
        }

    async def execute_targeted(
        self,
        name: str,
        arguments: dict,
        *,
        known_facts: dict,
    ) -> dict:
        name = _text(name)
        if name not in _TARGETED_BY_NAME:
            raise ToolValidationError("tool_not_registered")
        if not isinstance(arguments, dict):
            raise ToolValidationError("tool_arguments_invalid")
        _validate_no_sensitive_arguments(arguments)
        fact_ids = arguments.get("fact_ids")
        if not isinstance(fact_ids, list):
            raise ToolValidationError("tool_fact_reference_invalid")
        fact_ids = _validate_query_list(fact_ids)
        if not set(fact_ids).issubset(set(known_facts or {})):
            raise ToolValidationError("tool_fact_reference_invalid")
        self._validate_targeted_shape(name, arguments)
        handler = self.targeted_handlers.get(name)
        if handler is None:
            source = self._targeted_source(name)
            raw = {"source": source, "status": "disabled", "facts": []}
        else:
            try:
                raw = await _call_handler(handler, deepcopy(arguments))
            except Exception:
                source = self._targeted_source(name)
                raw = {
                    "source": source,
                    "status": "server_down",
                    "facts": [],
                    "error_code": "source_server_down",
                }
        raw_items = raw if isinstance(raw, list) else [raw]
        sources = [
            _normalize_source_result(
                _text(item.get("source")) or self._targeted_source(name),
                item,
                queries=self._targeted_query_summaries(name, arguments),
                config=self.config,
            )
            for item in raw_items
            if isinstance(item, dict)
        ]
        return {"tool": name, "sources": sources}

    @staticmethod
    def _targeted_source(name: str) -> str:
        if "wikipedia" in name:
            return "wikipedia"
        if "douban" in name:
            return "douban"
        return "tvdb"

    @staticmethod
    def _targeted_query_summaries(name: str, arguments: dict) -> list[str]:
        if name == "lookup_wikipedia_entity":
            return _validate_query_list(arguments.get("queries"), allow_empty=False)
        if name == "lookup_douban_subject":
            return _validate_query_list(arguments.get("subject_ids"), allow_empty=False)
        if name == "lookup_tvdb_episodes":
            return _validate_query_list(arguments.get("series_ids"), allow_empty=False)
        return _unique(
            item.get("title") or item.get("tvdb_id")
            for key in ("queries", "entity_ids")
            for item in (arguments.get(key) or [])
            if isinstance(item, dict)
        )

    @staticmethod
    def _validate_targeted_shape(name: str, arguments: dict) -> None:
        expected = {
            "lookup_wikipedia_entity": {"fact_ids", "queries"},
            "lookup_douban_subject": {"fact_ids", "subject_ids"},
            "lookup_tvdb_entity": {
                "fact_ids",
                "queries",
                "entity_ids",
            },
            "lookup_tvdb_episodes": {"fact_ids", "series_ids"},
        }[name]
        if set(arguments) != expected:
            raise ToolValidationError("tool_arguments_invalid")
        if name == "lookup_wikipedia_entity":
            _validate_query_list(arguments["queries"], allow_empty=False)
        elif name == "lookup_douban_subject":
            values = _validate_query_list(
                arguments["subject_ids"],
                allow_empty=False,
            )
            if any(not item.isdigit() for item in values):
                raise ToolValidationError("tool_stable_id_invalid")
        elif name == "lookup_tvdb_episodes":
            _validate_query_list(arguments["series_ids"], allow_empty=False)
        else:
            queries = arguments["queries"]
            entities = arguments["entity_ids"]
            if not isinstance(queries, list) or not isinstance(entities, list):
                raise ToolValidationError("tool_arguments_invalid")
            if (
                len(queries) > MAX_TARGETS_PER_TOOL
                or len(entities) > MAX_TARGETS_PER_TOOL
                or not (queries or entities)
            ):
                raise ToolValidationError("tool_target_limit_exceeded")
            for item in queries:
                if not isinstance(item, dict) or set(item) != {
                    "title",
                    "year",
                    "media_type",
                }:
                    raise ToolValidationError("tool_arguments_invalid")
                _validate_query_list([item["title"]], allow_empty=False)
                if _text(item["media_type"]).casefold() not in {
                    "movie",
                    "series",
                    "unknown",
                }:
                    raise ToolValidationError("tool_arguments_invalid")
            for item in entities:
                if not isinstance(item, dict) or set(item) != {
                    "tvdb_id",
                    "media_type",
                }:
                    raise ToolValidationError("tool_arguments_invalid")
                if not _text(item["tvdb_id"]):
                    raise ToolValidationError("tool_stable_id_invalid")
                if _text(item["media_type"]).casefold() not in {
                    "movie",
                    "series",
                }:
                    raise ToolValidationError("tool_arguments_invalid")
