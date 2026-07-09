# -*- coding: utf-8 -*-

from concurrent.futures import ThreadPoolExecutor


download_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="Media_Search_Handoff")


def _escape_markdown_v2(value) -> str:
    text = str(value or "")
    for char in r"_*[]()~`>#+-=|{}.!\\":
        text = text.replace(char, f"\\{char}")
    return text


def download_task(link, selected_path, user_id, naming_metadata=None, metadata=None):
    """Handoff contract consumed by media search and implemented by main/115 integration."""
    from app.utils.message_queue import add_task_to_queue

    add_task_to_queue(
        user_id,
        None,
        message=_escape_markdown_v2(
            "✅ 已确认媒体搜索候选。\n\n"
            f"链接：{link}\n"
            f"保存目录：{selected_path}\n\n"
            "media-search 分支只负责搜索与候选确认；实际 115 投递由 main 缝合实现。",
            version=2,
        ),
    )
    return {
        "handoff": "download_task",
        "link": link,
        "selected_path": selected_path,
        "user_id": user_id,
        "naming_metadata": naming_metadata,
        "metadata": metadata,
    }
