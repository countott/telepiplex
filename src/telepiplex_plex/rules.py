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


def choose_textless_poster(tmdb_posters, fanart_posters):
    tmdb_candidates = [
        dict(item, source="tmdb")
        for item in tmdb_posters or []
        if item.get("iso_639_1") is None
    ]
    if tmdb_candidates:
        return max(
            tmdb_candidates,
            key=lambda item: (
                _number(item.get("vote_count")),
                _number(item.get("vote_average")),
                _resolution(item),
                str(item.get("file_path") or ""),
            ),
        )
    fanart_candidates = [
        dict(item, source="fanart")
        for item in fanart_posters or []
        if str(item.get("lang") or "") == "00"
    ]
    if fanart_candidates:
        return max(
            fanart_candidates,
            key=lambda item: (
                _number(item.get("likes")),
                _resolution(item),
                str(item.get("url") or ""),
            ),
        )
    return None


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
        bool(stream.get("selected")),
    )


def choose_original_audio(streams, original_language):
    target_language = _normalize_language(original_language)
    candidates = [
        stream
        for stream in streams or []
        if _normalize_language(stream.get("language_code")) == target_language
    ]
    if not candidates:
        return None
    ranked = sorted(candidates, key=_audio_rank, reverse=True)
    if len(ranked) > 1 and _audio_rank(ranked[0]) == _audio_rank(ranked[1]):
        return None
    return ranked[0]


def choose_chi_subtitle(streams):
    chinese = [
        stream
        for stream in streams or []
        if str(stream.get("language_code") or "").strip().lower() == "chi"
    ]
    selected_external = next(
        (
            stream
            for stream in chinese
            if stream.get("external") and stream.get("selected")
        ),
        None,
    )
    if selected_external:
        return selected_external
    external = sorted(
        (
            stream
            for stream in chinese
            if stream.get("external") and not stream.get("transient")
        ),
        key=lambda stream: int(stream.get("id") or 0),
    )
    if external:
        return external[0]
    embedded = sorted(
        (stream for stream in chinese if not stream.get("external")),
        key=lambda stream: int(stream.get("id") or 0),
    )
    return embedded[0] if embedded else None
