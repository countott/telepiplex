"""Prowlarr field sorting and paged raw release presentation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re

from .release_identity import stable_release_id


PAGE_SIZE = 5
SORT_LABELS = {
    "age": "时间", "title": "标题", "size": "大小",
    "peers": "Peers", "indexer": "索引器", "grabs": "抓取数",
    "files": "文件数", "category": "分类", "protocol": "协议",
}


def raw_release_id(item: dict) -> str:
    # Preserve separate indexer rows even when they expose the same info hash.
    identity = "|".join((
        stable_release_id(item), str(item.get("indexer_id") or ""),
        str(item.get("indexer") or ""), str(item.get("guid") or ""),
    ))
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def selectable(item: dict) -> bool:
    return (
        str(item.get("protocol") or "").lower() in {"", "torrent"}
        and bool(item.get("magnet_url") or item.get("download_url"))
    )


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _date(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except (ValueError, TypeError):
        return None


def _sort_value(item, key, now):
    if key == "age":
        value = _number(item.get("age_minutes"))
        if value is not None:
            return value
        published = _date(item.get("publish_date"))
        return (now - published).total_seconds() / 60 if published else None
    if key == "peers":
        # Match Prowlarr's Peers predicate, not total peers or a quality score.
        return (_number(item.get("seeders")) or 0) * 1000000 + (
            _number(item.get("leechers")) or 0
        )
    if key in {"size", "files", "grabs"}:
        return _number(item.get(key))
    if key == "category":
        categories = item.get("categories") or []
        names = [str(cat.get("name") or "") for cat in categories if isinstance(cat, dict)]
        return next((name.casefold() for name in names if name), None)
    value = item.get("sort_title") or item.get("title") if key == "title" else item.get(key)
    return str(value).casefold() if value else None


def sorted_releases(items, key="age", descending=False):
    now = datetime.now(timezone.utc)
    # Prowlarr defaults to title as its secondary sort. Unknown values go last
    # in either direction; sorting never mutates the cached result rows.
    values = [(item, _sort_value(item, key, now)) for item in items]
    known = [(item, value) for item, value in values if value is not None]
    known.sort(key=lambda pair: str(pair[0].get("sort_title") or pair[0].get("title") or "").casefold())
    known.sort(key=lambda pair: pair[1], reverse=descending)
    return [item for item, _ in known] + [item for item, value in values if value is None]


def clipped_text(value, limit=220):
    text = str(value or "")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def result_view(plan_id, stored, *, prefix=""):
    results = sorted_releases(stored.get("results") or [], stored["raw_sort"], stored["raw_descending"])
    pages = max(1, math.ceil(len(results) / PAGE_SIZE))
    page = min(stored.get("raw_page", 0), pages - 1)
    stored["raw_page"] = page
    label = SORT_LABELS[stored["raw_sort"]]
    arrow = "↓" if stored["raw_descending"] else "↑"
    lines = [prefix] if prefix else []
    lines += [f"原文搜索：{clipped_text(stored['plan']['raw_query'])}", f"排序：{label} {arrow}", ""]
    choices = []
    for number, item in enumerate(results[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], 1):
        size = _number(item.get("size"))
        size_text = f"{size / 1024**3:.2f} GB" if size is not None else "大小未知"
        seeders = _number(item.get("seeders"))
        published = _date(item.get("publish_date"))
        date_text = published.strftime("%Y-%m-%d") if published else "时间未知"
        lines += [
            f"{number}. {clipped_text(item.get('title') or '未命名资源')}",
            f"{size_text} · 做种 {int(seeders) if seeders is not None else '未知'} · {date_text}",
            clipped_text(item.get("indexer") or "未知索引器", 60),
        ]
        if selectable(item):
            choices.append({"text": str(number), "callback_data": f"search:release:{plan_id}:{raw_release_id(item)}"})
        else:
            lines.append("无法投递：缺少下载链接或协议不受支持")
        lines.append("")
    if not results:
        lines.append("没有可用的搜索结果。")
    lines.append(f"第 {page + 1}/{pages} 页 · 共 {len(results)} 条")
    keyboard = [choices] if choices else []
    navigation = []
    for target, label in ((page - 1, "上一页"), (page + 1, "下一页")):
        if 0 <= target < pages:
            navigation.append({"text": label, "callback_data": f"search:raw_page:{plan_id}:{target}"})
    if navigation:
        keyboard.append(navigation)
    sorts = [{
        "text": label + (f" {arrow}" if key == stored["raw_sort"] else ""),
        "callback_data": f"search:raw_sort:{plan_id}:{key}",
    } for key, label in SORT_LABELS.items()]
    keyboard += [sorts[index:index + 3] for index in range(0, len(sorts), 3)]
    keyboard.append([{"text": "退出", "callback_data": f"search:cancel:{plan_id}"}])
    return {"kind": "edit_message", "text": "\n".join(lines), "data": {"keyboard": keyboard}}


def command_query(request):
    text = str(request.get("text") or "")
    match = re.match(r"^/pr(?:@\w+)?(?:\s+|$)", text, re.IGNORECASE)
    if match:
        return text[match.end():].strip()
    return " ".join(str(arg) for arg in request.get("args") or []).strip()
