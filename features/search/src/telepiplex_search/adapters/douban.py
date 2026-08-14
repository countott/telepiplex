"""No-key Douban evidence adapter owned by search."""

from __future__ import annotations

import html
import re
import threading
import time
import unicodedata
from copy import deepcopy
from urllib.parse import unquote, unquote_plus

import requests


USER_AGENT = "telepiplex/1.0 (media metadata lookup)"
_SUBJECT_PATTERN = re.compile(
    r"(?:https?:)?//movie\.douban\.com/subject/(\d+)/?|(?<![\w/])/subject/(\d+)/?"
)
_IMDB_PATTERN = re.compile(r"\btt\d{5,12}\b", re.IGNORECASE)
_CHINESE_NUMBER_VALUES = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_QUERY_CACHE: dict[tuple[str, ...], tuple[float, dict]] = {}
_SUBJECT_CACHE: dict[str, tuple[float, dict]] = {}
_CIRCUIT_STATE = {"failures": 0, "open_until": 0.0}
_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}
_STATE_LOCK = threading.Lock()


class DoubanSubjectLookupError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code or "server_down")
        super().__init__(self.code)


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _clean_title_text(value) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    visible = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )
    return _text(visible)


def _positive_number(value: str) -> int | None:
    value = _text(value)
    if value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    if value == "十":
        return 10
    if "十" in value and value.count("十") == 1:
        left, right = value.split("十")
        tens = 1 if not left else _CHINESE_NUMBER_VALUES.get(left)
        ones = 0 if not right else _CHINESE_NUMBER_VALUES.get(right)
        if tens is not None and ones is not None:
            parsed = tens * 10 + ones
            return parsed if parsed > 0 else None
    if len(value) == 1:
        parsed = _CHINESE_NUMBER_VALUES.get(value)
        return parsed if parsed and parsed > 0 else None
    return None


def clean_douban_series_title(
    title: str,
    media_type: str,
) -> tuple[str, int | None]:
    value = _clean_title_text(title)
    if _text(media_type).casefold() != "series" or not value:
        return value, None
    patterns = (
        re.compile(r"\s*第\s*([0-9零〇一二三四五六七八九十]+)\s*季\s*$"),
        re.compile(r"(?:\s+|[-:：])Season\s*0*(\d+)\s*$", re.IGNORECASE),
        re.compile(r"(?:\s+|[-:：])S0*(\d+)\s*$", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.search(value)
        if match is None:
            continue
        season_number = _positive_number(match.group(1))
        root = value[:match.start()].rstrip(" \t/／|｜·・-–—:：")
        if root and season_number is not None:
            return root, season_number
    return value, None


def _imdb_id(value) -> str:
    if isinstance(value, dict):
        preferred = (
            "imdb",
            "imdb_id",
            "imdbId",
            "imdbID",
        )
        for key in preferred:
            if key in value and (result := _imdb_id(value.get(key))):
                return result
        for nested in value.values():
            if result := _imdb_id(nested):
                return result
        return ""
    if isinstance(value, (list, tuple)):
        for nested in value:
            if result := _imdb_id(nested):
                return result
        return ""
    match = _IMDB_PATTERN.search(str(value or ""))
    return match.group(0).casefold() if match else ""


def _normalize_title_and_year(
    title: str,
    year_value: object,
) -> tuple[str, str]:
    normalized_title = _clean_title_text(title)
    normalized_year = _text(year_value)
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", normalized_year)
    trailing_match = re.search(
        r"\s*[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]\s*$",
        normalized_title,
    )
    if trailing_match:
        normalized_title = normalized_title[:trailing_match.start()].rstrip()
        if year_match is None:
            normalized_year = trailing_match.group(1)
            year_match = re.search(r"\b(19\d{2}|20\d{2})\b", normalized_year)
    return normalized_title, year_match.group(1) if year_match else ""


def _result(
    status: str,
    facts: list[dict] | None = None,
    source_urls: list[str] | None = None,
    error: str = "",
) -> dict:
    return {
        "source": "douban",
        "status": status,
        "facts": list(facts or []),
        "source_urls": list(source_urls or []),
        "error": _text(error),
    }


def _headers(referer: str = "") -> dict:
    result = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        result["Referer"] = referer
    return result


def _cache_get(cache: dict, key, ttl: float):
    if ttl <= 0:
        return None
    cached = cache.get(key)
    if not cached:
        return None
    created_at, value = cached
    if time.monotonic() - created_at >= ttl:
        cache.pop(key, None)
        return None
    return deepcopy(value)


def _cache_put(cache: dict, key, value) -> None:
    cache[key] = (time.monotonic(), deepcopy(value))


def _semaphore(max_concurrency: int) -> threading.BoundedSemaphore:
    limit = max(1, int(max_concurrency or 1))
    with _STATE_LOCK:
        return _SEMAPHORES.setdefault(
            limit,
            threading.BoundedSemaphore(limit),
        )


def _circuit_is_open() -> bool:
    return float(_CIRCUIT_STATE.get("open_until") or 0) > time.monotonic()


def _record_success() -> None:
    with _STATE_LOCK:
        _CIRCUIT_STATE.update({"failures": 0, "open_until": 0.0})


def _record_failure(
    *,
    threshold: int,
    seconds: float,
) -> None:
    with _STATE_LOCK:
        failures = int(_CIRCUIT_STATE.get("failures") or 0) + 1
        _CIRCUIT_STATE["failures"] = failures
        if failures >= max(1, int(threshold or 1)):
            _CIRCUIT_STATE["open_until"] = (
                time.monotonic() + max(1.0, float(seconds or 1))
            )


def _exception_status(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "timeout"
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code == 404:
        return "not_found"
    if status_code == 403:
        return "blocked"
    if status_code == 429:
        return "rate_limited"
    return "server_down"


def _failure_status(errors: list[tuple[str, str]]) -> str:
    statuses = {status for status, _error in errors}
    for status in (
        "rate_limited",
        "blocked",
        "timeout",
        "server_down",
        "not_found",
    ):
        if status in statuses:
            return status
    return "server_down"


def _error_text(errors: list[tuple[str, str]]) -> str:
    return "; ".join(
        error for _status, error in errors if _text(error)
    )


def _subject_urls(value: str) -> list[str]:
    decoded = html.unescape(str(value or ""))
    decoded = unquote_plus(unquote(decoded.replace("\\/", "/")))
    urls = []
    seen = set()
    for match in _SUBJECT_PATTERN.finditer(decoded):
        subject_id = match.group(1) or match.group(2)
        if not subject_id or subject_id in seen:
            continue
        seen.add(subject_id)
        urls.append(f"https://movie.douban.com/subject/{subject_id}/")
    return urls


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def _contains_latin(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]", value))


def _contains_japanese_script(value: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff]", value))


def _chinese_title_part(title: str, original_title: str) -> str:
    """Keep the provider's Chinese display title without a trailing foreign title."""

    value = _clean_title_text(title)
    original = _clean_title_text(original_title)
    if not _contains_cjk(value):
        return ""

    if original and value != original and value.endswith(original):
        prefix = value[:-len(original)].rstrip(" \t/／|｜·・-–—:：")
        if _contains_cjk(prefix):
            return prefix

    for match in re.finditer(r"\S+", value):
        if match.start() == 0:
            continue
        token = match.group(0)
        prefix = value[:match.start()].rstrip()
        if (
            _contains_cjk(prefix)
            and (_contains_latin(token) or _contains_japanese_script(token))
        ):
            return prefix.rstrip(" \t/／|｜·・-–—:：")
    return value


def _language(data: dict, original_title: str) -> str:
    value = _text(
        data.get("original_language")
        or data.get("originalLanguage")
        or data.get("language")
    ).casefold()
    if value in {"ja", "jpn", "japanese", "日语", "日語"}:
        return "ja"
    if _contains_japanese_script(original_title):
        return "ja"
    return value


def _list_values(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = re.split(r"\s*/\s*|\s*[、,]\s*", value)
    else:
        raw = []
    return [text for item in raw if (text := _text(item))]


def _media_type(data: dict) -> str:
    values = {
        _text(data.get("type")).casefold(),
        _text(data.get("subtype")).casefold(),
    }
    if data.get("is_tv") or values.intersection({"tv", "series", "tv_series", "show"}):
        return "series"
    if values.intersection({"movie", "film"}):
        return "movie"
    return ""


def _cover_url(data: dict) -> str:
    direct = _text(data.get("cover_url"))
    if direct:
        return direct
    for container_name in ("pic", "cover"):
        container = data.get(container_name)
        if isinstance(container, str) and _text(container):
            return _text(container)
        if not isinstance(container, dict):
            continue
        for key in ("large", "normal", "small", "url"):
            if _text(container.get(key)):
                return _text(container[key])
    return ""


def _normalize_payload(payload: dict, subject_url: str) -> dict | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("subject") if isinstance(payload.get("subject"), dict) else payload
    if not isinstance(data, dict):
        return None

    subject_id = _text(data.get("id") or data.get("subject_id"))
    url_id = next(iter(re.findall(r"/subject/(\d+)", subject_url)), "")
    if not subject_id:
        subject_id = url_id
    if not subject_id or (url_id and subject_id != url_id):
        return None

    title, normalized_year = _normalize_title_and_year(
        data.get("title") or data.get("name"),
        data.get("release_year") or data.get("year"),
    )
    original_title, _unused_original_year = _normalize_title_and_year(
        data.get("original_title")
        or data.get("originalTitle")
        or data.get("original_name")
        or data.get("originalName"),
        "",
    )
    douban_title_raw = _chinese_title_part(title, original_title)
    chinese_title, season_number = clean_douban_series_title(
        douban_title_raw,
        _media_type(data),
    )
    candidates = [
        original_title,
        data.get("originalTitle"),
        data.get("original_name"),
        data.get("originalName"),
        data.get("foreign_title"),
        data.get("foreignTitle"),
    ]
    for key in ("aka", "aka_titles", "aliases", "alias"):
        candidates.extend(_list_values(data.get(key)))
    if title and not chinese_title:
        candidates.append(title)
    english_title = next(
        (_text(item) for item in candidates if _contains_latin(_text(item))),
        "",
    )
    aliases = []
    for item in candidates:
        item, _unused_alias_year = _normalize_title_and_year(item, "")
        if item and item not in aliases:
            aliases.append(item)
    genres = _list_values(data.get("genres") or data.get("genre"))
    countries = _list_values(
        data.get("countries")
        or data.get("country")
        or data.get("regions")
        or data.get("region")
    )
    official_english_title, _unused_official_year = _normalize_title_and_year(
        data.get("official_english_title")
        or data.get("officialEnglishTitle")
        or english_title,
        "",
    )
    romanized_original_title, _unused_romanized_year = _normalize_title_and_year(
        data.get("romanized_original_title")
        or data.get("romanizedOriginalTitle")
        or data.get("romaji_title")
        or data.get("romajiTitle"),
        "",
    )
    external_ids = {"douban_subject": subject_id}
    imdb_id = _imdb_id(data)
    if imdb_id:
        external_ids["imdb"] = imdb_id
    return {
        "subject_id": subject_id,
        "external_ids": external_ids,
        "url": f"https://movie.douban.com/subject/{subject_id}/",
        "title": english_title or chinese_title or title,
        "douban_title_raw": douban_title_raw,
        "chinese_title": chinese_title,
        "season_number": season_number,
        "english_title": english_title,
        "original_title": original_title,
        "original_language": _language(data, original_title),
        "official_english_title": official_english_title,
        "romanized_original_title": romanized_original_title,
        "year": normalized_year,
        "media_type": _media_type(data),
        "aliases": aliases,
        "genres": genres,
        "countries": countries,
        "cover_url": _cover_url(data),
        "summary": _text(
            data.get("summary")
            or data.get("intro")
            or data.get("description")
        ),
    }


def _merge_subject_facts(facts: list[dict]) -> dict | None:
    valid = [deepcopy(item) for item in facts if isinstance(item, dict)]
    if not valid:
        return None
    subject_ids = {_text(item.get("subject_id")) for item in valid}
    if len(subject_ids) != 1 or "" in subject_ids:
        return None

    merged = valid[0]
    conflicts = []
    scalar_fields = (
        "title",
        "douban_title_raw",
        "chinese_title",
        "english_title",
        "original_title",
        "original_language",
        "official_english_title",
        "romanized_original_title",
        "cover_url",
        "summary",
    )
    list_fields = ("aliases", "genres", "countries")
    for fact in valid[1:]:
        for field in scalar_fields:
            if not _text(merged.get(field)) and _text(fact.get(field)):
                merged[field] = fact[field]
        for field in list_fields:
            values = []
            for value in list(merged.get(field) or []) + list(fact.get(field) or []):
                value = _text(value)
                if value and value not in values:
                    values.append(value)
            merged[field] = values
        for field in ("year", "media_type", "season_number"):
            current = _text(merged.get(field))
            incoming = _text(fact.get(field))
            if current and incoming and current != incoming:
                conflicts.append(field)
            elif not current and incoming:
                merged[field] = fact[field]
        external_ids = dict(merged.get("external_ids") or {})
        external_ids.update(dict(fact.get("external_ids") or {}))
        merged["external_ids"] = external_ids
    if conflicts:
        merged["identity_conflicts"] = sorted(set(conflicts))
    merged_chinese_title = _chinese_title_part(
        _text(merged.get("chinese_title") or merged.get("title")),
        _text(merged.get("original_title")),
    )
    if merged_chinese_title:
        merged["chinese_title"] = merged_chinese_title
    merged["title"] = (
        _text(merged.get("english_title"))
        or merged_chinese_title
        or _text(merged.get("title"))
    )
    return merged


def _fetch_subject(
    subject_url: str,
    timeout: float,
    errors: list[tuple[str, str]],
) -> dict | None:
    subject_id = next(iter(re.findall(r"/subject/(\d+)", subject_url)), "")
    attempts = (
        (
            f"https://movie.douban.com/j/subject_abstract?subject_id={subject_id}",
            subject_url,
        ),
        (
            f"https://m.douban.com/rexxar/api/v2/movie/{subject_id}",
            f"https://m.douban.com/movie/subject/{subject_id}/",
        ),
    )
    facts = []
    for endpoint, referer in attempts:
        try:
            response = requests.get(
                endpoint,
                headers={**_headers(referer), "Accept": "application/json, text/plain, */*"},
                timeout=timeout,
            )
            response.raise_for_status()
            fact = _normalize_payload(response.json(), subject_url)
        except Exception as exc:
            errors.append((_exception_status(exc), str(exc)))
            continue
        if fact:
            facts.append(fact)
    return _merge_subject_facts(facts)


def lookup_douban_subject(
    subject_id: str,
    timeout: float = 10,
    *,
    cache_ttl: float = 900,
    max_concurrency: int = 2,
) -> dict | None:
    subject_id = _text(subject_id)
    if not subject_id.isdigit():
        return None
    cached = _cache_get(_SUBJECT_CACHE, subject_id, float(cache_ttl or 0))
    if cached is not None:
        return cached
    if _circuit_is_open():
        raise DoubanSubjectLookupError("blocked")
    errors = []
    with _semaphore(max_concurrency):
        result = _fetch_subject(
            f"https://movie.douban.com/subject/{subject_id}/",
            timeout,
            errors,
        )
    if result:
        _cache_put(_SUBJECT_CACHE, subject_id, result)
    elif errors:
        status = _failure_status(errors)
        if status != "not_found":
            raise DoubanSubjectLookupError(status)
    return result


def lookup_douban_evidence(
    queries: list[str],
    timeout: float = 10,
    *,
    cache_ttl: float = 900,
    max_concurrency: int = 2,
    circuit_breaker_failures: int = 3,
    circuit_breaker_seconds: float = 300,
) -> dict:
    cleaned = []
    for item in queries or []:
        item = _text(item)
        if item and item not in cleaned:
            cleaned.append(item)
    if not cleaned:
        return _result("unavailable", error="source_queries_empty")
    if _circuit_is_open():
        return _result("blocked", error="douban circuit open")
    cache_key = tuple(item.casefold() for item in cleaned)
    cached = _cache_get(_QUERY_CACHE, cache_key, float(cache_ttl or 0))
    if cached is not None:
        return cached

    urls = []
    errors: list[tuple[str, str]] = []
    successful_searches = 0
    with _semaphore(max_concurrency):
        for query in cleaned:
            try:
                response = requests.get(
                    "https://www.douban.com/search",
                    params={"cat": "1002", "q": query},
                    headers=_headers("https://www.douban.com/"),
                    timeout=timeout,
                )
                response.raise_for_status()
                successful_searches += 1
            except Exception as exc:
                errors.append((_exception_status(exc), str(exc)))
                continue
            for url in _subject_urls(response.text):
                if url not in urls:
                    urls.append(url)

        facts = []
        for url in urls:
            subject_id = next(iter(re.findall(r"/subject/(\d+)", url)), "")
            fact = _cache_get(
                _SUBJECT_CACHE,
                subject_id,
                float(cache_ttl or 0),
            )
            if fact is None:
                fact = _fetch_subject(url, timeout, errors)
                if fact and subject_id:
                    _cache_put(_SUBJECT_CACHE, subject_id, fact)
            if fact:
                facts.append(fact)
    if facts:
        result = _result("ok", facts, [item["url"] for item in facts])
        _record_success()
        _cache_put(_QUERY_CACHE, cache_key, result)
        return result
    if successful_searches and not urls:
        result = _result("not_found")
        _record_success()
        _cache_put(_QUERY_CACHE, cache_key, result)
        return result
    status = _failure_status(errors)
    _record_failure(
        threshold=circuit_breaker_failures,
        seconds=circuit_breaker_seconds,
    )
    return _result(status, error=_error_text(errors))
