"""Serializable boundaries between caption providers, quality checks and workflows."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping


class Serializable:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]):
        keys = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in keys})


@dataclass(frozen=True)
class MediaQuery(Serializable):
    title: str
    original_title: str = ""
    year: int | None = None
    media_type: str = "movie"
    tmdb_id: str = ""
    imdb_id: str = ""
    season: int | None = None
    episode: int | None = None
    original_language: str = ""
    video_path: str = ""
    release_name: str = ""
    duration_seconds: float | None = None
    fps: float | None = None
    aliases: tuple[str, ...] = ()
    shooter_hash: str = ""
    file_size: int | None = None
    # Metadata runtime is only a coarse completeness reference, never video
    # synchronization evidence. Keep it distinct from a probed video duration.
    expected_duration_seconds: float | None = None


@dataclass(frozen=True)
class SubtitleCandidate(Serializable):
    provider: str
    candidate_id: str
    title: str
    download_url: str = ""
    detail_url: str = ""
    release_name: str = ""
    year: int | None = None
    media_type: str = ""
    season: int | None = None
    episode: int | None = None
    language: str = ""
    subtitle_format: str = ""
    downloads: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubtitleDocument:
    filename: str
    format: str
    content: bytes


@dataclass(frozen=True)
class MatchResult(Serializable):
    accepted: bool
    score: int = 0
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityReport(Serializable):
    accepted: bool
    language: str = ""
    bilingual: bool = False
    cue_count: int = 0
    first_start: float = 0
    last_end: float = 0
    encoding: str = ""
    timing_verified: bool = False
    format: str = ""
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    chinese_ratio: float = 0
    bilingual_ratio: float = 0
    # Always normalized UTF-8, preserving ASS styles and all original dialogue.
    normalized_text: str = field(default="", repr=False)
