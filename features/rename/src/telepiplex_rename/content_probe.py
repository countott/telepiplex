"""Build bounded metadata hints without concatenating a download file tree."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
import re
import unicodedata


_VIDEO = re.compile(
    r"(?i)\.(?:mkv|mp4|avi|mov|m4v|ts|m2ts|wmv|flv|webm)$"
)
_SUBTITLE = re.compile(r"(?i)\.(?:srt|ass|sup|vtt)$")
_EPISODE = re.compile(r"(?i)\bS(\d{1,2})E(\d{1,4})\b")
_EPISODE_CHAIN = re.compile(
    r"(?i)\bS(\d{1,2})((?:E\d{1,4}){2,})(?!\d)"
)
_EPISODE_RANGE = re.compile(
    r"(?i)\bS(\d{1,2})E(\d{1,4})\s*-\s*E?(\d{1,4})(?!\d)"
)
_X_EPISODE = re.compile(r"(?i)\b(\d{1,2})x(\d{1,3})\b")
_UNSCOPED_EPISODE = re.compile(r"(?i)(?<![A-Z0-9])E(\d{1,4})(?!\d)")
_EP_ABSOLUTE = re.compile(
    r"(?i)(?<![A-Z0-9])EP(?:ISODE)?[ ._-]*(\d{1,4})(?![\dP])"
)
_DASH_EPISODE = re.compile(
    r"(?<!\S)-\s*(\d{1,4})(?=\s*(?:-|\(|\[|$))"
)
_BRACKET_EPISODE = re.compile(r"[\[【]\s*(\d{1,4})\s*[\]】]")
_BARE_EPISODE_STEM = re.compile(r"^(\d{1,3})$")
_SEASON_RANGE = re.compile(
    r"(?i)\bS(\d{1,2})\s*(?:-|~|TO)\s*S?(\d{1,2})\b"
)
_SEASON = re.compile(r"(?i)\bS(\d{1,2})\b|\bSeason[ ._-]+(\d{1,2})\b")
_CHINESE_NUMBER = r"[0-9零〇一二三四五六七八九十百两]+"
_CHINESE_SEASON = re.compile(rf"第\s*({_CHINESE_NUMBER})\s*季")
_CHINESE_EPISODE = re.compile(rf"第\s*({_CHINESE_NUMBER})\s*[集话]")
_IDENTITY_MARKERS = (
    _EPISODE_CHAIN,
    _EPISODE_RANGE,
    re.compile(r"(?i)\bS\d{1,2}(?:E\d{1,4})?(?:\s*(?:-|~)\s*S?\d{1,2})?\b"),
    re.compile(r"(?i)\bSeason\s+\d{1,2}\b"),
    re.compile(r"(?i)\bEpisode\s+\d{1,3}\b"),
    re.compile(r"(?i)\b\d{1,2}x\d{1,3}\b"),
    re.compile(r"(?i)(?<![A-Z0-9])E\d{1,4}(?!\d)"),
    _EP_ABSOLUTE,
    _DASH_EPISODE,
    _CHINESE_SEASON,
    _CHINESE_EPISODE,
    re.compile(r"(?i)\b(?:2160p|1080p|720p|576p|480p|4K|8K)\b"),
    re.compile(
        r"(?i)\b(?:WEB(?:[ ._-]?DL|Rip)|BluRay|BDRip|HDRip|REMUX|HDTV|"
        r"x26[45]|H[ ._-]*26[45]|HEVC|AVC|HDR10?|DoVi|DV|"
        r"AAC|DTS|DDP?|EAC3|Atmos|TrueHD)\b"
    ),
)
_SITE_PREFIX = re.compile(
    r"(?i)^\s*(?:https?://)?(?:www\.)?[a-z0-9-]+\."
    r"[a-z]{2,10}\s*(?:[-_:|]+\s*)"
)
_BRACKET_GROUP = re.compile(r"[\[【]([^\]】]+)[\]】]")
_BRACKET_SEPARATOR = re.compile(r"[\s._-]*")
_BRACKET_NOISE = re.compile(
    r"(?i)^(?:\d{1,4}(?:\s*(?:-|~)\s*\d{1,4})?(?:\s*(?:end|fin|完))?|"
    r"\d{3,4}p(?:10)?|4k|8k|mp4|mkv|avi|webm|end|fin|"
    r"\d{1,2}月新番|国漫|國漫|"
    r"chs|cht|jpn|eng|简体|繁体|簡體|繁體|内封|内嵌|"
    r"webrip|web-dl|bluray|bdrip|hevc|x26[45]|h\.?26[45]|aac)$"
)
_NON_PRIMARY_VIDEO_PART = re.compile(
    r"(?i)^(?:samples?|trailers?|extras?|featurettes?|"
    r"behind[ ._-]*the[ ._-]*scenes?|花絮|预告片?)$"
)
_GENERIC_IDENTITIES = {
    "raw",
    "raw release",
    "release",
    "download",
    "downloads",
    "movie",
    "series",
    "season",
    "tv",
    "video",
    "未整理",
}
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


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


def _identity_query_value(raw_value: str) -> str:
    value = _text(raw_value)
    value = _VIDEO.sub("", value)
    value = _SUBTITLE.sub("", value)
    value = _SITE_PREFIX.sub("", value)
    value = re.sub(r"^\s*[（(](?:19|20)\d{2}[）)]\s*", "", value)
    bracket_identity = _bracket_release_identity(value)
    if bracket_identity:
        value = bracket_identity
    else:
        value = re.sub(
            r"^(?:(?:\s|[._-])*[\[【][^\]】]+[\]】])+[\s._-]*",
            "",
            value,
        )
    value = re.sub(r"[._]+", " ", value)
    value = " ".join(value.split())
    marker_positions = [
        match.start()
        for pattern in _IDENTITY_MARKERS
        if (match := pattern.search(value))
    ]
    if marker_positions:
        value = value[:min(marker_positions)]
    value = re.sub(r"(?:\s*\[[^\]]+\]\s*)+$", "", value)
    return " ".join(value.split()).strip(" -([")


def _bracket_release_identity(value: str) -> str:
    """Return a title group when a release name is bracket-only."""

    groups = []
    position = 0
    while True:
        separator = _BRACKET_SEPARATOR.match(value, position)
        start = separator.end() if separator else position
        match = _BRACKET_GROUP.match(value, start)
        if not match:
            position = start
            break
        groups.append(" ".join(match.group(1).split()))
        position = match.end()
    if not groups or value[position:].strip(" ._-"):
        return ""
    candidates = _bracket_identity_candidates(groups)
    if candidates:
        return candidates[0]
    return ""


def _looks_like_release_group(value: str, *, first: bool) -> bool:
    folded = value.casefold().replace(" ", "")
    if (
        "字幕" in value
        or "字幕" in folded
        or "fansub" in folded
        or "搬运" in value
        or "搬運" in value
        or (first and ("组" in value or "組" in value))
        or folded.endswith(("sub", "subs", "raw", "raws", "team"))
        or "-raws" in folded
        or "-team" in folded
    ):
        return True
    return bool(
        first
        and re.fullmatch(r"[A-Z0-9Q]{1,12}", value)
    )


def _bracket_identity_candidates(groups: list[str]) -> list[str]:
    result = []
    for index, group in enumerate(groups):
        group = " ".join(_text(group).split())
        if (
            not group
            or _BRACKET_NOISE.fullmatch(group)
            or _looks_like_release_group(group, first=index == 0)
        ):
            continue
        if group not in result:
            result.append(group)
    return result


def _is_numeric_identity(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}", " ".join(_text(value).split())))


def _is_usable_identity(value: str, *, allow_numeric: bool = False) -> bool:
    normalized = " ".join(_text(value).casefold().split()).strip(" -")
    if not normalized or normalized in _GENERIC_IDENTITIES:
        return False
    if re.fullmatch(r"(?:raw\s+)?release(?:\s+\d+)?", normalized):
        return False
    if re.fullmatch(r"(?:season\s*\d+|s\d{1,2})", normalized):
        return False
    if re.fullmatch(r"\d{5,}", normalized):
        return False
    if re.fullmatch(r"[0-9a-f]{12,}", normalized):
        return False
    if re.fullmatch(r"\d{1,3}", normalized) and not allow_numeric:
        return False
    if re.fullmatch(r"(?i)S\d{1,4}E\d{1,4}(?:E\d{1,4})*", normalized):
        return False
    if re.fullmatch(r"(?i)\d{1,4}x\d{1,4}(?:x\d{1,4})*", normalized):
        return False
    tokens = set(re.findall(r"[a-z]+", normalized))
    if tokens and tokens <= {
        "aac", "audio", "big", "cht", "chs", "dual", "dub", "eng",
        "esp", "gb", "ing", "jpn", "multi", "sub", "subs", "ukr",
    }:
        return False
    if re.fullmatch(r"[（(]?(?:19|20)\d{2}[）)]?", normalized):
        return False
    return True


def _identity_key(value: str) -> str:
    return "".join(
        character
        for character in _text(value).casefold()
        if character.isalnum()
    )


def _identity_tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[\w]+", _text(value).casefold()))


def _is_primary_video(path: str) -> bool:
    relative = PurePosixPath(path)
    if any(
        _NON_PRIMARY_VIDEO_PART.fullmatch(part.rsplit(".", 1)[0])
        for part in relative.parts
    ):
        return False
    return not any(
        token in {
            "sample",
            "samples",
            "trailer",
            "trailers",
            "extra",
            "extras",
            "featurette",
            "featurettes",
            "花絮",
            "预告",
            "预告片",
        }
        for token in re.split(
            r"[ ._\-]+",
            relative.stem.casefold(),
        )
    )


def _file_identity_consensus(
    video_paths: list[str],
) -> tuple[str, bool]:
    def consensus(
        candidates: list[str],
        *,
        allow_numeric: bool = False,
    ) -> tuple[str, bool]:
        candidates = [
            candidate for candidate in candidates
            if _is_usable_identity(
                candidate,
                allow_numeric=allow_numeric,
            )
        ]
        if not candidates:
            return "", False
        counts = Counter(_identity_key(candidate) for candidate in candidates)
        normalized, count = counts.most_common(1)[0]
        if count != len(candidates):
            if count > len(candidates) / 2 and all(
                _identity_key(candidate).startswith(
                    _identity_key(normalized)
                )
                for candidate in candidates
            ):
                return next(
                    candidate
                    for candidate in candidates
                    if _identity_key(candidate) == normalized
                ), False
            return "", True
        return next(
            candidate
            for candidate in candidates
            if _identity_key(candidate) == normalized
        ), False

    filename_candidates = []
    for path in video_paths:
        candidate = _identity_query_value(PurePosixPath(path).name)
        if _is_usable_identity(candidate) or (
            _is_numeric_identity(candidate)
            and _has_strong_file_identity_evidence(path)
        ):
            filename_candidates.append(candidate)
    filename_consensus, filename_conflict = consensus(
        filename_candidates,
        allow_numeric=True,
    )
    if filename_consensus:
        return filename_consensus, False
    if filename_conflict:
        return "", True

    directory_candidates = []
    for path in video_paths:
        parent_parts = PurePosixPath(path).parent.parts
        candidate = ""
        for part in reversed(parent_parts):
            query = _identity_query_value(part)
            if _is_usable_identity(query):
                candidate = query
                break
        if candidate:
            directory_candidates.append(candidate)
    if len(directory_candidates) != len(video_paths):
        return "", False
    return consensus(directory_candidates)


def _has_strong_file_identity_evidence(path: str) -> bool:
    name = PurePosixPath(path).name
    return any(
        pattern.search(name)
        for pattern in (
            _EPISODE,
            _X_EPISODE,
            _UNSCOPED_EPISODE,
            _EP_ABSOLUTE,
            _DASH_EPISODE,
            _CHINESE_EPISODE,
            *_IDENTITY_MARKERS[-2:],
        )
    )


def _identity_query(payload: dict, video_paths: list[str]) -> str:
    root_query = _identity_query_value(_root_name(payload))
    file_query, file_conflict = _file_identity_consensus(video_paths)
    if file_conflict:
        return ""
    if file_query and _is_usable_identity(root_query):
        root_key = _identity_key(root_query)
        file_key = _identity_key(file_query)
        if root_key == file_key:
            return root_query
        root_tokens = _identity_tokens(root_query)
        file_tokens = _identity_tokens(file_query)
        if (
            root_tokens[:len(file_tokens)] == file_tokens
            or file_tokens[:len(root_tokens)] == root_tokens
        ):
            return max((root_query, file_query), key=lambda item: len(
                _identity_key(item)
            ))
    if file_query and (
        len(video_paths) > 1
        or (
            len(video_paths) == 1
            and _has_strong_file_identity_evidence(video_paths[0])
        )
    ):
        return file_query
    if _is_usable_identity(root_query):
        return root_query
    if file_query:
        return file_query
    release = payload.get("release")
    if isinstance(release, dict):
        release_query = _identity_query_value(release.get("title") or "")
        if _is_usable_identity(release_query):
            return release_query
    return ""


def _parse_chinese_number(value: str) -> int | None:
    value = _text(value).strip()
    if value.isdigit():
        return int(value)
    if not value or any(
        character not in _CHINESE_DIGITS and character not in "十百"
        for character in value
    ):
        return None
    total = 0
    current = 0
    for character in value:
        if character in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[character]
        elif character == "十":
            total += (current or 1) * 10
            current = 0
        elif character == "百":
            total += (current or 1) * 100
            current = 0
    return total + current


def _observed_markers(
    values: list[str],
) -> tuple[set[int], set[tuple[int, int]], set[int]]:
    seasons: set[int] = set()
    episodes: set[tuple[int, int]] = set()
    unscoped_episodes: set[int] = set()
    for value in values:
        value = _text(value)
        local_seasons: set[int] = set()
        for match in _EPISODE_CHAIN.finditer(value):
            season = int(match.group(1))
            local_seasons.add(season)
            episodes.update(
                (season, int(number))
                for number in re.findall(r"(?i)E(\d{1,4})", match.group(2))
            )
        for match in _EPISODE_RANGE.finditer(value):
            season = int(match.group(1))
            start, end = int(match.group(2)), int(match.group(3))
            local_seasons.add(season)
            if 0 <= start <= end and end - start <= 100:
                episodes.update((season, episode) for episode in range(
                    start, end + 1
                ))
        for pattern in (_EPISODE, _X_EPISODE):
            for match in pattern.finditer(value):
                season, episode = int(match.group(1)), int(match.group(2))
                local_seasons.add(season)
                episodes.add((season, episode))
        for match in _SEASON_RANGE.finditer(value):
            start, end = int(match.group(1)), int(match.group(2))
            if 0 <= start <= end and end - start <= 100:
                local_seasons.update(range(start, end + 1))
        for match in _SEASON.finditer(value):
            season = match.group(1) or match.group(2)
            if season is not None:
                local_seasons.add(int(season))
        for match in _CHINESE_SEASON.finditer(value):
            season = _parse_chinese_number(match.group(1))
            if season is not None:
                local_seasons.add(season)
        seasons.update(local_seasons)

        unscoped = {
            int(match.group(1))
            for pattern in (
                _UNSCOPED_EPISODE,
                _EP_ABSOLUTE,
                _DASH_EPISODE,
            )
            for match in pattern.finditer(value)
        }
        unscoped.update(
            int(match.group(1))
            for match in _BRACKET_EPISODE.finditer(value)
            if int(match.group(1)) not in {480, 576, 720, 1080, 2160}
        )
        unscoped.update(
            episode
            for match in _CHINESE_EPISODE.finditer(value)
            if (episode := _parse_chinese_number(match.group(1))) is not None
        )
        if len(local_seasons) == 1:
            bare_match = _BARE_EPISODE_STEM.fullmatch(
                PurePosixPath(value).stem
            )
            if bare_match:
                unscoped.add(int(bare_match.group(1)))
        if len(local_seasons) == 1:
            season = next(iter(local_seasons))
            episodes.update((season, episode) for episode in unscoped)
        else:
            unscoped_episodes.update(unscoped)
    return seasons, episodes, unscoped_episodes


def _probe_identity_contract(
    payload: dict,
    video_paths: list[str],
    identity_query: str,
) -> dict:
    """Build bounded, explainable identity evidence for recovery."""

    evidence = []
    candidates = []

    def add(source: str, candidate: str, relative_path: str = "") -> None:
        candidate = " ".join(_text(candidate).split())
        allow_numeric = source.startswith("filename")
        if not (
            _is_usable_identity(candidate, allow_numeric=allow_numeric)
            or (allow_numeric and _is_numeric_identity(candidate))
        ):
            return
        key = _identity_key(candidate)
        if key and not any(_identity_key(item) == key for item in candidates):
            candidates.append(candidate)
        if len(evidence) >= 12:
            return
        record = {"source": source, "candidate": candidate}
        if relative_path:
            record["relative_path"] = relative_path
        if not any(
            item.get("source") == source
            and _identity_key(item.get("candidate") or "") == key
            and item.get("relative_path", "") == relative_path
            for item in evidence
        ):
            evidence.append(record)

    if identity_query:
        add("selected", identity_query)
    root_query = _identity_query_value(_root_name(payload))
    add("root", root_query)
    file_query, file_conflict = _file_identity_consensus(video_paths)
    add("filename_consensus", file_query)
    for path in video_paths[:8]:
        add(
            "filename",
            _identity_query_value(PurePosixPath(path).name),
            path,
        )
        bracket_groups = _BRACKET_GROUP.findall(PurePosixPath(path).name)
        for candidate in _bracket_identity_candidates(bracket_groups)[:4]:
            add("bracket_identity", candidate, path)
    release = payload.get("release")
    if isinstance(release, dict):
        add("release", _identity_query_value(release.get("title") or ""))

    reasons = []
    numeric = _is_numeric_identity(identity_query)
    if numeric:
        reasons.append("numeric_title")
    if file_conflict:
        reasons.append("identity_conflict")
    complex_bracket_release = any(
        "【" in PurePosixPath(path).name
        for path in video_paths[:8]
    )
    noisy = bool(identity_query and (
        _SITE_PREFIX.search(identity_query)
        or re.search(r"(?i)(?:\bEP\d+|\[[^\]]+\]|\|)", identity_query)
        or complex_bracket_release
    ))
    if noisy:
        reasons.append("unsupported_release_syntax")
    if not identity_query:
        reasons.append("missing_identity")

    if not identity_query or file_conflict or noisy:
        confidence = "low"
        requires_recovery = True
    elif numeric:
        confidence = "medium"
        requires_recovery = False
    elif file_query and len(video_paths) > 1:
        confidence = "high"
        requires_recovery = False
    elif file_query and _identity_key(file_query) == _identity_key(root_query):
        confidence = "high"
        requires_recovery = False
    else:
        confidence = "medium"
        requires_recovery = False
    return {
        "identity_candidates": candidates[:8],
        "query_confidence": confidence,
        "query_evidence": evidence[:12],
        "requires_recovery": requires_recovery,
        "recovery_reasons": list(dict.fromkeys(reasons)),
    }


def build_metadata_probe(payload: dict) -> dict:
    """Return a root identity query and a separate, bounded content shape."""

    paths = []
    video_paths = []
    subtitle_paths = []
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
        if _VIDEO.search(path) and _is_primary_video(path):
            video_paths.append(path)
        elif _SUBTITLE.search(path):
            subtitle_paths.append(path)
    identity_paths = video_paths or subtitle_paths
    marker_values = identity_paths or paths or [
        _root_name(payload),
        str(
            (payload.get("release") or {}).get("title") or ""
        ) if isinstance(payload.get("release"), dict) else "",
    ]
    seasons, episodes, unscoped_episodes = _observed_markers(marker_values)
    if len(seasons) > 1:
        shape = "multi_season_pack"
    elif len(episodes) > 1:
        shape = "season_pack"
    elif len(episodes) == 1:
        shape = "single_episode"
    elif len(unscoped_episodes) > 1:
        shape = "episode_pack_unscoped"
    elif len(unscoped_episodes) == 1:
        shape = "single_episode_unscoped"
    elif len(seasons) == 1:
        shape = "season_pack"
    elif len(video_paths) == 1:
        shape = "movie"
    else:
        shape = "unknown"
    identity_query = _identity_query(payload, identity_paths)
    identity_contract = _probe_identity_contract(
        payload,
        identity_paths,
        identity_query,
    )
    year_match = re.search(
        r"(?<!\d)(19\d{2}|20\d{2})(?!\d)",
        " ".join((
            _text(_root_name(payload)),
            identity_query,
            *(
                _text(PurePosixPath(path).name)
                for path in identity_paths[:8]
            ),
        )),
    )
    return {
        "identity_query": identity_query,
        **identity_contract,
        "year_hint": year_match.group(1) if year_match else "",
        "content_shape": shape,
        "observed_seasons": sorted(seasons),
        "observed_episodes": [{
            "season_number": season,
            "episode_number": episode,
        } for season, episode in sorted(episodes)] + [{
            "season_number": None,
            "episode_number": episode,
        } for episode in sorted(unscoped_episodes)],
        "video_count": len(video_paths),
        "subtitle_count": len(subtitle_paths),
    }
