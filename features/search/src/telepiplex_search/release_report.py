"""Compact, explainable Prowlarr result reports for Telegram."""

from __future__ import annotations

import re

from .release_identity import stable_release_id


_CIRCLED = tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫")
_RESULT_LINE_LIMIT = 245


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
        return "~?G"
    gib = size / 1024 ** 3
    return f"~{max(1, int(gib + 0.5))}G"


def _specification_label(value) -> str:
    label = _clip(value, 18)
    if label.casefold() == "2160p":
        return "4K"
    return label


def _specifications(item: dict) -> str:
    labels = []
    seen = set()
    for detail in item.get("score_details") or []:
        if (
            not isinstance(detail, dict)
            or detail.get("kind") != "keyword"
            or _safe_int(detail.get("score")) == 0
        ):
            continue
        label = _specification_label(detail.get("label"))
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            labels.append(label)
    return " / ".join(labels) or "规格未知"


def _compact_scope(value) -> str:
    scope = _clip(value or "门禁通过", 28)
    if scope in {"电影", "门禁通过", "movie"}:
        return "整片"
    season = re.fullmatch(r"第\s*(\d+)\s*季整季", scope)
    if season:
        return f"第{season.group(1)}季整季"
    return scope


def _result_line(index: int, item: dict) -> str:
    prefix = (
        f"{_CIRCLED[index]} {_safe_int(item.get('score'))}分"
        f"｜{_compact_scope(item.get('scope_label'))}"
        f"｜{_specifications(item)}"
        f"｜做种{_safe_int(item.get('seeders'))}"
        f"｜{_approx_size_label(item.get('size'))}"
        "｜"
    )
    title_limit = max(1, _RESULT_LINE_LIMIT - len(prefix))
    return _clip(
        prefix + _clip(item.get("title"), title_limit),
        _RESULT_LINE_LIMIT,
    )


def release_keyboard(plan_id: str, ranked) -> list[list[dict]]:
    releases = [
        item for item in (ranked or [])
        if isinstance(item, dict)
    ][:12]
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
    abnormal_count = len(down) + bool(str(summary.get("error") or "").strip())
    displayed = [
        item for item in (ranked or [])
        if isinstance(item, dict)
    ][:12]
    lines = [
        f"🔍 {_clip(query, 180)}",
        (
            f"搜索结果 {len(displayed)}"
            f"｜索引器完成 {completed}/{total or '?'}"
            f"｜异常 {int(abnormal_count)}"
        ),
    ]
    if not displayed:
        lines.append("没有同身份、同范围的可用片源。")
    for index, item in enumerate(displayed):
        lines.append(_result_line(index, item))
    text = "\n".join(lines)
    if len(text) > 4096:
        text = text[:4095].rstrip() + "…"
    return text
