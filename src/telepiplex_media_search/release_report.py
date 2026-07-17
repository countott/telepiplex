"""Compact, explainable Prowlarr result reports for Telegram."""

from __future__ import annotations


_CIRCLED = tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫")


def _clip(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:max(1, limit - 1)].rstrip() + "…"


def _signed(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return f"{number:+d}"


def _size_label(value) -> str:
    try:
        size = max(0, int(value or 0))
    except (TypeError, ValueError):
        size = 0
    if not size:
        return "未知"
    gib = size / 1024 ** 3
    return f"{gib:.1f} GiB"


def _score_parts(item: dict) -> tuple[str, str, str, str]:
    details = [
        detail
        for detail in item.get("score_details") or []
        if isinstance(detail, dict)
    ]
    keywords = [
        f"{_clip(detail.get('label'), 18)}({_signed(detail.get('score'))})"
        for detail in details
        if detail.get("kind") == "keyword"
        and int(detail.get("score") or 0) != 0
    ][:5]
    indexer = next(
        (
            f"{_clip(detail.get('label'), 22)}"
            f"({_signed(detail.get('score'))})"
            for detail in details
            if detail.get("kind") == "indexer"
        ),
        _clip(item.get("indexer") or "未知", 22),
    )
    seeders = next(
        (
            f"{detail.get('label')}({_signed(detail.get('score'))})"
            for detail in details
            if detail.get("kind") == "seeders"
        ),
        str(item.get("seeders") or 0),
    )
    size = next(
        (
            f"{_size_label(detail.get('label'))}"
            f"({_signed(detail.get('score'))})"
            for detail in details
            if detail.get("kind") == "size"
        ),
        _size_label(item.get("size")),
    )
    return "、".join(keywords) or "无", indexer, seeders, size


def release_keyboard(plan_id: str, count: int) -> list[list[dict]]:
    count = min(12, max(0, int(count or 0)))
    buttons = [{
        "text": _CIRCLED[index],
        "callback_data": f"media-search:release:{plan_id}:{index}",
    } for index in range(count)]
    keyboard = [
        buttons[index:index + 3]
        for index in range(0, len(buttons), 3)
    ]
    keyboard.append([{
        "text": "退出",
        "callback_data": f"media-search:cancel:{plan_id}",
    }])
    return keyboard


def _indexer_lines(indexer_summary: dict) -> list[str]:
    summary = indexer_summary if isinstance(indexer_summary, dict) else {}
    enabled = [
        _clip(item, 30)
        for item in summary.get("enabled_indexers") or []
        if str(item or "").strip()
    ][:12]
    counts = summary.get("result_sources") or {}
    count_text = "、".join(
        f"{_clip(name, 24)}={int(count)}"
        for name, count in sorted(counts.items())
    ) or "无"
    lines = [
        "Indexer："
        f"启用 {('、'.join(enabled) if enabled else '未取得')}；"
        f"原始返回 {count_text}"
    ]
    down = [
        f"{_clip(item.get('source') or 'Prowlarr', 22)}: "
        f"{_clip(item.get('message'), 60)}"
        for item in summary.get("down_indexers") or []
        if isinstance(item, dict)
    ][:6]
    if down:
        lines.append("Indexer 异常：" + "；".join(down))
    error = _clip(summary.get("error"), 180)
    if error:
        lines.append("Indexer 状态读取失败：" + error)
    return lines


def format_release_report(
    query: str,
    gate,
    ranked: list[dict],
    indexer_summary: dict,
) -> str:
    rejection_counts = getattr(gate, "rejection_counts", {}) or {}
    rejection_text = "、".join(
        f"{key}={int(value)}"
        for key, value in sorted(rejection_counts.items())
    ) or "无"
    eligible = len(getattr(gate, "eligible", ()) or ())
    lines = [
        f"Prowlarr Query：{_clip(query, 140)}",
        (
            "正确性门禁："
            f"原始 {int(getattr(gate, 'raw_count', 0) or 0)}，"
            f"合格 {eligible}，拒绝 {rejection_text}"
        ),
        *_indexer_lines(indexer_summary),
    ]
    if not ranked:
        lines.append("没有同身份、同范围的可用片源；未自动展示其他范围。")
    for index, item in enumerate((ranked or [])[:12]):
        keyword_text, indexer, seeders, size = _score_parts(item)
        lines.extend([
            f"{_CIRCLED[index]} {_clip(item.get('title'), 86)}",
            (
                f"范围：{_clip(item.get('scope_label') or '已通过门禁', 28)}"
                f"｜最终得分：{int(item.get('score') or 0)}"
            ),
            f"片源匹配关键词：{keyword_text}",
            (
                f"Indexer：{indexer}"
                f"｜做种：{seeders}"
                f"｜大小：{size}"
            ),
        ])
    text = "\n".join(lines)
    if len(text) > 4096:
        text = text[:4095].rstrip() + "…"
    return text
