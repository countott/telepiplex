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
_SIMPLIFIED = re.compile(
    r"(?i)(?:^|[ ._\-\[\]()])(?:CHS|SC|GB|GBK|GB2312|ZH[ ._-]*HANS|"
    r"ZH[ ._-]*CN|CHI)(?:$|[ ._\-\[\]()&+])|简体|簡體|简中|簡中"
)
_TRADITIONAL = re.compile(
    r"(?i)(?:^|[ ._\-\[\]()])(?:CHT|TC|BIG5|ZH[ ._-]*HANT|"
    r"ZH[ ._-]*(?:TW|HK))(?:$|[ ._\-\[\]()&+])|繁体|繁體|繁中"
)
_ENGLISH = re.compile(
    r"(?i)(?:^|[ ._\-\[\]()&+])(?:ENG|EN)(?:$|[ ._\-\[\]()&+])|英文"
)
_ENGLISH_TAIL = re.compile(
    r"(?i)(?:^|[ ._\-\[\]()&+])ENGLISH"
    r"(?:[ ._\-\[\]()&+]*(?:FORCED|SDH|DEFAULT))*$"
)
_OTHER_LANGUAGE = re.compile(
    r"(?i)(?:^|[ ._\-\[\]()])(?:JPN|JA|JAPANESE|KOR|KO|KOREAN|"
    r"FRE|FRA|FR|GER|DEU|DE|SPA|ESP|ES|ITA|IT|RUS|RU|ARA|AR|"
    r"THA|TH|VIE|VI)(?:$|[ ._\-\[\]()&+])|日文|日语|日語|韩文|"
    r"韩语|韓文|韓語|法文|德文|西班牙文|俄文"
)
_KNOWN_LANGUAGE_CODES = (
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:JPN|JA|JAPANESE)(?:$|[ ._\-\[\]()&+])|日文|日语|日語"), "jpn"),
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:KOR|KO|KOREAN)(?:$|[ ._\-\[\]()&+])|韩文|韩语|韓文|韓語"), "kor"),
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:FRE|FRA|FR)(?:$|[ ._\-\[\]()&+])|法文"), "fre"),
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:GER|DEU|DE)(?:$|[ ._\-\[\]()&+])|德文"), "ger"),
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:SPA|ESP|ES)(?:$|[ ._\-\[\]()&+])|西班牙文"), "spa"),
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:ITA|IT)(?:$|[ ._\-\[\]()&+])|意大利文"), "ita"),
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:RUS|RU)(?:$|[ ._\-\[\]()&+])|俄文"), "rus"),
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:ARA|AR)(?:$|[ ._\-\[\]()&+])|阿拉伯文"), "ara"),
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:THA|TH)(?:$|[ ._\-\[\]()&+])|泰文|泰语|泰語"), "tha"),
    (re.compile(r"(?i)(?:^|[ ._\-\[\]()&+])(?:VIE|VI)(?:$|[ ._\-\[\]()&+])|越南文|越南语|越南語"), "vie"),
)


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


def _language_details(relative_path: str) -> tuple[str, str, str]:
    value = _text(relative_path)
    simplified = bool(_SIMPLIFIED.search(value))
    traditional = bool(_TRADITIONAL.search(value))
    english = bool(_ENGLISH.search(value) or _ENGLISH_TAIL.search(
        PurePosixPath(value).stem
    ))
    other = bool(_OTHER_LANGUAGE.search(value))
    if simplified and not traditional:
        if english:
            return "chi", "simplified_bilingual", "bilingual"
        return "chi", "simplified", "simplified"
    if traditional:
        return "chi", "traditional", "traditional"
    if english:
        return "eng", "english", "general"
    if other:
        for pattern, code in _KNOWN_LANGUAGE_CODES:
            if pattern.search(value):
                return code, "other", "general"
    return "unknown", "unknown", "unknown"


def collect_subtitle_evidence(file_tree: list[dict]) -> list[dict]:
    evidence = []
    for node in _subtitle_nodes(file_tree):
        language_code, language_profile, subtitle_variant = (
            _language_details(node["relative_path"])
        )
        evidence.append({
            **node,
            "episode_key": _episode_key(node["relative_path"]),
            "language_code": language_code,
            "language_profile": language_profile,
            "subtitle_variant": subtitle_variant,
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
        f"{target_stem}{variant}.{node['language_code']}"
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
        "language_profile": node["language_profile"],
        "language_code": node["language_code"],
        "subtitle_variant": node["subtitle_variant"],
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
        if item["language_code"] == "unknown" or grouping_key(item) is None
    ]
    eligible = [
        item for item in evidence
        if item["language_code"] != "unknown" and grouping_key(item) is not None
    ]
    grouped = defaultdict(list)
    for item in eligible:
        grouped[(
            grouping_key(item),
            item["language_code"],
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
