from __future__ import annotations

from telepiplex_plugin_sdk.storage_paths import normalize_115_directory


def _single_line(value, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise ValueError(
            f"download save directory {field} must be one non-empty line"
        )
    return text


def normalize_save_directory_path(value) -> str:
    """Adapt canonical 115 paths to Download's legacy root-relative snapshot."""
    return normalize_115_directory(value, allow_relative=True, allow_root=False)[1:]


def normalize_save_directories(value) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("download save_directories must be a list")
    normalized = []
    names = set()
    paths = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "path"}:
            raise ValueError(
                "download save directory must contain only name and path"
            )
        name = _single_line(item.get("name"), field="name")
        path = normalize_save_directory_path(item.get("path"))
        if name in names or path in paths:
            raise ValueError(
                "download save directory name and path must be unique"
            )
        names.add(name)
        paths.add(path)
        normalized.append({"name": name, "path": path})
    return normalized
