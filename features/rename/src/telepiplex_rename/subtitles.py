"""Plan evidence-bound external subtitle organization."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
import re
import unicodedata

from .media_naming import parse_episode_marker, sanitize_target_name


SUBTITLE_EXTENSIONS = {".srt", ".ass", ".sup", ".vtt"}

_SEASON = re.compile(
    r"(?i)(?:^|[ ._\-/])(?:S|Season[ ._-]*)(\d{1,2})(?:$|[ ._\-/])"
)
_BARE_EPISODE = re.compile(
    r"(?i)^(?:E|EP|Episode[ ._-]*)?(\d{1,4})(?=$|[ ._\-])"
)
SUBTITLE_FILENAME_LANGUAGE = "chi"


def _text(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def _subtitle_nodes(file_tree: list[dict]) -> list[dict]:
    result = []
    for item in file_tree or []:
        if not isinstance(item, dict) or item.get("is_dir"):
            continue
        relative = str(
            item.get("relative_path") or item.get("name") or ""
        ).strip("/")
        extension = PurePosixPath(relative).suffix.lower()
        if relative and extension in SUBTITLE_EXTENSIONS:
            node = dict(item)
            node["relative_path"] = relative
            node["name"] = str(item.get("name") or PurePosixPath(relative).name)
            node["extension"] = extension
            node["source_id"] = str(
                item.get("source_id")
                or item.get("file_id")
                or item.get("fid")
                or item.get("id")
                or f"path:{relative}"
            )
            result.append(node)
    return result


def _episode_key(relative_path: str) -> tuple[int, int] | None:
    marker = parse_episode_marker(relative_path)
    if marker is not None:
        return marker
    path = PurePosixPath(relative_path)
    seasons = {
        int(match.group(1))
        for part in path.parent.parts
        for match in _SEASON.finditer(f"/{part}/")
    }
    if len(seasons) != 1:
        return None
    stem = path.stem
    match = _BARE_EPISODE.match(stem)
    if not match:
        return None
    episode = int(match.group(1))
    return (next(iter(seasons)), episode) if episode > 0 else None


def collect_subtitle_evidence(file_tree: list[dict]) -> list[dict]:
    evidence = []
    for node in _subtitle_nodes(file_tree):
        evidence.append({
            **node,
            "episode_key": _episode_key(node["relative_path"]),
            "language_code": "unknown",
            "language_profile": "unknown",
            "subtitle_variant": "unknown",
        })
    return evidence


def _operation(
    node: dict,
    *,
    final_path: str,
    target_dir: str,
    target_stem: str,
    episode_key: tuple[int, int] | None = None,
    variant_index: int = 1,
) -> dict:
    target_stem = sanitize_target_name(target_stem)
    variant = f".variant-{variant_index:02d}" if variant_index > 1 else ""
    rename_to = (
        f"{target_stem}{variant}.{SUBTITLE_FILENAME_LANGUAGE}"
        f"{node['extension']}"
    )
    source_path = str(node.get("path") or "") or (
        f"{str(final_path).rstrip('/')}/{node['relative_path']}"
    )
    source_parent = source_path.rsplit("/", 1)[0]
    operation = {
        "media_kind": "subtitle",
        "content_role": "external_subtitle",
        "source_relative_path": node["relative_path"],
        "source_id": node["source_id"],
        "source_path": source_path,
        "rename_to": rename_to,
        "renamed_source_path": f"{source_parent}/{rename_to}",
        "target_dir": target_dir,
        "target_relative_path": rename_to,
        "final_path": f"{str(target_dir).rstrip('/')}/{rename_to}",
        "language_profile": "unknown",
        "language_code": "unknown",
        "subtitle_variant": "unknown",
        "extension": node["extension"],
        "source_sha1": str(
            node.get("sha1") or node.get("sha") or ""
        ).strip().lower(),
    }
    if episode_key is not None:
        operation.update({
            "episode_key": episode_key,
            "season_number": episode_key[0],
            "episode_number": episode_key[1],
        })
    return operation


def _plan_subtitles(
    evidence: list[dict],
    *,
    grouping_key,
) -> tuple[list[tuple[dict, int]], list[str]]:
    kept = [
        item["relative_path"]
        for item in evidence
        if grouping_key(item) is None
    ]
    eligible = [
        item for item in evidence
        if grouping_key(item) is not None
    ]
    grouped = defaultdict(list)
    for item in eligible:
        grouped[(
            grouping_key(item),
            item["extension"],
        )].append(item)

    planned = []
    for key in sorted(grouped, key=lambda value: str(value)):
        items = sorted(grouped[key], key=lambda item: item["source_id"])
        planned.extend((item, index) for index, item in enumerate(items, 1))
    return planned, sorted(set(kept))


def build_series_subtitle_plan(
    *,
    final_path: str,
    target_root: str,
    series_name: str,
    file_tree: list[dict],
    allowed_targets: set[tuple[int, int]] | None = None,
    episode_assignments: dict[str, tuple[int, int]] | None = None,
) -> dict:
    assignments = episode_assignments or {}
    evidence = collect_subtitle_evidence(file_tree)
    for item in evidence:
        assigned = assignments.get(item["relative_path"])
        if assigned is not None:
            item["episode_key"] = assigned
        if (
            item["episode_key"] is not None
            and allowed_targets
            and item["episode_key"] not in allowed_targets
        ):
            item["episode_key"] = None

    selected, kept = _plan_subtitles(
        evidence,
        grouping_key=lambda item: item["episode_key"],
    )
    operations = []
    safe_series_name = sanitize_target_name(series_name)
    for item, variant_index in selected:
        season, episode = item["episode_key"]
        marker = f"S{season:02d}E{episode:0{3 if episode >= 100 else 2}d}"
        target_dir = (
            f"{str(target_root).rstrip('/')}/"
            f"{safe_series_name} Season {season:02d}"
        )
        operation = _operation(
            item,
            final_path=final_path,
            target_dir=target_dir,
            target_stem=f"{safe_series_name} {marker}",
            episode_key=(season, episode),
            variant_index=variant_index,
        )
        operation["target_relative_path"] = (
            f"{safe_series_name} Season {season:02d}/"
            f"{operation['rename_to']}"
        )
        operations.append(operation)
    operations.sort(key=lambda item: (
        item["season_number"], item["episode_number"],
        item["language_code"], item["extension"], item["source_id"],
    ))
    return {
        "operations": operations,
        "discard_sources": [],
        "kept_sources": kept,
        "unresolved_sources": [],
    }


def build_movie_subtitle_plan(
    *,
    final_path: str,
    target_dir: str,
    target_stem: str,
    file_tree: list[dict],
) -> dict:
    evidence = collect_subtitle_evidence(file_tree)
    selected, kept = _plan_subtitles(
        evidence,
        grouping_key=lambda _item: "movie",
    )
    operations = [
        _operation(
            item,
            final_path=final_path,
            target_dir=target_dir,
            target_stem=target_stem,
            variant_index=variant_index,
        )
        for item, variant_index in selected
    ]
    operations.sort(key=lambda item: (
        item["language_code"], item["extension"], item["source_id"],
    ))
    return {
        "operations": operations,
        "discard_sources": [],
        "kept_sources": kept,
        "unresolved_sources": [],
    }
