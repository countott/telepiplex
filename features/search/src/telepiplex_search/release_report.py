"""Compact, explainable Prowlarr result reports for Telegram."""

from __future__ import annotations

import re

from .release_identity import stable_release_id


_CIRCLED = tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫")
_RESULT_LINE_LIMIT = 180
_GROUP_TOKEN = re.compile(r"(?i)(?:-|[ ._])([a-z][a-z0-9]{1,19})$")
_KNOWN_TRAILING_TOKENS = {
    "aac", "ac3", "atmos", "av1", "avc", "bluray", "dd", "ddp",
    "dts", "dtsma", "dtsx", "dv", "dovi", "eac3", "flac", "h264",
    "h265", "hdr", "hdr10", "hevc", "remastered", "remux", "truehd",
    "uhd", "web", "webdl", "webrip", "x264", "x265",
}


def _clip(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:max(1, limit - 1)].rstrip() + "…"


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _approx_size_label(value) -> str:
    size = max(0, _safe_int(value))
    if not size:
        return "未知大小"
    gib = size / 1024 ** 3
    if abs(gib - round(gib)) < 0.05:
        return f"{max(1, int(round(gib)))} GB"
    return f"{gib:.1f} GB"


def _matches(title: str, pattern: str) -> bool:
    return re.search(pattern, title, re.IGNORECASE) is not None


def _resolution(title: str) -> str:
    for pattern, label in (
        (r"(?<!\w)(?:2160p|4k|uhd)(?!\w)", "2160p"),
        (r"(?<!\w)1080p(?!\w)", "1080p"),
        (r"(?<!\w)1080i(?!\w)", "1080i"),
        (r"(?<!\w)720p(?!\w)", "720p"),
    ):
        if _matches(title, pattern):
            return label
    return ""


def _source(title: str) -> str:
    for pattern, label in (
        (r"(?<!\w)remux(?!\w)", "REMUX"),
        (r"(?<!\w)web[ ._-]?dl(?!\w)", "WEB-DL"),
        (r"(?<!\w)webrip(?!\w)", "WEBRip"),
        (r"(?<!\w)blu[ ._-]?ray(?!\w)|(?<!\w)b[dr]rip(?!\w)", "BluRay"),
        (r"(?<!\w)hdtv(?!\w)", "HDTV"),
    ):
        if _matches(title, pattern):
            return label
    return ""


def _dynamic_range(title: str) -> list[str]:
    labels = []
    if _matches(title, r"(?<!\w)(?:dv|dovi)(?!\w)|dolby[ ._-]?vision"):
        labels.append("DV")
    if _matches(title, r"(?<!\w)hdr10[+](?!\w)"):
        labels.append("HDR10+")
    elif _matches(title, r"(?<!\w)hdr10(?!\w)"):
        labels.append("HDR10")
    elif _matches(title, r"(?<!\w)hdr(?!\w)"):
        labels.append("HDR")
    return labels


def _channel_count(title: str) -> int | None:
    layout = re.search(
        r"(?<!\d)(\d{1,2})\.(\d)(?:\.(\d))?(?!\d)",
        title,
    )
    if layout:
        parts = [
            int(value)
            for value in layout.groups()
            if value is not None
        ]
        if (
            parts[0] <= 12
            and parts[1] <= 2
            and all(value <= 8 for value in parts[2:])
        ):
            return sum(parts)
    explicit = re.search(
        r"(?<![a-z0-9])(\d{1,2})[ ._-]?ch(?![a-z0-9])",
        title,
        re.IGNORECASE,
    )
    if explicit:
        return int(explicit.group(1))
    return None


def _audio_tier(title: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "", title.casefold())
    immersive = any(
        marker in compact
        for marker in ("atmos", "dtsx", "auro3d")
    )
    channels = _channel_count(title)
    if channels is None:
        return "?ch沉浸" if immersive else "?ch"
    if immersive:
        return f"{channels}ch沉浸"
    if channels == 2:
        return "2ch立体"
    if channels > 2:
        return f"{channels}ch环绕"
    return f"{channels}ch"


def _editions(title: str) -> list[str]:
    labels = []
    for pattern, label in (
        (r"(?<!\w)hybrid(?!\w)", "Hybrid"),
        (r"(?<!\w)remastered(?!\w)", "Remastered"),
        (r"(?<!\w)imax(?!\w)", "IMAX"),
        (r"(?<!\w)extended(?!\w)", "Extended"),
        (r"(?<!\w)criterion(?!\w)", "Criterion"),
        (r"director(?:'s)?[ ._-]?cut", "Director's Cut"),
        (r"(?<!\w)unrated(?!\w)", "Unrated"),
    ):
        if _matches(title, pattern):
            labels.append(label)
    return labels


def _specifications(item: dict) -> str:
    title = str(item.get("title") or "")
    labels = []
    labels.extend(filter(None, (
        _resolution(title),
        _source(title),
    )))
    labels.extend(_dynamic_range(title))
    labels.extend(_editions(title))
    labels.append(_audio_tier(title))
    return " · ".join(dict.fromkeys(labels)) or "规格未知"


def _compact_scope(value) -> str:
    scope = _clip(value or "门禁通过", 28)
    if scope in {"电影", "门禁通过", "movie"}:
        return "整片"
    season = re.fullmatch(r"第\s*(\d+)\s*季整季", scope)
    if season:
        return f"第{season.group(1)}季整季"
    return scope


def _shared_scope(items: list[dict]) -> str:
    scopes = {
        scope
        for scope in (
            _compact_scope(item.get("scope_label"))
            for item in items
        )
        if scope != "整片"
    }
    if len(scopes) == 1:
        return scopes.pop()
    return ""


def _release_group(title: str) -> str:
    match = _GROUP_TOKEN.search(str(title or "").strip())
    if match is None:
        return ""
    candidate = match.group(1)
    key = candidate.casefold().replace("+", "")
    if (
        key in _KNOWN_TRAILING_TOKENS
        or re.fullmatch(
            r"(?:19|20)\d{2}|\d{3,4}p|s\d{1,2}(?:e\d{1,3})?|"
            r"\d+(?:\.\d+)?",
            key,
        )
    ):
        return ""
    return candidate


def _display_versions(ranked) -> list[dict]:
    versions = []
    by_fingerprint = {}
    for item in ranked or []:
        if not isinstance(item, dict):
            continue
        title = re.sub(
            r"[\s._-]+", " ", str(item.get("title") or "").casefold()
        ).strip()
        size = _safe_int(item.get("size"))
        fingerprint = (title, size) if title and size else (
            stable_release_id(item),
        )
        existing = by_fingerprint.get(fingerprint)
        if existing is None:
            representative = dict(item)
            representative["_source_count"] = 1
            versions.append(representative)
            by_fingerprint[fingerprint] = representative
        else:
            existing["_source_count"] += 1
            values = list(existing.get("_explicit_seeders") or [])
            if not values:
                try:
                    values.append(int(existing.get("seeders")))
                except (TypeError, ValueError):
                    pass
            try:
                values.append(int(item.get("seeders")))
            except (TypeError, ValueError):
                pass
            existing["_explicit_seeders"] = values
            if values:
                existing["seeders"] = max(values)
    return versions[:12]


def _seed_status(item: dict) -> str:
    raw_values = (
        item.get("_explicit_seeders")
        if isinstance(item.get("_explicit_seeders"), list)
        else [item.get("seeders")] if "seeders" in item else []
    )
    values = []
    for raw in raw_values:
        try:
            values.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        return "疑似死种"
    maximum = max(values)
    if maximum >= 3:
        return "活种"
    if maximum >= 1:
        return "疑似死种"
    return "死种"


def _result_lines(index: int, item: dict) -> tuple[str, str]:
    specification = _clip(
        f"{_CIRCLED[index]} {_specifications(item)}",
        _RESULT_LINE_LIMIT,
    )
    availability = (
        f"   {_approx_size_label(item.get('size'))}"
        f"｜{_seed_status(item)}"
    )
    return specification, availability


def release_keyboard(plan_id: str, ranked) -> list[list[dict]]:
    releases = _display_versions(ranked)
    buttons = [{
        "text": _CIRCLED[index],
        "callback_data": (
            f"search:release:{plan_id}:{stable_release_id(item)}"
        ),
    } for index, item in enumerate(releases)]
    keyboard = [
        buttons[index:index + 3]
        for index in range(0, len(buttons), 3)
    ]
    keyboard.append([{
        "text": "退出",
        "callback_data": f"search:cancel:{plan_id}",
    }])
    return keyboard


def format_release_report(
    query: str,
    gate,
    ranked: list[dict],
    indexer_summary: dict,
) -> str:
    del gate
    summary = indexer_summary if isinstance(indexer_summary, dict) else {}
    enabled = summary.get("enabled_indexers") or []
    total = _safe_int(summary.get("total_indexers") or len(enabled))
    completed = _safe_int(
        summary.get("completed_indexers")
        if summary.get("completed_indexers") is not None
        else total
    )
    down = [
        item for item in summary.get("down_indexers") or []
        if isinstance(item, dict)
    ]
    releases = [
        item for item in (ranked or [])
        if isinstance(item, dict)
    ][:12]
    displayed = _display_versions(releases)
    offline = len(down)
    online_completed = max(0, completed - offline)
    final = bool(
        summary.get("final")
        if summary.get("final") is not None
        else completed >= total
    )
    scope = _shared_scope(displayed)
    if scope:
        title_limit = max(1, 120 - len(scope) - 3)
        title = f"{_clip(query, title_limit) or '未知作品'} · {scope}"
    else:
        title = _clip(query, 120) or "未知作品"
    lines = [
        f"{'✅' if final else '🔍'} {title}",
        f"搜索器 {online_completed}/({total}-{offline})，离线 {offline}",
    ]
    if displayed:
        lines.append("")
    if not displayed:
        lines.append("没有同身份、同范围的可用片源。")
    for index, item in enumerate(displayed):
        lines.extend(_result_lines(index, item))
    text = "\n".join(lines)
    if len(text) > 4096:
        text = text[:4095].rstrip() + "…"
    return text
