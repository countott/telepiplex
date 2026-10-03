"""Caption's metadata selection view, following Rename's candidate layout."""

from collections.abc import Iterable, Mapping
from itertools import islice
from urllib.parse import urlsplit


def _text(value: object, limit: int) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _countries(value: object) -> str:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)):
        return "地区未知"
    countries = [_text(item, 24) for item in values[:6]]
    return _text("、".join(item for item in countries if item), 80) or "地区未知"


def _poster_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username:
            return value
    except ValueError:
        pass
    return ""


def metadata_choice_view(
    candidates: Iterable[Mapping[str, object]],
    operation_id: str,
    *,
    video_path: str = "",
) -> tuple[str, dict]:
    """Render up to five numbered candidates without changing their indices."""
    lines = ["请选择要查找外挂字幕的作品："]
    if path := _text(video_path, 240):
        lines.append(f"当前文件：{path}")
    keyboard = []
    poster_items = []
    for index, value in enumerate(islice(candidates or (), 5)):
        candidate = value if isinstance(value, Mapping) else {}
        title = _text(candidate.get("title"), 160) or "未知作品"
        original = _text(candidate.get("original_title"), 160)
        display_title = (
            f"{title} ({original})"
            if original and original.casefold() != title.casefold()
            else title
        )
        media_type = _text(candidate.get("media_type_label"), 16)
        if media_type not in {"电影", "剧集", "动画电影", "动画剧集"}:
            media_type = {"movie": "电影", "series": "剧集", "tv": "剧集"}.get(
                _text(candidate.get("media_type"), 16), "类型未知"
            )
        year = _text(candidate.get("year"), 16) or "年份未知"
        countries = _countries(candidate.get("countries"))
        lines.append(f"{index + 1}. {display_title}\n   {year}｜{countries}｜{media_type}")
        keyboard.append([{
            "text": f"{index + 1}. {_text(title, 24)}",
            "callback_data": f"caption:choose:{operation_id[:12]}:{index}",
        }])
        poster_items.append({
            "number": index + 1,
            "title": title,
            "poster_url": _poster_url(candidate.get("poster_url")),
        })
    lines.append("点击作品按钮或回复编号进行选择。")
    details = {"keyboard": keyboard}
    if any(item["poster_url"] for item in poster_items):
        details["poster_items"] = poster_items
    return "\n".join(lines), details
