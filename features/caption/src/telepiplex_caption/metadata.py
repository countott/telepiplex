"""Adapt Search's confirmed identity without creating a second naming policy."""
from __future__ import annotations

from pathlib import PurePosixPath
import math
import re

from telepiplex_plugin_sdk.media_metadata_v2 import validate_media_metadata_v2

from .models import MediaQuery

VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts", ".wmv", ".webm"})
_MULTI_EPISODE = re.compile(r"(?i)(?:s\d+[ ._-]*e|\d+x)\d+(?:[ ._-]*e\d+|[ _]*-[ _]*\d+)(?![a-z0-9])")
_EXTRA_DIRECTORIES = frozenset({"sample", "samples", "extras", "trailers", "featurettes", "behind the scenes", "deleted scenes", "花絮", "预告片", "样片"})


def _is_extra(item: dict) -> bool:
    path = PurePosixPath(str(item.get("path") or item.get("name") or "").replace("\\", "/"))
    if any(part.casefold() in _EXTRA_DIRECTORIES for part in path.parts[:-1]):
        return True
    stem = PurePosixPath(str(item.get("name") or path.name)).stem.casefold()
    return bool(re.fullmatch(r"(?:sample|trailer|preview)(?:[ ._-]*\d{1,2})?", stem)
                or re.search(r"[._-](?:sample|trailer|preview)$", stem))


def episode_coordinates(value: str) -> tuple[int | None, int | None]:
    if _MULTI_EPISODE.search(value):
        return None, None
    match = re.search(r"(?i)(?<![a-z0-9])s(\d{1,3})[ ._-]*e(\d{1,4})(?!\d)", value)
    if not match:
        match = re.search(r"(?i)(?<!\d)(\d{1,2})x(\d{1,4})(?!\d)", value)
    if match:
        return int(match[1]), int(match[2])
    return None, None


def same_work(left: dict, right: dict) -> bool:
    a, b = left.get("identity") or {}, right.get("identity") or {}
    refs_a, refs_b = a.get("provider_refs") or {}, b.get("provider_refs") or {}
    shared = set(refs_a) & set(refs_b)
    return bool(shared) and all(str(refs_a[k]) == str(refs_b[k]) for k in shared) and a.get("media_type") == b.get("media_type")


def metadata_query(metadata: dict) -> str:
    identity = metadata.get("identity") or {}
    scope = metadata.get("scope") or {}
    title = identity.get("title_en") or identity.get("title_original") or identity.get("title_zh") or ""
    value = f"{title} {identity.get('year') or ''}".strip()
    if scope.get("season_number") is not None:
        value += f" S{int(scope['season_number']):02d}"
        if scope.get("episode_number") is not None:
            value += f"E{int(scope['episode_number']):02d}"
    return value


def make_query(metadata: dict, context: dict | None = None, item: dict | None = None) -> MediaQuery:
    if validate_media_metadata_v2(metadata) is None:
        raise ValueError("confirmed_media_metadata_required")
    identity, scope = metadata["identity"], metadata["scope"]
    context, item = context or {}, item or {}
    path = str(item.get("path") or "")
    name = str(item.get("name") or PurePosixPath(path).name)
    if path and _is_extra(item):
        raise ValueError("non_feature_video")
    season, episode = episode_coordinates(name)
    if identity["media_type"] == "series" and path and _MULTI_EPISODE.search(name):
        raise ValueError("multi_episode_video_unsupported")
    if season is None:
        season, episode = item.get("season"), item.get("episode")
    if season is None and not path:
        season, episode = scope.get("season_number"), scope.get("episode_number")
    if identity["media_type"] == "series" and path and (season is None or episode is None):
        raise ValueError("episode_mapping_unresolved")
    if identity["media_type"] == "movie":
        season, episode = None, None
    titles = tuple(dict.fromkeys(str(identity[k]) for k in ("title_zh", "title_en", "title_original") if identity.get(k)))
    refs = identity.get("provider_refs") or {}
    expected = context.get("expected_duration_seconds") if identity["media_type"] == "movie" else None
    if identity["media_type"] == "series" and season is not None and episode is not None:
        durations = context.get("episode_durations") or {}
        expected = durations.get(f"S{int(season):02d}E{int(episode):02d}") if isinstance(durations, dict) else None
    return MediaQuery(
        title=identity.get("title_en") or identity.get("title_original") or titles[0],
        original_title=identity.get("title_original") or "",
        aliases=titles,
        year=identity.get("year"),
        media_type=identity["media_type"],
        tmdb_id=str(refs.get("tmdb_movie") or refs.get("tmdb_tv") or ""),
        imdb_id=str(context.get("imdb_id") or ""),
        season=season,
        episode=episode,
        original_language=str(context.get("original_language") or ""),
        video_path=path,
        release_name=name,
        duration_seconds=_duration(item.get("duration_seconds")),
        expected_duration_seconds=_duration(expected),
        fps=item.get("fps"),
        file_size=item.get("size"),
        shooter_hash=str(item.get("shooter_hash") or ""),
    )


def _duration(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) and 0 < value <= 12 * 3600 else None


def video_items(tree: list[dict]) -> list[dict]:
    return [dict(item) for item in tree if isinstance(item, dict)
            and not item.get("is_dir")
            and not _is_extra(item)
            and PurePosixPath(str(item.get("name") or item.get("path") or "")).suffix.lower() in VIDEO_EXTENSIONS]
