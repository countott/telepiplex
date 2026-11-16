"""Ordered roots shared by Rename inventory and the Caption bridge."""


def inventory_roots(config):
    roots = [{
        "kind": str(item.get("kind") or ""),
        "name": str(item.get("name") or item.get("path") or ""),
        "path": "/" + str(item.get("path") or "").strip("/"),
        "source_kind": "category",
    } for item in config.get("category_folder") or [] if (
        isinstance(item, dict) and str(item.get("path") or "").strip("/")
    )]
    extra = "/" + str(config.get("unorganized_path") or "").strip("/")
    if extra != "/":
        roots.append({"kind": "unorganized", "name": "未整理", "path": extra, "source_kind": "unorganized"})
    return roots
