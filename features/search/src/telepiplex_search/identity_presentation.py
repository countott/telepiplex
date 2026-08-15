"""Stable user-facing presentation for a confirmed media identity."""

from __future__ import annotations

import hashlib
import json
import unicodedata


_PROVIDER_LABELS = {
    "douban": "豆瓣",
    "tvdb": "TVDB",
    "wikipedia": "Wikipedia",
}


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _normalized(value) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", _text(value)).casefold()
        if character.isalnum()
    )


def _title(identity: dict) -> str:
    chinese = _text(identity.get("chinese_title"))
    english = _text(
        identity.get("official_english_title")
        or identity.get("english_title")
        or identity.get("canonical_latin_title")
    )
    if chinese and english and _normalized(chinese) != _normalized(english):
        return f"{chinese} ({english})"
    return chinese or english or "未知作品"


def _scope(contract: dict, media_type: str) -> str:
    retrieval = contract.get("retrieval") or {}
    placement = contract.get("placement") or {}
    scope = _text(retrieval.get("scope")).casefold()
    season = placement.get("season_number")
    episode = placement.get("episode_number")
    if media_type == "movie":
        return "电影"
    if scope == "episode" and season and episode:
        return f"S{int(season):02d}E{int(episode):02d}"
    if scope == "season" and season:
        return f"第 {int(season)} 季"
    return "全剧"


def _providers(contract: dict) -> list[str]:
    evidence = contract.get("evidence") or {}
    providers = []
    for item in evidence.get("source_links") or ():
        if isinstance(item, dict):
            provider = _text(item.get("provider")).casefold()
            if provider and provider not in providers:
                providers.append(provider)
    source_provider = _text(
        (contract.get("source_entry") or {}).get("provider")
    ).casefold()
    if source_provider and source_provider not in providers:
        providers.append(source_provider)
    return providers


def build_identity_presentation(contract: dict) -> dict:
    contract = contract if isinstance(contract, dict) else {}
    identity = contract.get("identity") or {}
    title = _title(identity)
    media_type = _text(
        (contract.get("retrieval") or {}).get("media_type")
        or (contract.get("placement") or {}).get("library_type")
        or identity.get("content_kind")
    ).casefold()
    media_label = "电影" if media_type == "movie" else "剧集"
    countries = [
        _text(item)
        for item in identity.get("countries") or ()
        if _text(item)
    ]
    country_label = "、".join(dict.fromkeys(countries)) or "地区未知"
    provider_values = _providers(contract)
    provider_label = "、".join(
        _PROVIDER_LABELS.get(item, item)
        for item in provider_values
    ) or "来源未知"
    scope_label = _scope(contract, media_type)
    year = _text(identity.get("year")) or "年份未知"
    text = (
        f"🎬 {title}\n"
        f"{year}｜{country_label}｜{media_label}｜{scope_label}\n"
        f"来源：{provider_label}"
    )
    stable_identity = {
        "external_ids": dict(identity.get("external_ids") or {}),
        "title": title,
        "year": year,
        "media_type": media_type,
        "scope": scope_label,
    }
    digest = hashlib.sha256(json.dumps(
        stable_identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:24]
    photo_url = _text(identity.get("poster_url"))
    if not photo_url.startswith("https://"):
        photo_url = ""
    return {
        "title": title,
        "title_status": (
            "verified_chinese"
            if _text(identity.get("chinese_title"))
            else "latin_fallback"
        ),
        "text": text,
        "photo_url": photo_url,
        "milestone_id": f"media-{digest}",
    }
