# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


INVALID_NAME_CHARS = re.compile(r'[\\/*?"<>|]+')
CHINESE_DASH_PATTERN = re.compile(r"\s*(?:——|—|–|－)\s*")
RELEASE_GROUP_PATTERN = re.compile(r"-[A-Za-z0-9]+$")
QUALITY_START_PATTERN = re.compile(
    r"(?i)\b(?:19\d{2}|20\d{2}|S\d{1,2}E\d{1,3}|\d{1,2}x\d{1,3}|"
    r"2160p|1080p|720p|480p|WEB[-_. ]?DL|WEBRip|BluRay|BDRip|Remux|"
    r"HEVC|H\.?265|x265|H\.?264|x264|AAC|DTS|DDP?|EAC3|Atmos|TrueHD)\b"
)


@dataclass(frozen=True)
class MediaNamingPlan:
    chinese_folder: str
    english_folder: str
    target_relative_dir: str
    file_name: str
    is_episode: bool


def sanitize_path_name(name: str) -> str:
    name = str(name or "")
    name = name.replace("：", ": ")
    name = name.replace("（", "(").replace("）", ")")
    name = CHINESE_DASH_PATTERN.sub(" - ", name)
    name = re.sub(r"\s+:", ":", name)
    name = re.sub(r"(?<!\d):\s*", ": ", name)
    name = INVALID_NAME_CHARS.sub("", name)
    return " ".join(name.split()).strip().strip(".")


def _strip_collection_suffix(name: str, suffix: str) -> str:
    name = sanitize_path_name(name)
    if name.lower().endswith(suffix.lower()):
        name = name[: -len(suffix)].strip()
    return sanitize_path_name(name)


def _display_folder(chinese_title: str, english_title: str) -> str:
    chinese_title = sanitize_path_name(chinese_title)
    english_title = sanitize_path_name(english_title)
    if chinese_title and english_title and chinese_title != english_title:
        return f"{chinese_title} ◈ {english_title}"
    return chinese_title or english_title


def _collection_titles(metadata: dict) -> tuple[str, str]:
    chinese_title = (
        metadata.get("collection_chinese_title")
        or metadata.get("chinese_collection_title")
        or metadata.get("collection_chinese")
        or ""
    )
    english_title = (
        metadata.get("collection_english_title")
        or metadata.get("english_collection_title")
        or metadata.get("collection_english")
        or metadata.get("collection_title")
        or ""
    )
    return _strip_collection_suffix(chinese_title, "系列"), _strip_collection_suffix(english_title, "Collection")


def _episode_marker_text(season: int, episode: int) -> str:
    episode_width = 3 if episode >= 100 else 2
    return f"S{season:02d}E{episode:0{episode_width}d}"


def parse_episode_marker(release_title: str):
    title = str(release_title or "")
    patterns = [
        re.compile(r"(?i)\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})\b"),
        re.compile(r"(?i)\b(?P<season>\d{1,2})x(?P<episode>\d{1,3})\b"),
        re.compile(r"第\s*(?P<season>\d{1,2})\s*季\D{0,6}第\s*(?P<episode>\d{1,3})\s*[集话話]"),
    ]
    for pattern in patterns:
        match = pattern.search(title)
        if match:
            return int(match.group("season")), int(match.group("episode"))
    return None


def infer_english_title_from_release(release_title: str) -> str:
    title = str(release_title or "")
    title = RELEASE_GROUP_PATTERN.sub("", title)
    title = re.sub(r"(?i)\bS\d{1,2}E\d{1,3}\b", " ", title)
    title = re.sub(r"(?i)\b\d{1,2}x\d{1,3}\b", " ", title)
    title = re.sub(r"第\s*\d{1,2}\s*季\D{0,6}第\s*\d{1,3}\s*[集话話]", " ", title)

    quality_match = QUALITY_START_PATTERN.search(title)
    if quality_match:
        title = title[:quality_match.start()]

    title = re.sub(r"[._]+", " ", title)
    title = re.sub(r"[\s([{<]+$", "", title)
    return sanitize_path_name(title)


def build_media_naming_plan(metadata: dict | None, release_title: str, original_file_name: str):
    metadata = metadata or {}
    if metadata.get("source") not in {"douban", "search_query", "filename"}:
        return None

    chinese_folder = sanitize_path_name(metadata.get("chinese_title"))
    english_folder = sanitize_path_name(metadata.get("english_title"))
    if not english_folder and metadata.get("source") in {"search_query", "filename"}:
        english_folder = infer_english_title_from_release(release_title)
    if not chinese_folder and metadata.get("source") == "filename":
        chinese_folder = english_folder
    if not chinese_folder or not english_folder:
        return None

    suffix = Path(str(original_file_name or "")).suffix
    episode_marker = parse_episode_marker(release_title)
    if episode_marker:
        season, episode = episode_marker
        marker = _episode_marker_text(season, episode)
        target_relative_dir = f"{_display_folder(chinese_folder, english_folder)}/{english_folder} Season {season:02d}"
        file_stem = f"{english_folder} {marker}"
        is_episode = True
    else:
        movie_folder = _display_folder(chinese_folder, english_folder)
        collection_chinese, collection_english = _collection_titles(metadata)
        collection_folder = _display_folder(collection_chinese, collection_english)
        target_relative_dir = f"{collection_folder}/{movie_folder}" if collection_folder else movie_folder
        file_stem = english_folder
        is_episode = False

    return MediaNamingPlan(
        chinese_folder=chinese_folder,
        english_folder=english_folder,
        target_relative_dir=target_relative_dir,
        file_name=f"{file_stem}{suffix}",
        is_episode=is_episode,
    )
