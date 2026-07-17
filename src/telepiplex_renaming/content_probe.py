"""Build bounded metadata hints without concatenating a download file tree."""

from __future__ import annotations

from pathlib import PurePosixPath
import re
import unicodedata


_VIDEO = re.compile(r"(?i)\.(?:mkv|mp4|avi|mov|m4v|ts|m2ts|wmv)$")
_EPISODE = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,3})\b")
_X_EPISODE = re.compile(r"(?i)\b(\d{1,2})x(\d{1,3})\b")
_SEASON_RANGE = re.compile(
    r"(?i)\bS(\d{1,2})\s*(?:-|~|TO)\s*S?(\d{1,2})\b"
)
_SEASON = re.compile(r"(?i)\bS(\d{1,2})\b|\bSeason[ ._-]+(\d{1,2})\b")
_IDENTITY_MARKERS = (
    re.compile(r"(?i)\bS\d{1,2}(?:E\d{1,3})?(?:\s*(?:-|~)\s*S?\d{1,2})?\b"),
    re.compile(r"(?i)\bSeason\s+\d{1,2}\b"),
    re.compile(r"(?i)\bEpisode\s+\d{1,3}\b"),
    re.compile(r"(?i)\b(?:2160p|1080p|720p|576p|480p|4K|8K)\b"),
    re.compile(
        r"(?i)\b(?:WEB\s*DL|WEBRip|BluRay|BDRip|REMUX|HDTV|"
        r"x26[45]|H\s*26[45]|HEVC|AVC|HDR10?|DoVi|DV)\b"
    ),
)


def _text(value) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).replace(
        "\xa0",
        " ",
    )


def _root_name(payload: dict) -> str:
    resource_name = str(payload.get("resource_name") or "").strip()
    if resource_name:
        return resource_name
    path = str(
        payload.get("download_root")
        or payload.get("final_path")
        or ""
    ).strip()
    if path:
        return PurePosixPath(path).name
    release = payload.get("release")
    if isinstance(release, dict):
        return str(release.get("title") or "")
    return ""


def _identity_query(payload: dict) -> str:
    value = _text(_root_name(payload))
    value = re.sub(r"^(?:\s*\[[^\]]+\]\s*)+", "", value)
    value = re.sub(
        r"(?i)\.(?:mkv|mp4|avi|mov|m4v|ts|m2ts|wmv)$",
        "",
        value,
    )
    value = re.sub(r"[._]+", " ", value)
    value = re.sub(r"\s*-\s*", " ", value)
    value = " ".join(value.split())
    marker_positions = [
        match.start()
        for pattern in _IDENTITY_MARKERS
        if (match := pattern.search(value))
    ]
    if marker_positions:
        value = value[:min(marker_positions)]
    return " ".join(value.split()).strip(" -")


def _observed_markers(values: list[str]) -> tuple[set[int], set[tuple[int, int]]]:
    seasons: set[int] = set()
    episodes: set[tuple[int, int]] = set()
    for value in values:
        value = _text(value)
        for pattern in (_EPISODE, _X_EPISODE):
            for match in pattern.finditer(value):
                season, episode = int(match.group(1)), int(match.group(2))
                seasons.add(season)
                episodes.add((season, episode))
        for match in _SEASON_RANGE.finditer(value):
            start, end = int(match.group(1)), int(match.group(2))
            if 0 <= start <= end and end - start <= 100:
                seasons.update(range(start, end + 1))
        for match in _SEASON.finditer(value):
            season = match.group(1) or match.group(2)
            if season is not None:
                seasons.add(int(season))
    return seasons, episodes


def build_metadata_probe(payload: dict) -> dict:
    """Return a root identity query and a separate, bounded content shape."""

    paths = []
    video_paths = []
    for node in payload.get("file_tree") or []:
        if not isinstance(node, dict) or node.get("is_dir"):
            continue
        path = str(
            node.get("relative_path")
            or node.get("name")
            or ""
        ).strip()
        if not path:
            continue
        paths.append(path)
        if _VIDEO.search(path):
            video_paths.append(path)
    marker_values = paths or [
        _root_name(payload),
        str(
            (payload.get("release") or {}).get("title") or ""
        ) if isinstance(payload.get("release"), dict) else "",
    ]
    seasons, episodes = _observed_markers(marker_values)
    if len(seasons) > 1:
        shape = "multi_season_pack"
    elif len(episodes) > 1:
        shape = "season_pack"
    elif len(episodes) == 1:
        shape = "single_episode"
    elif len(seasons) == 1:
        shape = "season_pack"
    elif len(video_paths) == 1:
        shape = "movie"
    else:
        shape = "unknown"
    identity_query = _identity_query(payload)
    year_match = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", identity_query)
    return {
        "identity_query": identity_query,
        "year_hint": year_match.group(1) if year_match else "",
        "content_shape": shape,
        "observed_seasons": sorted(seasons),
        "observed_episodes": [{
            "season_number": season,
            "episode_number": episode,
        } for season, episode in sorted(episodes)],
        "video_count": len(video_paths),
    }
