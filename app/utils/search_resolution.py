# -*- coding: utf-8 -*-

import re
from datetime import date, datetime


def _collapse_spaces(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


CHINESE_NUMERAL_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


ENGLISH_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def _parse_number_token(value: str) -> int:
    value = _collapse_spaces(value).lower().replace("-", " ")
    if not value:
        return 0
    if value.isdigit():
        return int(value)
    if value in ENGLISH_NUMBER_WORDS:
        return ENGLISH_NUMBER_WORDS[value]
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = CHINESE_NUMERAL_DIGITS.get(left, 1 if left == "" else 0)
        ones = CHINESE_NUMERAL_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    total = 0
    for char in value:
        if char not in CHINESE_NUMERAL_DIGITS:
            return 0
        total = total * 10 + CHINESE_NUMERAL_DIGITS[char]
    return total


CHINESE_NUMBER_PATTERN = r"\d+|[零〇一二两三四五六七八九十]+"
ENGLISH_NUMBER_PATTERN = r"\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
SEASON_EPISODE_WORD_PATTERN = rf"(?:{ENGLISH_NUMBER_PATTERN})"


def _strip_scope_text(text: str) -> str:
    patterns = [
        r"(?i)\bS\d{1,2}\s*E\d{1,3}\b",
        r"(?i)\b\d{1,2}\s*x\s*\d{1,3}\b",
        rf"(?i)\bseason\s*(?:{SEASON_EPISODE_WORD_PATTERN})\s*(?:episode|ep)\s*(?:{SEASON_EPISODE_WORD_PATTERN})\b",
        r"(?i)\bS\d{1,2}\b",
        rf"第?\s*(?:{CHINESE_NUMBER_PATTERN})\s*季\s*第?\s*(?:{CHINESE_NUMBER_PATTERN})\s*[集话話]",
        rf"第?\s*(?:{CHINESE_NUMBER_PATTERN})\s*季",
        rf"第\s*(?:{CHINESE_NUMBER_PATTERN})\s*[集话話]",
        rf"(?i)\bseason\s*(?:{SEASON_EPISODE_WORD_PATTERN})\b",
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
        episode_match = re.search(
            r"(?i)\b(\d{1,2})\s*x\s*(\d{1,3})\b",
            query,
        )
    if not episode_match:
        episode_match = re.search(
            rf"(?i)\bseason\s*({SEASON_EPISODE_WORD_PATTERN})\s*(?:episode|ep)\s*({SEASON_EPISODE_WORD_PATTERN})\b",
            query,
        )
    if not episode_match:
        episode_match = re.search(
            rf"第?\s*({CHINESE_NUMBER_PATTERN})\s*季\s*第?\s*({CHINESE_NUMBER_PATTERN})\s*[集话話]",
            query,
        )
    if episode_match:
        intent.update(
            {
                "scope": "episode",
                "season_number": _parse_number_token(episode_match.group(1)),
                "episode_number": _parse_number_token(episode_match.group(2)),
                "title": _strip_scope_text(query),
            }
        )
        return intent

    season_match = re.search(r"(?i)\bS(\d{1,2})\b", query)
    if not season_match:
        season_match = re.search(rf"第?\s*({CHINESE_NUMBER_PATTERN})\s*季", query)
    if not season_match:
        season_match = re.search(rf"(?i)\bseason\s*({SEASON_EPISODE_WORD_PATTERN})\b", query)
    if season_match:
        intent.update(
            {
                "scope": "season",
                "season_number": _parse_number_token(season_match.group(1)),
                "title": _strip_scope_text(query),
            }
        )
        return intent

    if re.search(r"全集|全季|整季", query, re.IGNORECASE):
        intent.update({"scope": "whole_series", "title": _strip_scope_text(query)})

    return intent


def _candidate_title(entry: dict) -> str:
    return _collapse_spaces(entry.get("title") or entry.get("english_title") or entry.get("name") or entry.get("chinese_title"))


def _candidate_query_title(entry: dict) -> str:
    english_title = _collapse_spaces(entry.get("english_title") or "")
    if english_title and re.search(r"[A-Za-z]", english_title):
        return english_title
    title = _candidate_title(entry)
    if title and re.search(r"[A-Za-z]", title):
        return title
    return title


def _clean_prowlarr_query_text(value: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(value or ""), flags=re.UNICODE)
    return _collapse_spaces(value)


def _strip_trailing_season_suffix(value: str) -> str:
    value = _collapse_spaces(value)
    value = re.sub(rf"(?i)\bseason\s*(?:{SEASON_EPISODE_WORD_PATTERN})\s*$", " ", value)
    value = re.sub(r"(?i)\bS\d{1,2}\s*$", " ", value)
    value = re.sub(rf"第\s*(?:{CHINESE_NUMBER_PATTERN})\s*季\s*$", " ", value)
    return _collapse_spaces(value)


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
        "english_title": entry.get("english_title") or "",
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
            season_episodes = [
                item for item in episodes if _episode_key(item) and _episode_key(item)[0] == requested_season
            ]
            aired_episodes = [
                item for item in season_episodes if not is_unreleased_episode(item, today=today)
            ]
            has_unreleased_episodes = any(is_unreleased_episode(item, today=today) for item in season_episodes)
            if aired_episodes and has_unreleased_episodes:
                for episode in sorted(aired_episodes, key=lambda item: _episode_key(item)[1], reverse=True):
                    candidate = _base_candidate(entry, "episode")
                    candidate["season_number"], candidate["episode_number"] = _episode_key(episode)
                    candidates.append(candidate)
            elif aired_episodes:
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
    title = _clean_prowlarr_query_text(_candidate_query_title(candidate))
    scope = candidate.get("scope")
    if candidate.get("media_type") == "movie" or scope == "movie":
        year = str(candidate.get("year") or "").strip()
        return _collapse_spaces(f"{title} {year}" if year and year not in title else title)

    title = _clean_prowlarr_query_text(_strip_trailing_season_suffix(title))
    if scope == "episode":
        return _collapse_spaces(f"{title} S{int(candidate.get('season_number')):02d}E{int(candidate.get('episode_number')):02d}")
    if scope == "season":
        return _collapse_spaces(f"{title} S{int(candidate.get('season_number')):02d}")
    return title
