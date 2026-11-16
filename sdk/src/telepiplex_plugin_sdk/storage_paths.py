"""Pure 115 cloud directory parsing; never use for local paths or HTTP routes."""

import unicodedata


def normalize_115_directory(value: str, *, allow_relative: bool, allow_root: bool) -> str:
    """Return an absolute cloud path without changing directory segment identity."""
    if not isinstance(value, str) or not value:
        raise ValueError("115 directory must be a non-empty string")
    if "\\" in value or any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("115 directory contains a backslash or control character")
    if not value.startswith("/"):
        if not allow_relative:
            raise ValueError("115 directory must be absolute")
        value = "/" + value
    if value == "/":
        if not allow_root:
            raise ValueError("115 root is not allowed for this directory role")
        return value
    if value.endswith("/"):
        value = value[:-1]
    if any(part in {"", ".", ".."} for part in value[1:].split("/")):
        raise ValueError("115 directory must contain safe non-empty segments")
    return value
