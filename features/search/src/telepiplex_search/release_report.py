"""Compact, explainable Prowlarr result reports for Telegram."""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from .release_formats import (
    AUDIO_CODEC_PARENTS,
    ReleaseSpecs,
    normalize_release_text,
    parse_release_specs,
)
from .release_identity import stable_release_id


_CIRCLED = tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")
_RESULT_LINE_LIMIT = 180
_GROUP_TOKEN = re.compile(r"(?i)(?:-|[ ._])([a-z][a-z0-9]{1,19})$")
_KNOWN_TRAILING_TOKENS = {
    "aac", "ac3", "atmos", "av1", "avc", "bluray", "dd", "ddp",
    "dts", "dtsma", "dtsx", "dv", "dovi", "eac3", "flac", "h264",
    "h265", "hdr", "hdr10", "hevc", "remastered", "remux", "truehd",
    "uhd", "web", "webdl", "webrip", "x264", "x265",
}


def circled_number(number: int) -> str:
    if 1 <= number <= len(_CIRCLED):
        return _CIRCLED[number - 1]
    return str(number)


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


def _audio_tier(specs: ReleaseSpecs) -> str:
    channels = specs.channels
    if channels is None:
        return "?ch沉浸" if specs.immersive else "?ch"
    if specs.immersive:
        return f"{channels}ch沉浸"
    if channels == 2:
        return "2ch立体"
    if channels > 2:
        return f"{channels}ch环绕"
    return f"{channels}ch"


_SCALAR_FORMAT_FIELDS = (
    "resolution", "source", "video_codec", "bit_depth", "container", "frame_rate",
)


def _merge_dynamic_ranges(left: tuple, right: tuple) -> tuple | None:
    labels = set(left) | set(right)
    if "SDR" in labels and len(labels) > 1:
        return None
    if "HLG" in labels and labels.intersection({"HDR10", "HDR10+"}):
        return None
    if "HDR10+" in labels:
        labels.difference_update({"HDR10", "HDR"})
    elif labels.intersection({"HDR10", "HLG"}):
        labels.discard("HDR")
    return tuple(
        label for label in ("DV", "HDR10+", "HDR10", "HDR", "HLG", "SDR")
        if label in labels
    )


def _is_audio_parent(parent: str, child: str) -> bool:
    seen = set()
    while child in AUDIO_CODEC_PARENTS and child not in seen:
        seen.add(child)
        child = AUDIO_CODEC_PARENTS[child]
        if child == parent:
            return True
    return False


def _merge_audio_codecs(left: tuple, right: tuple) -> tuple | None:
    if left and right and not any(
        a == b or _is_audio_parent(a, b) or _is_audio_parent(b, a)
        for a in left for b in right
    ):
        return None
    codecs = tuple(dict.fromkeys((*left, *right)))
    # A parent and subtype explicitly listed in one title may be separate
    # tracks. Refining a generic fallback must not erase that evidence.
    independent_parents = {
        codec for group in (left, right) for codec in group
        if any(_is_audio_parent(codec, other) for other in group)
    }
    return tuple(
        codec for codec in codecs
        if codec in independent_parents
        or not any(_is_audio_parent(codec, other) for other in codecs)
    )


def _merge_specs(primary: ReleaseSpecs, alternative: ReleaseSpecs) -> ReleaseSpecs:
    """Fill missing fields from compatible evidence of this same release."""
    for name in _SCALAR_FORMAT_FIELDS:
        left, right = getattr(primary, name), getattr(alternative, name)
        if left and right and left != right:
            return primary
    dynamic_range = _merge_dynamic_ranges(
        primary.dynamic_range, alternative.dynamic_range,
    )
    if dynamic_range is None:
        return primary
    audio_codecs = _merge_audio_codecs(
        primary.audio_codecs, alternative.audio_codecs,
    )
    if audio_codecs is None:
        return primary
    if (
        primary.channels is not None and alternative.channels is not None
        and primary.channels != alternative.channels
    ):
        return primary
    missing = {
        name: getattr(primary, name) or getattr(alternative, name)
        for name in _SCALAR_FORMAT_FIELDS
    }
    return replace(
        primary, **missing,
        dynamic_range=dynamic_range,
        audio_codecs=audio_codecs,
        channels=(
            primary.channels if primary.channels is not None
            else alternative.channels
        ),
        immersive=primary.immersive or alternative.immersive,
    )


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
    specs = parse_release_specs(title)
    fallback_titles = [item.get("sort_title") or item.get("sortTitle")]
    for key in ("magnet_url", "magnetUrl", "download_url", "downloadUrl"):
        value = str(item.get(key) or "")
        if value.casefold().startswith("magnet:?"):
            try:
                fallback_titles.extend(parse_qs(urlsplit(value).query).get("dn", ()))
            except ValueError:
                continue
    for fallback in dict.fromkeys(str(value) for value in fallback_titles if value):
        specs = _merge_specs(specs, parse_release_specs(fallback))
    labels = list(filter(None, (specs.resolution, specs.source)))
    labels.extend(specs.dynamic_range)
    labels.extend(filter(None, (
        specs.video_codec, specs.bit_depth, specs.container, specs.frame_rate,
    )))
    labels.extend(_editions(normalize_release_text(title)))
    if specs.audio_codecs:
        labels.append(" / ".join(specs.audio_codecs))
    labels.append(_audio_tier(specs))
    return " · ".join(dict.fromkeys(labels))


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
    *,
    search_queries: list[str] | None = None,
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
    failed = len(down)
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
    del search_queries
    if final:
        lines = [title, f"搜索完成：{completed}/{total}"]
    else:
        lines = [f"正在搜索：{title}", f"搜索进度：{completed}/{total}"]
    if failed:
        lines[-1] += f"，失败 {failed}"
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
