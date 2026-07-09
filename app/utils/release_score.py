# -*- coding: utf-8 -*-

import math
import re

import init


DEFAULT_SCORING = {
    "prefer_resolution": ["2160p", "1080p"],
    "prefer_source": ["WEB-DL", "BluRay", "Remux"],
    "prefer_codec": ["HEVC", "H.265", "x265", "H.264", "x264"],
    "prefer_audio": ["Atmos", "TrueHD", "DTS-HD", "EAC3"],
    "reject_keywords": ["CAM", "TS", "TC", "枪版", "抢先", "HC", "HDTC", "HDCAM"],
}

WEIGHTS = {
    "prefer_resolution": {"2160p": 35, "1080p": 25},
    "prefer_source": {"WEB-DL": 25, "BluRay": 22, "Remux": 24},
    "prefer_codec": {"HEVC": 18, "H.265": 18, "x265": 18, "H.264": 10, "x264": 10},
    "prefer_audio": {"Atmos": 16, "TrueHD": 14, "DTS-HD": 12, "EAC3": 8},
}


def _get_scoring_config():
    search_config = init.bot_config.get("search") or {}
    scoring = search_config.get("scoring") or {}
    merged = DEFAULT_SCORING.copy()
    merged.update({key: value for key, value in scoring.items() if value is not None})
    return merged


def _score_value(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _configured_score_map(scoring: dict, key: str) -> dict:
    values = scoring.get(key) or {}
    if not isinstance(values, dict):
        return {}

    scores = {}
    for raw_key, raw_value in values.items():
        name = str(raw_key or "").strip()
        score = _score_value(raw_value)
        if name and score is not None:
            scores[name] = score
    return scores


def _keyword_score_entries(scoring: dict) -> list[tuple[str, int]]:
    configured_scores = _configured_score_map(scoring, "keyword_scores")
    emitted = set()
    entries = []

    for group in ("prefer_resolution", "prefer_source", "prefer_codec", "prefer_audio"):
        weights = WEIGHTS.get(group, {})
        for keyword in scoring.get(group, []):
            keyword = str(keyword or "").strip()
            if not keyword:
                continue
            score = configured_scores.get(keyword, weights.get(keyword, 8))
            entries.append((keyword, score))
            emitted.add(keyword.lower())

    for keyword in scoring.get("reject_keywords", []):
        keyword = str(keyword or "").strip()
        if not keyword:
            continue
        score = configured_scores.get(keyword, -60)
        entries.append((keyword, score))
        emitted.add(keyword.lower())

    for keyword, score in configured_scores.items():
        if keyword.lower() not in emitted:
            entries.append((keyword, score))

    return entries


def _indexer_score(indexer: str, scoring: dict) -> tuple[int, str]:
    indexer = str(indexer or "").strip()
    if not indexer:
        return 0, ""

    for configured_name, score in _configured_score_map(scoring, "indexer_scores").items():
        if configured_name.lower() == indexer.lower():
            return score, configured_name

    return 0, ""


def _contains_keyword(title: str, keyword: str) -> bool:
    if not keyword:
        return False

    if re.fullmatch(r"[A-Za-z0-9]+", keyword):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])"
        return re.search(pattern, title, re.IGNORECASE) is not None

    return keyword.lower() in title.lower()


def _safe_int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _score_seeders(seeders: int) -> int:
    if seeders <= 0:
        return -20
    if seeders < 3:
        return -8
    return min(25, int(math.log2(seeders + 1) * 5))


def _score_size(size: int) -> int:
    gib = size / 1024**3 if size else 0
    if gib <= 0:
        return -8
    if gib < 0.7:
        return -25
    if gib < 1.5:
        return -10
    if gib > 80:
        return -5
    return min(15, int(gib / 2))


def score_release(item: dict) -> tuple[int, list[str]]:
    title = item.get("title") or ""
    scoring = _get_scoring_config()
    score = 0
    features = []

    for keyword, keyword_score in _keyword_score_entries(scoring):
        if _contains_keyword(title, keyword):
            features.append(keyword)
            score += keyword_score

    indexer_score, indexer_name = _indexer_score(item.get("indexer"), scoring)
    if indexer_name:
        features.append(f"indexer:{indexer_name}")
        score += indexer_score

    score += _score_seeders(_safe_int(item.get("seeders")))
    score += _score_size(_safe_int(item.get("size")))

    return score, features


def _selectable_url(item: dict) -> str:
    return item.get("magnet_url") or item.get("download_url") or item.get("magnetUrl") or item.get("downloadUrl") or ""


def rank_releases(items: list[dict], limit: int) -> list[dict]:
    ranked = []
    for item in items:
        if not _selectable_url(item):
            continue
        score, features = score_release(item)
        enriched = item.copy()
        enriched["score"] = score
        enriched["features"] = features
        ranked.append(enriched)

    ranked.sort(key=lambda item: (item["score"], _safe_int(item.get("seeders")), _safe_int(item.get("size"))), reverse=True)
    return ranked[:limit]
