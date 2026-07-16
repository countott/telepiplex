"""No-key Douban evidence adapter owned by media-search."""

from __future__ import annotations

import html
import re
from urllib.parse import unquote, unquote_plus

import requests


USER_AGENT = "Telepiplex/1.0 (media metadata lookup)"
_SUBJECT_PATTERN = re.compile(
    r"(?:https?:)?//movie\.douban\.com/subject/(\d+)/?|(?<![\w/])/subject/(\d+)/?"
)


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


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

    title = _text(data.get("title") or data.get("name"))
    chinese_title = title if _contains_cjk(title) else ""
    original_title = _text(
        data.get("original_title")
        or data.get("originalTitle")
        or data.get("original_name")
        or data.get("originalName")
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
        item = _text(item)
        if item and item not in aliases:
            aliases.append(item)
    genres = _list_values(data.get("genres") or data.get("genre"))
    official_english_title = _text(
        data.get("official_english_title")
        or data.get("officialEnglishTitle")
        or english_title
    )
    romanized_original_title = _text(
        data.get("romanized_original_title")
        or data.get("romanizedOriginalTitle")
        or data.get("romaji_title")
        or data.get("romajiTitle")
    )
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", _text(data.get("release_year") or data.get("year")))
    return {
        "subject_id": subject_id,
        "external_ids": {"douban_subject": subject_id},
        "url": f"https://movie.douban.com/subject/{subject_id}/",
        "title": english_title or chinese_title or title,
        "chinese_title": chinese_title,
        "english_title": english_title,
        "original_title": original_title,
        "original_language": _language(data, original_title),
        "official_english_title": official_english_title,
        "romanized_original_title": romanized_original_title,
        "year": year_match.group(1) if year_match else "",
        "media_type": _media_type(data),
        "aliases": aliases,
        "genres": genres,
        "cover_url": _cover_url(data),
    }


def _fetch_subject(subject_url: str, timeout: float, errors: list[str]) -> dict | None:
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
            errors.append(str(exc))
            continue
        if fact:
            return fact
    return None


def lookup_douban_evidence(
    queries: list[str],
    timeout: float = 10,
) -> dict:
    cleaned = []
    for item in queries or []:
        item = _text(item)
        if item and item not in cleaned:
            cleaned.append(item)
    if not cleaned:
        return _result("not_found")

    urls = []
    errors = []
    successful_searches = 0
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
            errors.append(str(exc))
            continue
        for url in _subject_urls(response.text):
            if url not in urls:
                urls.append(url)

    facts = []
    for url in urls:
        fact = _fetch_subject(url, timeout, errors)
        if fact:
            facts.append(fact)
    if facts:
        return _result("ok", facts, [item["url"] for item in facts])
    if successful_searches and not urls:
        return _result("not_found")
    return _result("server_down", error="; ".join(item for item in errors if item))
