"""Immutable file facts and deterministic filename evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re
import unicodedata

from .media_naming import parse_episode_marker


VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".m2ts",
    ".wmv", ".flv", ".webm",
}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".sup", ".vtt"}
OTHER_MEDIA_EXTENSIONS = {
    ".aac", ".ac3", ".dts", ".flac", ".mka", ".mp3", ".wav",
}

_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_EPISODE_START = re.compile(
    r"(?i)(?:\bS\d{1,2}E\d{1,4}\b|\b\d{1,2}x\d{1,4}\b|"
    r"\b(?:S|Season[ ._-]*)\d{1,2}\s*[-–—_.]\s*\d{1,4}\b|"
    r"第\s*\d{1,2}\s*季\D{0,6}第\s*\d{1,4}\s*[集话話])"
)
_QUALITY_START = re.compile(
    r"(?i)\b(?:2160p|1080p|720p|576p|480p|4k|8k|web[ ._-]?(?:dl|rip)|"
    r"bluray|bdrip|remux|hdtv|x26[45]|h[ ._-]*26[45]|hevc|avc|"
    r"aac|dts|ddp?|eac3|atmos|truehd)\b"
)
_SUBTITLE_MARKER = re.compile(
    r"(?i)(?:^|[ ._-])(?:chs|cht|chi|sc|tc|gb|big5|zh[ ._-]*(?:cn|tw|"
    r"hans|hant)|eng|en|jpn|ja|kor|ko|fre|fra|fr|ger|deu|de|spa|"
    r"esp|es|ita|it|rus|ru|ara|ar|tha|th|vie|vi|forced|sdh|cc)"
    r"(?:$|[ ._-])"
)
_ROLE_MARKERS = {
    "sample": "sample",
    "samples": "sample",
    "trailer": "trailer",
    "trailers": "trailer",
    "extra": "extra",
    "extras": "extra",
    "featurette": "extra",
    "featurettes": "extra",
    "花絮": "extra",
    "预告": "trailer",
    "预告片": "trailer",
}


@dataclass(frozen=True)
class FileFact:
    source_id: str
    provider: str
    absolute_path: str
    relative_path: str
    basename: str
    parent_parts: tuple[str, ...]
    extension: str
    size: int
    sha1: str
    media_kind: str
    snapshot_id: str


@dataclass(frozen=True)
class ParsedFileEvidence:
    source_id: str
    title_candidates: tuple[str, ...]
    title_key: str
    year_hint: int | None
    season_number: int | None
    episode_number: int | None
    absolute_episode: int | None
    content_role: str
    subtitle_language: str
    subtitle_variant: str
    confidence: str
    evidence: tuple[str, ...]
    directory_hints: tuple[str, ...]


def _normalized_path(value: str) -> str:
    return str(PurePosixPath(str(value or "") or "/"))


def _source_id(node: dict, provider: str, absolute_path: str) -> str:
    provider_id = str(
        node.get("file_id") or node.get("fid") or node.get("id") or ""
    ).strip()
    if provider_id:
        return provider_id
    identity = f"{provider.casefold()}\0{_normalized_path(absolute_path)}"
    return "path:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _media_kind(extension: str) -> str:
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if extension in OTHER_MEDIA_EXTENSIONS:
        return "other_media"
    return "non_media"


def build_file_facts(
    nodes: list[dict],
    *,
    root_path: str,
    provider: str,
    snapshot_id: str,
) -> list[FileFact]:
    """Convert scanned file nodes to immutable facts without inferring identity."""

    root = PurePosixPath(_normalized_path(root_path))
    facts = []
    for node in nodes or []:
        if not isinstance(node, dict) or node.get("is_dir"):
            continue
        relative = str(
            node.get("relative_path") or node.get("name") or ""
        ).strip().replace("\\", "/").strip("/")
        absolute_value = str(node.get("path") or "").strip()
        if not relative and not absolute_value:
            continue
        absolute = PurePosixPath(_normalized_path(
            absolute_value or str(root / relative)
        ))
        if not relative:
            try:
                relative = str(absolute.relative_to(root))
            except ValueError:
                relative = absolute.name
        relative_path = PurePosixPath(relative)
        basename = str(node.get("name") or relative_path.name).strip()
        extension = PurePosixPath(basename).suffix.lower()
        sha1 = str(node.get("sha1") or node.get("sha") or "").strip().lower()
        try:
            size = int(node.get("size") or node.get("fs") or 0)
        except (TypeError, ValueError):
            size = 0
        facts.append(FileFact(
            source_id=_source_id(node, provider, str(absolute)),
            provider=str(provider or ""),
            absolute_path=str(absolute),
            relative_path=str(relative_path),
            basename=basename,
            parent_parts=tuple(relative_path.parent.parts)
            if str(relative_path.parent) != "." else (),
            extension=extension,
            size=size,
            sha1=sha1,
            media_kind=_media_kind(extension),
            snapshot_id=str(snapshot_id or ""),
        ))
    return facts


def normalize_title_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in value if character.isalnum())


def _filename_title(fact: FileFact) -> tuple[str, int | None]:
    value = unicodedata.normalize("NFKC", PurePosixPath(fact.basename).stem)
    year_match = _YEAR.search(value)
    year = int(year_match.group(1)) if year_match else None
    value = re.sub(r"[（(]\s*(?:19|20)\d{2}\s*[）)]", " ", value)
    cut_points = [
        match.start()
        for match in (
            _YEAR.search(value),
            _EPISODE_START.search(value),
            _QUALITY_START.search(value),
            _SUBTITLE_MARKER.search(value),
        )
        if match is not None
    ]
    if cut_points:
        value = value[:min(cut_points)]
    value = re.sub(r"[._]+", " ", value)
    value = " ".join(value.split()).strip(" -–—([{")
    return value, year


def _subtitle_language(value: str) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", value)
    if re.search(r"(?i)(?:chs|sc|gb|zh[ ._-]*(?:cn|hans)|简体|簡體|简中|簡中)", normalized):
        return "chi", "simplified"
    if re.search(r"(?i)(?:cht|tc|big5|zh[ ._-]*(?:tw|hk|hant)|繁体|繁體|繁中)", normalized):
        return "chi", "traditional"
    if re.search(r"(?i)(?:^|[ ._-])(?:eng|en)(?:$|[ ._-])|英文", normalized):
        return "eng", "general"
    if re.search(r"(?i)(?:^|[ ._-])chi(?:$|[ ._-])", normalized):
        return "chi", "general"
    return "unknown", "unknown"


def _content_role(fact: FileFact) -> str:
    tokens = re.split(
        r"[ ._-]+",
        unicodedata.normalize("NFKC", "/".join(
            (*fact.parent_parts, PurePosixPath(fact.basename).stem)
        )).casefold(),
    )
    for token in tokens:
        if token in _ROLE_MARKERS:
            return _ROLE_MARKERS[token]
    if fact.media_kind == "subtitle":
        return "subtitle"
    if fact.media_kind == "video":
        return "main"
    return "unknown"


def parse_file_evidence(fact: FileFact) -> ParsedFileEvidence:
    """Parse file-local evidence; directory parts remain weak hints only."""

    directory_hints = tuple(
        " ".join(unicodedata.normalize("NFKC", part).split())
        for part in fact.parent_parts
        if str(part).strip()
    )
    if fact.media_kind == "non_media":
        return ParsedFileEvidence(
            source_id=fact.source_id,
            title_candidates=(),
            title_key="",
            year_hint=None,
            season_number=None,
            episode_number=None,
            absolute_episode=None,
            content_role="unknown",
            subtitle_language="unknown",
            subtitle_variant="unknown",
            confidence="low",
            evidence=(),
            directory_hints=directory_hints,
        )

    title, year = _filename_title(fact)
    marker = parse_episode_marker(PurePosixPath(fact.basename).stem)
    language, variant = (
        _subtitle_language(PurePosixPath(fact.basename).stem)
        if fact.media_kind == "subtitle"
        else ("unknown", "unknown")
    )
    evidence = []
    if title:
        evidence.append("filename:title")
    if year is not None:
        evidence.append("filename:year")
    if marker is not None:
        evidence.append("filename:episode")
    if language != "unknown":
        evidence.append("filename:subtitle_language")
    confidence = "high" if title and marker is not None else (
        "medium" if title else "low"
    )
    return ParsedFileEvidence(
        source_id=fact.source_id,
        title_candidates=(title,) if title else (),
        title_key=normalize_title_key(title),
        year_hint=year,
        season_number=marker[0] if marker else None,
        episode_number=marker[1] if marker else None,
        absolute_episode=None,
        content_role=_content_role(fact),
        subtitle_language=language,
        subtitle_variant=variant,
        confidence=confidence,
        evidence=tuple(evidence),
        directory_hints=directory_hints,
    )
