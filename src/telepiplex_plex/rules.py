# -*- coding: utf-8 -*-

from __future__ import annotations


GUID_SOURCE_ALIASES = {
    "imdb": "imdb",
    "tmdb": "tmdb",
    "themoviedb": "tmdb",
    "tvdb": "tvdb",
    "thetvdb": "tvdb",
}

LANGUAGE_CODES = {
    "en": "eng",
    "eng": "eng",
    "ja": "jpn",
    "jpn": "jpn",
    "ko": "kor",
    "kor": "kor",
    "zh": "chi",
    "zho": "chi",
    "chi": "chi",
}


def _normalize_expected_ids(external_ids):
    normalized = {}
    for source, value in (external_ids or {}).items():
        source = GUID_SOURCE_ALIASES.get(str(source).strip().lower())
        value = str(value or "").strip()
        if source and value:
            normalized[source] = value
    return normalized


def _normalize_actual_guids(guids):
    normalized = {}
    for guid in guids or []:
        if isinstance(guid, dict):
            guid = guid.get("id") or guid.get("guid") or ""
        source, separator, value = str(guid or "").strip().partition("://")
        source = GUID_SOURCE_ALIASES.get(source.lower())
        if separator and source and value:
            normalized[source] = value.strip()
    return normalized


def external_ids_match(expected, actual_guids):
    expected_ids = _normalize_expected_ids(expected)
    actual_ids = _normalize_actual_guids(actual_guids)
    overlapping_sources = expected_ids.keys() & actual_ids.keys()
    return bool(overlapping_sources) and all(
        expected_ids[source] == actual_ids[source]
        for source in overlapping_sources
    )


def choose_exact_match(expected, candidates):
    matches = [
        candidate
        for candidate in candidates or []
        if external_ids_match(expected, candidate.get("guids") or [])
    ]
    return matches[0] if len(matches) == 1 else None


def _number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolution(item):
    return int(_number(item.get("width"))) * int(_number(item.get("height")))


def _poster_rank(item):
    source = str(item.get("source") or "")
    return (
        2 if source == "tmdb" else 1,
        _number(
            item.get("vote_count")
            if source == "tmdb"
            else item.get("likes")
        ),
        _number(item.get("vote_average")),
        _resolution(item),
    )


def rank_textless_posters(tmdb_posters, fanart_posters):
    tmdb_candidates = [
        dict(item, source="tmdb")
        for item in tmdb_posters or []
        if item.get("iso_639_1") is None
    ]
    fanart_candidates = [
        dict(item, source="fanart")
        for item in fanart_posters or []
        if str(item.get("lang") or "") == "00"
    ]
    return sorted(
        tmdb_candidates + fanart_candidates,
        key=_poster_rank,
        reverse=True,
    )


def choose_textless_poster(tmdb_posters, fanart_posters):
    ranked = rank_textless_posters(tmdb_posters, fanart_posters)
    if not ranked:
        return None
    if len(ranked) > 1 and _poster_rank(ranked[0]) == _poster_rank(ranked[1]):
        return None
    return ranked[0]


def _normalize_language(value):
    value = str(value or "").strip().lower()
    return LANGUAGE_CODES.get(value, value)


def _audio_tier(stream):
    description = " ".join(
        str(stream.get(key) or "")
        for key in ("codec", "codec_profile", "display_title")
    ).lower()
    if any(marker in description for marker in ("truehd", "dts-hd ma", "dca-ma", "flac", "lpcm", "pcm")):
        return 300
    if "eac3" in description or "dd+" in description or "dolby digital plus" in description:
        return 220 if "atmos" in description else 210
    if "dts" in description:
        return 200
    if "ac3" in description:
        return 110
    if "aac" in description:
        return 100
    return 0


def _audio_rank(stream):
    return (
        _audio_tier(stream),
        int(_number(stream.get("channels"))),
        int(_number(stream.get("bitrate"))),
    )


def rank_original_audio(streams, original_language):
    target_language = _normalize_language(original_language)
    candidates = [
        stream
        for stream in streams or []
        if _normalize_language(stream.get("language_code")) == target_language
    ]
    return sorted(candidates, key=_audio_rank, reverse=True)


def choose_original_audio(streams, original_language):
    ranked = rank_original_audio(streams, original_language)
    if not ranked:
        return None
    if len(ranked) > 1 and _audio_rank(ranked[0]) == _audio_rank(ranked[1]):
        return None
    return ranked[0]


def _subtitle_tier(stream):
    if stream.get("external") and stream.get("selected"):
        return 3
    if stream.get("external") and not stream.get("transient"):
        return 2
    if not stream.get("external"):
        return 1
    return 0


def rank_chi_subtitles(streams):
    candidates = [
        stream
        for stream in streams or []
        if _normalize_language(stream.get("language_code")) == "chi"
        and _subtitle_tier(stream)
    ]
    return sorted(candidates, key=_subtitle_tier, reverse=True)


def choose_chi_subtitle(streams):
    ranked = rank_chi_subtitles(streams)
    if not ranked:
        return None
    if (
        len(ranked) > 1
        and _subtitle_tier(ranked[0]) == _subtitle_tier(ranked[1])
    ):
        return None
    return ranked[0]
