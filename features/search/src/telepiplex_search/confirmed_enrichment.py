"""Deterministic source queries after a user-confirmed work identity."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entity_graph import normalize_title


@dataclass(frozen=True)
class ConfirmedIdentity:
    provider: str
    stable_id: str
    chinese_title: str
    english_title: str
    original_title: str
    year: str
    media_type: str
    requested_scope: str


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _titles(value: dict) -> set[str]:
    return {
        normalized
        for title in (
            value.get("title"),
            value.get("name"),
            value.get("chinese_title"),
            value.get("english_title"),
            value.get("official_english_title"),
            value.get("original_title"),
            *(value.get("aliases") or ()),
        )
        if (normalized := normalize_title(title))
    }


def _identity_titles(identity: ConfirmedIdentity) -> set[str]:
    return {
        normalized
        for title in (
            identity.chinese_title,
            identity.english_title,
            identity.original_title,
        )
        if (normalized := normalize_title(title))
    }


def build_wikipedia_queries(
    identity: ConfirmedIdentity,
) -> dict[str, list[str]]:
    if identity.media_type not in {"movie", "series"}:
        return {"wikipedia_zh": [], "wikipedia_en": []}
    zh_type = "电影" if identity.media_type == "movie" else "电视剧"
    en_type = "film" if identity.media_type == "movie" else "TV series"
    zh = _text(" ".join(filter(None, (
        identity.chinese_title,
        identity.year,
        zh_type,
    ))))
    english_base = identity.english_title or (
        identity.original_title
        if re.search(r"[A-Za-z]", identity.original_title)
        else ""
    )
    en = _text(" ".join(filter(None, (
        english_base,
        identity.year,
        en_type,
    ))))
    return {
        "wikipedia_zh": [zh] if zh else [],
        "wikipedia_en": [en] if en else [],
    }


def _same_identity(
    raw: dict,
    identity: ConfirmedIdentity,
    *,
    media_type: str,
    require_media_type: bool = False,
) -> bool:
    raw_type = _text(raw.get("media_type")).casefold()
    if require_media_type and not raw_type:
        return False
    if raw_type and raw_type != media_type:
        return False
    raw_year = _text(raw.get("year"))[:4]
    if identity.year and raw_year and raw_year != identity.year:
        return False
    return bool(_titles(raw).intersection(_identity_titles(identity)))


def select_unique_wikipedia_fact(
    result: dict,
    identity: ConfirmedIdentity,
) -> dict | None:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    matches = [
        fact
        for fact in result.get("facts") or ()
        if isinstance(fact, dict)
        and _same_identity(
            fact,
            identity,
            media_type=identity.media_type,
            require_media_type=True,
        )
        and _text(
            fact.get("wikibase_item")
            or (
                fact.get("external_ids")
                if isinstance(fact.get("external_ids"), dict)
                else {}
            ).get("wikipedia")
        )
    ]
    stable_ids = {
        _text(
            fact.get("wikibase_item")
            or (fact.get("external_ids") or {}).get("wikipedia")
        )
        for fact in matches
    }
    return dict(matches[0]) if len(stable_ids) == 1 else None


def build_tvdb_query(
    identity: ConfirmedIdentity,
    wikipedia_fact: dict | None,
) -> dict | None:
    if identity.media_type != "series":
        return None
    wikipedia_fact = (
        wikipedia_fact if isinstance(wikipedia_fact, dict) else {}
    )
    title = _text(
        wikipedia_fact.get("official_english_title")
        or wikipedia_fact.get("english_title")
        or identity.english_title
        or identity.original_title
    )
    if not title or not re.search(r"[A-Za-z]", title):
        return None
    return {
        "title": title,
        "year": _text(wikipedia_fact.get("year") or identity.year)[:4],
        "media_type": "series",
    }


def select_unique_tvdb_series(
    result: dict,
    identity: ConfirmedIdentity,
) -> dict | None:
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    series = []
    for fact in result.get("facts") or ():
        if not isinstance(fact, dict):
            continue
        series.extend(
            item
            for item in fact.get("series") or ()
            if isinstance(item, dict)
        )
    matches = [
        item
        for item in series
        if _same_identity(item, identity, media_type="series")
        and _text(
            item.get("tvdb_series_id")
            or item.get("tvdb_id")
            or item.get("id")
        )
    ]
    ids = {
        _text(
            item.get("tvdb_series_id")
            or item.get("tvdb_id")
            or item.get("id")
        )
        for item in matches
    }
    return dict(matches[0]) if len(ids) == 1 else None
