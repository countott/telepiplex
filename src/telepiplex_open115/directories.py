from __future__ import annotations


def _single_line(value, *, field: str) -> str:
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise ValueError(
            f"open115 save directory {field} must be one non-empty line"
        )
    return text


def normalize_save_directory_path(value) -> str:
    path = _single_line(value, field="path")
    if path.startswith("/"):
        raise ValueError(
            "open115 save directory path must start from the 115 root folder "
            "without a leading slash"
        )
    path = path.rstrip("/")
    parts = path.split("/")
    if not path or any(not part or part in {".", ".."} for part in parts):
        raise ValueError(
            "open115 save directory path must contain safe root-relative segments"
        )
    return path


def normalize_save_directories(value) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("open115 save_directories must be a list")
    normalized = []
    names = set()
    paths = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "path"}:
            raise ValueError(
                "open115 save directory must contain only name and path"
            )
        name = _single_line(item.get("name"), field="name")
        path = normalize_save_directory_path(item.get("path"))
        if name in names or path in paths:
            raise ValueError(
                "open115 save directory name and path must be unique"
            )
        names.add(name)
        paths.add(path)
        normalized.append({"name": name, "path": path})
    return normalized
