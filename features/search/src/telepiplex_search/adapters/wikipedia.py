"""Wikipedia evidence adapter."""

from __future__ import annotations

import re
import threading
import time
from urllib.parse import quote

import requests


USER_AGENT = "telepiplex/1.0 (media metadata lookup)"
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_STATE = {
    "last_request_at": 0.0,
    "limited_until": 0.0,
}


class WikipediaRateLimited(RuntimeError):
    pass


class WikipediaPageLookupError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code or "server_down")
        super().__init__(self.code)


def _retry_after_seconds(response, fallback: float) -> float:
    headers = getattr(response, "headers", None)
    raw = (
        headers.get("Retry-After")
        if hasattr(headers, "get")
        else ""
    )
    try:
        return max(float(raw), float(fallback))
    except (TypeError, ValueError):
        return max(0.0, float(fallback))


def _get(
    endpoint: str,
    *,
    params: dict,
    headers: dict,
    timeout: float,
    min_interval: float,
    rate_limit_cooldown: float,
):
    with _RATE_LIMIT_LOCK:
        now = time.monotonic()
        if now < float(_RATE_LIMIT_STATE["limited_until"]):
            raise WikipediaRateLimited("wikipedia rate-limit circuit open")
        interval = max(0.0, float(min_interval))
        wait = interval - (
            now - float(_RATE_LIMIT_STATE["last_request_at"])
        )
        if wait > 0:
            time.sleep(wait)
        response = requests.get(
            endpoint,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        now = time.monotonic()
        _RATE_LIMIT_STATE["last_request_at"] = now
        if getattr(response, "status_code", None) == 429:
            cooldown = _retry_after_seconds(
                response,
                rate_limit_cooldown,
            )
            if cooldown > 0:
                _RATE_LIMIT_STATE["limited_until"] = now + cooldown
        return response


def _classification(title: str, extract: str) -> tuple[str, str]:
    text = f"{title} {extract}"
    numeric_title = re.match(
        r"^\s*((?:19|20)\d{2})(?=\s|$|[\(（])",
        title,
    )
    year_text = text
    if numeric_title:
        start, end = numeric_title.span(1)
        year_text = f"{title[:start]} {title[end:]} {extract}"
    year_match = re.search(
        r"(?<!\d)(19\d{2}|20\d{2})(?!\d)",
        year_text,
    )
    lowered = text.casefold()
    lowered_title = title.casefold()
    series_signals = (
        "television series",
        "tv series",
        "television anime",
        "anime television",
        "電視動畫",
        "电视动画",
        "電視劇",
        "电视剧",
        "劇集",
        "剧集",
    )
    movie_signals = (" film", "movie", "電影", "电影", "影片")
    title_is_movie = any(
        item in lowered_title for item in movie_signals
    )
    title_is_series = any(
        item in lowered_title for item in series_signals
    )
    if title_is_movie != title_is_series:
        media_type = "movie" if title_is_movie else "series"
    else:
        is_series = any(item in lowered for item in series_signals)
        is_movie = any(item in lowered for item in movie_signals)
        media_type = (
            "series"
            if is_series and not is_movie
            else "movie"
            if is_movie and not is_series
            else ""
        )
    return (year_match.group(1) if year_match else "", media_type)


def _empty(status: str, error: str = "") -> dict:
    return {
        "source": "wikipedia",
        "status": status,
        "facts": [],
        "source_urls": [],
        "error": str(error or ""),
    }


def _exception_status(exc: Exception) -> str:
    if isinstance(exc, WikipediaRateLimited):
        return "rate_limited"
    if isinstance(exc, requests.Timeout):
        return "timeout"
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "rate_limited"
    return "server_down"


def lookup_wikipedia_evidence(
    queries: list[str],
    languages: tuple[str, ...] = ("zh", "en"),
    timeout: float = 10,
    *,
    min_interval: float = 0,
    rate_limit_cooldown: float = 0,
) -> dict:
    cleaned_queries = [" ".join(str(item or "").split()) for item in queries]
    cleaned_queries = [item for item in cleaned_queries if item]
    if not cleaned_queries:
        return _empty("unavailable", "source_queries_empty")

    facts = []
    urls = []
    errors = []
    rate_limited = False
    successful_requests = 0
    for language in languages:
        endpoint = f"https://{language}.wikipedia.org/w/api.php"
        for query in cleaned_queries:
            try:
                response = _get(
                    endpoint,
                    params={
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": query,
                        "gsrlimit": 5,
                        "prop": "extracts|pageprops|info",
                        "exintro": 1,
                        "explaintext": 1,
                        "inprop": "url",
                        "format": "json",
                        "formatversion": 2,
                    },
                    headers={"User-Agent": USER_AGENT},
                    timeout=timeout,
                    min_interval=min_interval,
                    rate_limit_cooldown=rate_limit_cooldown,
                )
                response.raise_for_status()
                payload = response.json()
                successful_requests += 1
            except Exception as exc:
                errors.append(str(exc))
                response = getattr(exc, "response", None)
                if (
                    isinstance(exc, WikipediaRateLimited)
                    or getattr(response, "status_code", None) == 429
                ):
                    rate_limited = True
                continue

            pages = ((payload or {}).get("query") or {}).get("pages") or []
            if isinstance(pages, dict):
                pages = list(pages.values())
            for page in pages:
                if not isinstance(page, dict):
                    continue
                title = " ".join(str(page.get("title") or "").split())
                extract = " ".join(str(page.get("extract") or "").split())
                if not title or not extract:
                    continue
                page_url = str(page.get("fullurl") or "").strip()
                if not page_url:
                    page_url = (
                        f"https://{language}.wikipedia.org/wiki/"
                        f"{quote(title.replace(' ', '_'))}"
                    )
                year, media_type = _classification(title, extract)
                facts.append(
                    {
                        "language": language,
                        "query": query,
                        "title": title,
                        "extract": extract,
                        "url": page_url,
                        "wikibase_item": str(
                            (page.get("pageprops") or {}).get("wikibase_item") or ""
                        ),
                        "year": year,
                        "media_type": media_type,
                        "chinese_title": title if language.startswith("zh") else "",
                        "english_title": title if language.startswith("en") else "",
                    }
                )
                if page_url not in urls:
                    urls.append(page_url)
    if facts:
        return {
            "source": "wikipedia",
            "status": "ok",
            "facts": facts,
            "source_urls": urls,
            "error": "",
        }
    if successful_requests:
        return _empty("not_found")
    if rate_limited:
        return _empty(
            "rate_limited",
            "; ".join(item for item in errors if item),
        )
    return _empty("server_down", "; ".join(item for item in errors if item))


def lookup_wikipedia_page(
    language: str,
    title: str,
    *,
    timeout: float = 10,
) -> dict | None:
    """Read one exact Wikipedia article without running another search."""

    language = re.sub(r"[^a-z0-9-]", "", str(language or "").casefold())
    title = " ".join(str(title or "").replace("_", " ").split())
    if not language or not title:
        return None
    endpoint = f"https://{language}.wikipedia.org/w/api.php"
    try:
        response = _get(
            endpoint,
            params={
                "action": "query",
                "titles": title,
                "redirects": 1,
                "prop": "extracts|pageprops|info|pageimages",
                "exintro": 1,
                "explaintext": 1,
                "inprop": "url",
                "piprop": "original|thumbnail",
                "pithumbsize": 1000,
                "format": "json",
                "formatversion": 2,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            min_interval=0,
            rate_limit_cooldown=30,
        )
        response.raise_for_status()
        pages = ((response.json() or {}).get("query") or {}).get("pages") or []
    except Exception as exc:
        status = _exception_status(exc)
        if status == "not_found":
            return None
        raise WikipediaPageLookupError(status) from exc
    if isinstance(pages, dict):
        pages = list(pages.values())
    page = next(
        (
            item for item in pages
            if isinstance(item, dict) and not item.get("missing")
        ),
        None,
    )
    if page is None:
        return None
    resolved_title = " ".join(str(page.get("title") or title).split())
    extract = " ".join(str(page.get("extract") or "").split())
    year, media_type = _classification(resolved_title, extract)
    page_url = str(page.get("fullurl") or "").strip() or (
        f"https://{language}.wikipedia.org/wiki/"
        f"{quote(resolved_title.replace(' ', '_'))}"
    )
    image = page.get("original") or page.get("thumbnail") or {}
    poster_url = (
        str(image.get("source") or "").strip()
        if isinstance(image, dict)
        else ""
    )
    return {
        "language": language,
        "title": resolved_title,
        "extract": extract,
        "url": page_url,
        "wikibase_item": str(
            (page.get("pageprops") or {}).get("wikibase_item") or ""
        ),
        "year": year,
        "media_type": media_type,
        "chinese_title": resolved_title if language.startswith("zh") else "",
        "english_title": resolved_title if language.startswith("en") else "",
        "official_english_title": (
            resolved_title if language.startswith("en") else ""
        ),
        "cover_url": poster_url,
    }
