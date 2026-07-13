"""Wikipedia evidence adapter."""

from __future__ import annotations

import re
from urllib.parse import quote

import requests


USER_AGENT = "Telepiplex/1.0 (media metadata lookup)"


def _classification(title: str, extract: str) -> tuple[str, str]:
    text = f"{title} {extract}"
    year_match = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", text)
    lowered = text.casefold()
    lowered_title = title.casefold()
    series_signals = ("television series", "tv series", "電視劇", "电视剧", "劇集", "剧集")
    movie_signals = (" film", "movie", "電影", "电影", "影片")
    media_type = ""
    if any(item in lowered_title for item in movie_signals):
        media_type = "movie"
    elif any(item in lowered_title for item in series_signals):
        media_type = "series"
    elif any(item in lowered for item in series_signals):
        media_type = "series"
    elif any(item in lowered for item in movie_signals):
        media_type = "movie"
    return (year_match.group(1) if year_match else "", media_type)


def _empty(status: str, error: str = "") -> dict:
    return {
        "source": "wikipedia",
        "status": status,
        "facts": [],
        "source_urls": [],
        "error": str(error or ""),
    }


def lookup_wikipedia_evidence(
    queries: list[str],
    languages: tuple[str, ...] = ("zh", "en"),
    timeout: float = 10,
) -> dict:
    cleaned_queries = [" ".join(str(item or "").split()) for item in queries]
    cleaned_queries = [item for item in cleaned_queries if item]
    if not cleaned_queries:
        return _empty("not_found")

    facts = []
    urls = []
    errors = []
    successful_requests = 0
    for language in languages:
        endpoint = f"https://{language}.wikipedia.org/w/api.php"
        for query in cleaned_queries:
            try:
                response = requests.get(
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
                )
                response.raise_for_status()
                payload = response.json()
                successful_requests += 1
            except Exception as exc:
                errors.append(str(exc))
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
    return _empty("server_down", "; ".join(item for item in errors if item))
