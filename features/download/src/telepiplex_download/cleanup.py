from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


MIB = 1024 * 1024
DEFAULT_MINIMUM_VIDEO_SIZE_MIB = 100
MAXIMUM_VIDEO_SIZE_MIB = 10240
VIDEO_EXTENSIONS = frozenset({
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".ts",
    ".webm",
    ".wmv",
})


class DownloadCleanupError(RuntimeError):
    pass


@dataclass(frozen=True)
class CleanupPlan:
    retained_video_paths: tuple[str, ...]
    rejected_paths: tuple[str, ...]


def normalize_minimum_video_size_mib(value) -> int:
    if isinstance(value, bool):
        raise ValueError("最小视频体积必须是 0–10240 之间的整数")
    if isinstance(value, int):
        size_mib = value
    else:
        text = str(value or "").strip()
        if not text.isdecimal():
            raise ValueError("最小视频体积必须是 0–10240 之间的整数")
        size_mib = int(text)
    if not 0 <= size_mib <= MAXIMUM_VIDEO_SIZE_MIB:
        raise ValueError("最小视频体积必须是 0–10240 之间的整数")
    return size_mib


def configured_minimum_video_size_mib(config: dict) -> int:
    raw_value = config.get(
        "minimum_video_size_mib",
        DEFAULT_MINIMUM_VIDEO_SIZE_MIB,
    )
    try:
        return normalize_minimum_video_size_mib(raw_value)
    except ValueError as exc:
        raise DownloadCleanupError(str(exc)) from exc


def configured_minimum_video_size_bytes(config: dict) -> int:
    return configured_minimum_video_size_mib(config) * MIB


def plan_download_cleanup(
    file_tree: list[dict],
    *,
    minimum_video_size_bytes: int,
) -> CleanupPlan:
    retained = []
    rejected = []
    for item in file_tree if isinstance(file_tree, list) else ():
        if not isinstance(item, dict) or item.get("is_dir"):
            continue
        path = str(item.get("path") or "").strip()
        name = str(item.get("name") or PurePosixPath(path).name).strip()
        if not path or not name:
            raise DownloadCleanupError("下载文件树包含无法定位的文件")
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError) as exc:
            raise DownloadCleanupError(
                f"下载文件大小无效：{name}"
            ) from exc
        is_video = PurePosixPath(name).suffix.casefold() in VIDEO_EXTENSIONS
        if is_video and size >= minimum_video_size_bytes:
            retained.append(path)
        else:
            rejected.append(path)
    if not retained:
        raise DownloadCleanupError(
            "下载内容中没有达到最小体积门槛的视频文件"
        )
    return CleanupPlan(tuple(retained), tuple(rejected))
