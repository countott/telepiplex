"""Stable identities for Prowlarr releases across incremental rank updates."""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlsplit


def _magnet_identity(value: str) -> str:
    text = str(value or "").strip()
    if not text.lower().startswith("magnet:?"):
        return ""
    values = parse_qs(urlsplit(text).query).get("xt") or []
    for value in values:
        normalized = str(value or "").strip().lower()
        if normalized.startswith("urn:btih:"):
            return normalized
    return text.lower()


def stable_release_id(item: dict) -> str:
    release = item if isinstance(item, dict) else {}
    magnet = _magnet_identity(
        release.get("magnet_url")
        or release.get("download_url")
        or ""
    )
    if magnet:
        identity = f"magnet:{magnet}"
    else:
        download_url = str(release.get("download_url") or "").strip()
        info_url = str(release.get("info_url") or "").strip()
        if download_url or info_url:
            identity = f"url:{download_url}|{info_url}"
        else:
            identity = "|".join((
                "fields",
                str(release.get("indexer") or "").strip().casefold(),
                str(release.get("title") or "").strip().casefold(),
                str(release.get("size") or 0),
                str(release.get("publish_date") or "").strip(),
            ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def deduplicate_releases(items) -> list[dict]:
    releases = []
    by_id = {}
    for raw_item in items or []:
        if not isinstance(raw_item, dict):
            continue
        release_id = stable_release_id(raw_item)
        existing = by_id.get(release_id)
        if existing is not None:
            values = list(existing.get("_explicit_seeders") or [])
            if not values:
                try:
                    values.append(int(existing.get("seeders")))
                except (TypeError, ValueError):
                    pass
            try:
                values.append(int(raw_item.get("seeders")))
            except (TypeError, ValueError):
                pass
            if values:
                existing["_explicit_seeders"] = values
                existing["seeders"] = max(values)
            continue
        item = dict(raw_item)
        by_id[release_id] = item
        releases.append(item)
    return releases
