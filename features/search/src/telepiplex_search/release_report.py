"""Compact, explainable Prowlarr result reports for Telegram."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

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


def _technical_text(value: object) -> str:
    # Scene names use underscores as separators; ASCII boundaries also allow
    # technical labels immediately beside a Chinese/Japanese title.
    return unicodedata.normalize("NFKC", str(value or "")).replace("_", ".")


def _token(pattern: str) -> str:
    return rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])"


def _resolution(title: str) -> str:
    for pattern, label in (
        (_token(r"2160p|4k|uhd"), "2160p"),
        (_token(r"1080p"), "1080p"),
        (_token(r"1080i"), "1080i"),
        (_token(r"720p"), "720p"),
        (_token(r"576p"), "576p"),
        (_token(r"480p"), "480p"),
        (_token(r"(?:3840|4096)\s*[x×]\s*2160"), "2160p"),
        (_token(r"1920\s*[x×]\s*1080"), "1080p"),
        (_token(r"1280\s*[x×]\s*720"), "720p"),
    ):
        if _matches(title, pattern):
            return label
    return ""


def _source(title: str) -> str:
    for pattern, label in (
        (_token(r"remux"), "REMUX"),
        (_token(r"web[ .-]?dl"), "WEB-DL"),
        (_token(r"web[ .-]?rip"), "WEBRip"),
        (_token(r"blu[ .-]?ray|b[dr]rip") + r"|蓝光|藍光", "BluRay"),
        (_token(r"hdtv"), "HDTV"),
        (_token(r"dvd[ .-]?rip|dvd"), "DVD"),
    ):
        if _matches(title, pattern):
            return label
    return ""


def _dynamic_range(title: str) -> list[str]:
    labels = []
    if _matches(title, _token(r"dv|dovi|dolby[ .-]?vision") + r"|杜比视界|杜比視界"):
        labels.append("DV")
    if _matches(title, _token(r"hdr10(?:[+]|plus|p)")):
        labels.append("HDR10+")
    elif _matches(title, _token(r"hdr10")):
        labels.append("HDR10")
    elif _matches(title, _token(r"hdr")):
        labels.append("HDR")
    elif _matches(title, _token(r"hlg")):
        labels.append("HLG")
    elif not labels and _matches(title, _token(r"sdr")):
        labels.append("SDR")
    return labels


def _video_codec(title: str) -> str:
    for pattern, label in (
        (r"hevc|h[ .-]?265|x265", "HEVC"),
        (r"avc|h[ .-]?264|x264", "AVC"),
        (r"av1", "AV1"),
        (r"vp9", "VP9"),
    ):
        if _matches(title, _token(pattern)):
            return label
    return ""


def _bit_depth(title: str) -> str:
    match = re.search(_token(r"(8|10|12)[ .-]?bits?"), title, re.IGNORECASE)
    if match:
        return f"{match.group(1)}bit"
    return "10bit" if _matches(title, _token(r"hi10p")) else ""


def _audio_codecs(title: str) -> list[str]:
    # Consume compound names first, so DTS-HD MA does not also emit DTS.
    patterns = (
        (r"true[ .-]?hd", "TrueHD"),
        (r"dts[ .-]?(?:hd[ .-]?)?ma", "DTS-HD MA"),
        (r"dts[ .-]?hd[ .-]?hra", "DTS-HD HRA"),
        (r"dts[ .-]?hd", "DTS-HD"),
        (r"dts[ .:-]?x(?!26[45])", "DTS:X"),
        (r"dts", "DTS"),
        (r"e[ .-]?ac[ .-]?3|ddp|dd[+]|dolby[ .-]?digital[ .-]?plus", "DD+"),
        (r"ac[ .-]?3|dd|dolby[ .-]?digital", "DD"),
        (r"flac", "FLAC"),
        (r"lpcm|pcm", "PCM"),
        (r"aac", "AAC"),
        (r"opus", "Opus"),
        (r"mp3", "MP3"),
    )
    remaining = title
    labels = []
    for pattern, label in patterns:
        pattern = rf"(?<![a-z0-9])(?:{pattern})(?=$|[^a-z0-9]|\d)"
        if _matches(remaining, pattern):
            labels.append(label)
            remaining = re.sub(pattern, " ", remaining, flags=re.IGNORECASE)
    return labels


_AUDIO_FORMAT = (
    r"(?:true[ .-]?hd|dts(?:[ .-]?hd(?:[ .-]?(?:ma|hra))?)?|"
    r"e[ .-]?ac[ .-]?3|ac[ .-]?3|ddp?|dd[+]|aac|flac|pcm|lpcm|opus)"
)
_TECHNICAL_MARKER = re.compile(
    rf"(?<![a-z0-9])(?:\d{{3,4}}[pi]|4k|uhd|blu[ .-]?ray|"
    rf"web[ .-]?(?:dl|rip)|hdtv|x26[45]|h[ .-]?26[45]|hevc|"
    rf"{_AUDIO_FORMAT})(?=$|[^a-z0-9]|\d)", re.IGNORECASE,
)


def _channel_count(title: str) -> int | None:
    counts = []
    technical_start = _TECHNICAL_MARKER.search(title)
    for layout in re.finditer(
        r"(?<!\d)(\d{1,2})\.(\d)(?:\.(\d))?(?!\d)", title,
    ):
        parts = [int(value) for value in layout.groups() if value is not None]
        if (
            1 <= parts[0] <= 12
            and parts[1] <= 2
            and all(value <= 8 for value in parts[2:])
        ):
            # A decimal in the work title (e.g. Movie 3.0) before the
            # technical suffix is not an audio layout.
            adjacent_audio = _matches(
                title[:layout.start()], rf"{_AUDIO_FORMAT}[ .-]*$",
            ) or _matches(title[layout.end():], rf"^[ .-]*{_AUDIO_FORMAT}(?![a-z])")
            if (
                technical_start is None
                or layout.start() >= technical_start.start()
                or adjacent_audio
                or title[layout.end():].startswith("声道")
            ):
                counts.append(sum(parts))
    for explicit in re.finditer(
        r"(?<!\d)(\d{1,2})[ .-]?(?:ch(?:annels?)?|声道)(?![a-z0-9])",
        title,
        re.IGNORECASE,
    ):
        count = int(explicit.group(1))
        if 1 <= count <= 32:
            counts.append(count)
    for compact in re.finditer(
        rf"(?<![a-z0-9]){_AUDIO_FORMAT}[ .-]?(20|51|71)(?![a-z0-9])",
        title, re.IGNORECASE,
    ):
        counts.append(sum(int(value) for value in compact.group(1)))
    for pattern, count in (
        (_token(r"stereo") + r"|立体声|立體聲", 2),
        (_token(r"mono") + r"|单声道|單聲道", 1),
    ):
        match = re.search(pattern, title, re.IGNORECASE)
        if match and (
            technical_start is None or match.start() >= technical_start.start()
        ):
            counts.append(count)
    return max(counts) if counts else None


def _immersive(title: str) -> bool:
    return _matches(
        title,
        _token(r"atmos|dts[ .:-]?x|auro[ .-]?3d") + r"|杜比全景声|杜比全景聲",
    )


def _audio_tier(title: str, *, known_immersive: bool = False) -> str:
    channels = _channel_count(title)
    immersive = known_immersive or _immersive(title)
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
    title = _technical_text(item.get("title"))
    fallback_titles = [
        _technical_text(item.get("sort_title") or item.get("sortTitle")),
    ]
    for key in ("magnet_url", "magnetUrl", "download_url", "downloadUrl"):
        value = str(item.get(key) or "")
        if value.casefold().startswith("magnet:?"):
            try:
                names = parse_qs(urlsplit(value).query).get("dn", ())
            except ValueError:
                names = ()
            fallback_titles.extend(_technical_text(name) for name in names)
    # A contradictory alternate title is insufficient evidence for filling
    # missing facts. In particular, never copy 2160p/HDR audio into an SDR rip.
    compatible = []
    for fallback in fallback_titles:
        if all(
            not (primary := parser(title))
            or not (alternative := parser(fallback))
            or primary == alternative
            for parser in (
                _resolution, _source, _dynamic_range, _video_codec, _bit_depth,
            )
        ) and (
            not (primary_codecs := set(_audio_codecs(title)))
            or not (alternative_codecs := set(_audio_codecs(fallback)))
            or bool(primary_codecs & alternative_codecs)
        ) and (
            (primary_channels := _channel_count(title)) is None
            or (alternative_channels := _channel_count(fallback)) is None
            or primary_channels == alternative_channels
        ):
            compatible.append(fallback)
    evidence = tuple(dict.fromkeys(value for value in (title, *compatible) if value))
    labels = []
    for parser in (_resolution, _source, _dynamic_range, _video_codec, _bit_depth):
        value = next((value for text in evidence if (value := parser(text))), None)
        if isinstance(value, list):
            labels.extend(value)
        elif value:
            labels.append(value)
    labels.extend(_editions(title))
    # Select audio evidence as a whole so a known stereo title keeps its own
    # codec/channel capabilities even if a fallback describes another track.
    audio_title = next(
        (text for text in evidence if _channel_count(text) is not None),
        next((text for text in evidence if _immersive(text)), title),
    )
    codecs = list(dict.fromkeys((*_audio_codecs(title), *_audio_codecs(audio_title))))
    if not codecs:
        codecs = next((value for text in evidence if (value := _audio_codecs(text))), [])
    if codecs:
        labels.append(" / ".join(codecs))
    labels.append(_audio_tier(audio_title, known_immersive=_immersive(title)))
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
