from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import re

from .media_naming import sanitize_target_name
from .subtitles import SUBTITLE_EXTENSIONS


VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts",
    ".wmv", ".flv", ".webm",
}
_SERIES_FILE = re.compile(
    r"^(.+?) S(?P<season>\d{2})E(?P<episode>\d{2,3})$",
    re.IGNORECASE,
)
_SEASON_DIR = re.compile(
    r"^(.+?) Season (?P<season>\d{2})$",
    re.IGNORECASE,
)
_RAW_RELEASE_MARKER = re.compile(
    r"(?i)(?:^|[ ._\-])(?:2160p|1080p|720p|480p|"
    r"WEB(?:[ ._\-]?DL|Rip)|BluRay|BDRip|Remux|HEVC|H[ ._]?265|"
    r"x265|H[ ._]?264|x264|AAC|DTS|DDP?|EAC3|Atmos|TrueHD)"
    r"(?:$|[ ._\-])"
)


def inventory_job_id(item: dict, source_path: str) -> str:
    file_id = str(
        item.get("file_id") or item.get("fid") or item.get("cid") or ""
    ).strip()
    if file_id:
        return f"inventory:{file_id}"
    digest = hashlib.sha256(
        str(source_path or "").encode("utf-8")
    ).hexdigest()[:24]
    return f"inventory:path:{digest}"


def _video_nodes(file_tree: list[dict]) -> list[dict]:
    return [
        item
        for item in file_tree or []
        if isinstance(item, dict)
        and not item.get("is_dir")
        and PurePosixPath(
            str(item.get("relative_path") or item.get("name") or "")
        ).suffix.lower() in VIDEO_EXTENSIONS
    ]


def _media_nodes(file_tree: list[dict]) -> list[dict]:
    return [
        item
        for item in file_tree or []
        if isinstance(item, dict)
        and not item.get("is_dir")
        and PurePosixPath(
            str(item.get("relative_path") or item.get("name") or "")
        ).suffix.lower() in (VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS)
    ]


def _normalized_media_identity(
    relative: PurePosixPath,
) -> tuple[str, str] | None:
    extension = relative.suffix.lower()
    if extension in VIDEO_EXTENSIONS:
        return relative.stem, "video"
    if extension in SUBTITLE_EXTENSIONS and relative.stem.endswith(".chi"):
        return relative.stem[:-4], extension
    return None


def contains_video(file_tree: list[dict]) -> bool:
    return bool(_video_nodes(file_tree))


def _display_english_title(root_name: str) -> str:
    match = re.search(r"\(([^()]*)\)\s*$", str(root_name or "").strip())
    return str(match.group(1) if match else root_name or "").strip()


def _has_only_target_safe_media(file_tree: list[dict]) -> bool:
    for item in file_tree or []:
        if not isinstance(item, dict):
            return False
        relative = PurePosixPath(str(
            item.get("relative_path") or item.get("name") or ""
        ))
        if not relative.parts or any(
            sanitize_target_name(part) != part for part in relative.parts
        ):
            return False
        if item.get("is_dir"):
            continue
        if relative.suffix.lower() not in (
            VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS
        ):
            return False
    return True


def looks_organized_release(root_name: str, file_tree: list[dict]) -> bool:
    nodes = [item for item in file_tree or [] if isinstance(item, dict)]
    videos = _video_nodes(file_tree)
    media = _media_nodes(file_tree)
    if (
        not media
        or sanitize_target_name(root_name) != str(root_name or "").strip()
        or not _has_only_target_safe_media(file_tree)
    ):
        return False
    english_title = _display_english_title(root_name)
    if not english_title:
        return False

    direct_media = []
    direct_media_kinds = set()
    for item in media:
        relative = PurePosixPath(str(
            item.get("relative_path") or item.get("name") or ""
        ))
        identity = _normalized_media_identity(relative)
        if (
            identity is None
            or len(relative.parts) != 1
            or identity[0] != english_title
            or identity[1] in direct_media_kinds
        ):
            direct_media = []
            break
        direct_media.append(relative)
        direct_media_kinds.add(identity[1])
    if (
        direct_media
        and len(nodes) == len(media)
        and not _RAW_RELEASE_MARKER.search(english_title)
    ):
        return True

    movie_children = []
    movie_video_counts = {}
    movie_media_kinds = {}
    for item in media:
        relative = PurePosixPath(str(
            item.get("relative_path") or item.get("name") or ""
        ))
        if len(relative.parts) != 2:
            movie_children = []
            break
        child_english_title = _display_english_title(relative.parts[0])
        identity = _normalized_media_identity(relative)
        if (
            not child_english_title
            or identity is None
            or identity[0] != child_english_title
        ):
            movie_children = []
            break
        target_key = (relative.parts[0], identity[1])
        if target_key in movie_media_kinds:
            movie_children = []
            break
        movie_media_kinds[target_key] = True
        movie_children.append(child_english_title)
        if identity[1] == "video":
            movie_video_counts[relative.parts[0]] = (
                movie_video_counts.get(relative.parts[0], 0) + 1
            )
        else:
            movie_video_counts.setdefault(relative.parts[0], 0)
    actual_movie_directories = {
        relative.parts[0]
        for item in nodes
        if item.get("is_dir")
        for relative in [PurePosixPath(str(
            item.get("relative_path") or item.get("name") or ""
        ))]
        if len(relative.parts) == 1
    }
    if (
        movie_children
        and actual_movie_directories == set(movie_video_counts)
        and all(count <= 1 for count in movie_video_counts.values())
        and len(nodes) == len(media) + len(actual_movie_directories)
    ):
        return True

    season_directories = set()
    episode_targets = set()
    for item in media:
        relative = PurePosixPath(str(
            item.get("relative_path") or item.get("name") or ""
        ))
        if len(relative.parts) != 2:
            return False
        directory_match = _SEASON_DIR.fullmatch(relative.parts[-2])
        identity = _normalized_media_identity(relative)
        file_match = (
            _SERIES_FILE.fullmatch(identity[0]) if identity else None
        )
        if (
            not directory_match
            or not file_match
            or directory_match.group(1) != english_title
            or file_match.group(1) != english_title
            or directory_match.group("season")
            != file_match.group("season")
        ):
            return False
        season_directories.add(relative.parts[-2])
        episode_target = (
            relative.parts[-2],
            identity[0].casefold(),
            identity[1],
        )
        if episode_target in episode_targets:
            return False
        episode_targets.add(episode_target)
    actual_season_directories = {
        relative.parts[0]
        for item in nodes
        if item.get("is_dir")
        for relative in [PurePosixPath(str(
            item.get("relative_path") or item.get("name") or ""
        ))]
        if len(relative.parts) == 1
    }
    return (
        actual_season_directories == season_directories
        and len(nodes) == len(media) + len(actual_season_directories)
    )
