# -*- coding: utf-8 -*-

import re
from datetime import date, datetime


def _collapse_spaces(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _strip_scope_text(text: str) -> str:
    patterns = [
        r"(?i)\bS\d{1,2}\s*E\d{1,3}\b",
        r"(?i)\bS\d{1,2}\b",
        r"第\s*\d+\s*季\s*第\s*\d+\s*[集话話]",
        r"第\s*\d+\s*季",
        r"第\s*\d+\s*[集话話]",
        r"(?i)\bseason\s*\d+\b",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text)
    return _collapse_spaces(text)


def parse_search_intent(raw_query: str) -> dict:
    query = _collapse_spaces(raw_query)
    intent = {
        "raw_query": query,
        "title": query,
        "scope": "movie_or_series",
        "season_number": None,
        "episode_number": None,
        "year": "",
    }

    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", query)
    if year_match:
        intent["year"] = year_match.group(1)

    episode_match = re.search(r"(?i)\bS(\d{1,2})\s*E(\d{1,3})\b", query)
    if not episode_match:
        episode_match = re.search(r"第\s*(\d+)\s*季\s*第\s*(\d+)\s*[集话話]", query)
    if episode_match:
        intent.update(
            {
                "scope": "episode",
                "season_number": int(episode_match.group(1)),
                "episode_number": int(episode_match.group(2)),
                "title": _strip_scope_text(query),
            }
        )
        return intent

    season_match = re.search(r"(?i)\bS(\d{1,2})\b", query)
    if not season_match:
        season_match = re.search(r"第\s*(\d+)\s*季", query)
    if not season_match:
        season_match = re.search(r"(?i)\bseason\s*(\d+)\b", query)
    if season_match:
        intent.update(
            {
                "scope": "season",
                "season_number": int(season_match.group(1)),
                "title": _strip_scope_text(query),
            }
        )
        return intent

    if re.search(r"全集|全季|整季", query, re.IGNORECASE):
        intent.update({"scope": "whole_series", "title": _strip_scope_text(query)})

    return intent


def _candidate_title(entry: dict) -> str:
    return _collapse_spaces(entry.get("title") or entry.get("english_title") or entry.get("name") or entry.get("chinese_title"))


def _external_id(entry: dict, key: str = "tvdb") -> str:
    external_ids = entry.get("external_ids") if isinstance(entry.get("external_ids"), dict) else {}
    return str(external_ids.get(key) or entry.get(f"{key}_series_id") or entry.get(f"{key}_id") or "").strip()


def _episode_key(episode: dict):
    try:
        return int(episode.get("season_number")), int(episode.get("episode_number"))
    except (TypeError, ValueError):
        return None


def _parse_air_date(value: str):
    value = str(value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None


def is_unreleased_episode(episode: dict, today: date | None = None) -> bool:
    aired = _parse_air_date(episode.get("aired") or episode.get("first_aired") or episode.get("firstAired"))
    if not aired:
        return True
    return aired > (today or date.today())


def _base_candidate(entry: dict, scope: str) -> dict:
    external_ids = entry.get("external_ids") if isinstance(entry.get("external_ids"), dict) else {}
    return {
        "media_type": entry.get("media_type") or ("series" if _external_id(entry, "tvdb") else "movie"),
        "scope": scope,
        "title": _candidate_title(entry),
        "chinese_title": entry.get("chinese_title") or "",
        "year": str(entry.get("year") or ""),
        "external_ids": external_ids.copy(),
        "cover_url": entry.get("cover_url") or "",
        "recommended": False,
    }


def build_confirmation_candidates(
    entries: list[dict],
    intent: dict,
    episodes_by_series: dict | None = None,
    today: date | None = None,
) -> list[dict]:
    candidates = []
    episodes_by_series = episodes_by_series or {}
    intent_scope = (intent or {}).get("scope") or "movie_or_series"

    for entry in entries or []:
        media_type = entry.get("media_type") or ("series" if _external_id(entry, "tvdb") else "movie")
        if media_type != "series":
            candidate = _base_candidate(entry, "movie")
            candidate["media_type"] = "movie"
            candidates.append(candidate)
            continue

        series_id = _external_id(entry, "tvdb")
        episodes = episodes_by_series.get(series_id) or []

        if intent_scope == "episode":
            requested = (int(intent.get("season_number") or 0), int(intent.get("episode_number") or 0))
            episode = next((item for item in episodes if _episode_key(item) == requested), None)
            if episode and not is_unreleased_episode(episode, today=today):
                candidate = _base_candidate(entry, "episode")
                candidate["season_number"], candidate["episode_number"] = requested
                candidates.append(candidate)
            continue

        if intent_scope == "season":
            requested_season = int(intent.get("season_number") or 0)
            aired_season = any(
                _episode_key(item)
                and _episode_key(item)[0] == requested_season
                and not is_unreleased_episode(item, today=today)
                for item in episodes
            )
            if aired_season:
                candidate = _base_candidate(entry, "season")
                candidate["season_number"] = requested_season
                candidates.append(candidate)
            continue

        whole = _base_candidate(entry, "whole_series")
        candidates.append(whole)

    if candidates:
        candidates[0]["recommended"] = True
    return candidates


def candidate_to_prowlarr_query(candidate: dict) -> str:
    title = _candidate_title(candidate)
    scope = candidate.get("scope")
    if candidate.get("media_type") == "movie" or scope == "movie":
        year = str(candidate.get("year") or "").strip()
        return _collapse_spaces(f"{title} {year}" if year and year not in title else title)

    if scope == "episode":
        return _collapse_spaces(f"{title} S{int(candidate.get('season_number')):02d}E{int(candidate.get('episode_number')):02d}")
    if scope == "season":
        return _collapse_spaces(f"{title} S{int(candidate.get('season_number')):02d}")
    return title
