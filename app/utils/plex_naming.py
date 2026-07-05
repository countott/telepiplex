# -*- coding: utf-8 -*-

import re
from dataclasses import dataclass
from pathlib import Path


INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|]+')
RELEASE_GROUP_PATTERN = re.compile(r"-[A-Za-z0-9]+$")
QUALITY_START_PATTERN = re.compile(
    r"(?i)\b(?:19\d{2}|20\d{2}|S\d{1,2}E\d{1,3}|\d{1,2}x\d{1,3}|"
    r"2160p|1080p|720p|480p|WEB[-_. ]?DL|WEBRip|BluRay|BDRip|Remux|"
    r"HEVC|H\.?265|x265|H\.?264|x264|AAC|DTS|DDP?|EAC3|Atmos|TrueHD)\b"
)


@dataclass(frozen=True)
class PlexNamingPlan:
    chinese_folder: str
    english_folder: str
    file_name: str
    is_episode: bool


def sanitize_path_name(name: str) -> str:
    name = INVALID_NAME_CHARS.sub(" ", str(name or ""))
    return " ".join(name.split()).strip().strip(".")


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


def build_plex_naming_plan(metadata: dict | None, release_title: str, original_file_name: str):
    metadata = metadata or {}
    if metadata.get("source") not in {"douban", "search_query"}:
        return None

    chinese_folder = sanitize_path_name(metadata.get("chinese_title"))
    english_folder = sanitize_path_name(metadata.get("english_title"))
    if not english_folder and metadata.get("source") == "search_query":
        english_folder = infer_english_title_from_release(release_title)
    if not chinese_folder or not english_folder:
        return None

    suffix = Path(str(original_file_name or "")).suffix
    episode_marker = parse_episode_marker(release_title)
    if episode_marker:
        season, episode = episode_marker
        file_stem = f"{english_folder} S{season:02d}E{episode:02d}"
        is_episode = True
    else:
        file_stem = english_folder
        is_episode = False

    return PlexNamingPlan(
        chinese_folder=chinese_folder,
        english_folder=english_folder,
        file_name=f"{file_stem}{suffix}",
        is_episode=is_episode,
    )
