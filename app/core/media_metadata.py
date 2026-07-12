from __future__ import annotations

import json
import re
from copy import deepcopy


MEDIA_METADATA_KEY = "media_metadata"
LEGACY_METADATA_KEY = "_".join(("download", "plan"))
SCHEMA_VERSION = 1
CONTENT_KINDS = {
    "movie",
    "series",
    "main_episode",
    "ova",
    "narrative_bonus",
    "non_narrative_extra",
    "special",
    "prequel_movie",
    "sequel_movie",
    "extension_movie",
    "spin_off",
}
CATEGORY_LIBRARY_TYPES = {
    "live_action_series": "series",
    "live_action_movie": "movie",
    "animated_movie": "movie",
    "animated_series": "series",
}
MAPPING_KINDS = {
    "tvdb_official",
    "ai_inferred_tvdb",
    "temporary_related_special",
    "standalone",
}
SERIES_EPISODE_MAPPINGS = {
    "tvdb_official",
    "ai_inferred_tvdb",
    "temporary_related_special",
}
INVALID_NAME_CHARS = re.compile(r'[\\/*?"<>|]+')


def resolve_category_route(config: dict | None, category_kind: str) -> dict | None:
    if category_kind not in CATEGORY_LIBRARY_TYPES:
        return None
    for item in (config or {}).get("category_folder") or []:
        if not isinstance(item, dict) or item.get("kind") != category_kind:
            continue
        path = "/" + str(item.get("path") or "").strip("/")
        if path == "/":
            return None
        return {
            "kind": category_kind,
            "name": str(item.get("name") or category_kind),
            "path": path,
            "plex_library_id": str(item.get("plex_library_id") or ""),
        }
    return None


def require_complete_category_routes(config: dict | None) -> None:
    routes = (config or {}).get("category_folder")
    if not isinstance(routes, list):
        raise ValueError("category_folder must define four routes with kind")
    seen = set()
    for item in routes:
        if not isinstance(item, dict):
            raise ValueError("category_folder entries must be objects with kind")
        kind = item.get("kind")
        if kind not in CATEGORY_LIBRARY_TYPES or kind in seen:
            raise ValueError("category_folder has missing, duplicate, or invalid kind")
        if not str(item.get("path") or "").strip("/"):
            raise ValueError(f"category_folder.{kind}.path is required")
        if "plex_library_id" not in item:
            raise ValueError(f"category_folder.{kind}.plex_library_id key is required")
        seen.add(kind)
    if seen != set(CATEGORY_LIBRARY_TYPES):
        raise ValueError("category_folder must contain exactly four kind values")


def sanitize_contract_name(value) -> str:
    value = str(value or "").replace("：", ": ").replace("（", "(").replace("）", ")")
    value = re.sub(r"\s*(?:——|—|–|－)\s*", " - ", value)
    value = re.sub(r"\s+:", ":", value)
    value = re.sub(r"(?<!\d):\s*", ": ", value)
    value = INVALID_NAME_CHARS.sub("", value)
    return " ".join(value.split()).strip().strip(".")


def series_titles(value: dict) -> tuple[str, str]:
    relation_target = ((value.get("relation") or {}).get("target_series") or {})
    identity = value.get("identity") or {}
    source = relation_target if (
        relation_target.get("chinese_title") or relation_target.get("english_title")
    ) else identity
    return (
        sanitize_contract_name(source.get("chinese_title")),
        sanitize_contract_name(source.get("english_title")),
    )


def series_folder_name(value: dict) -> str:
    chinese_title, english_title = series_titles(value)
    if chinese_title and english_title and chinese_title != english_title:
        return f"{chinese_title} ({english_title})"
    return chinese_title or english_title


def series_season_directory_name(value: dict, season_number: int) -> str:
    chinese_title, english_title = series_titles(value)
    series_name = english_title or chinese_title
    return f"{series_name} Season {int(season_number):02d}" if series_name else ""


def series_scope_key(value: dict) -> str:
    target = ((value.get("relation") or {}).get("target_series") or {})
    target_ids = target.get("external_ids") or {}
    if target_ids.get("tvdb"):
        return f"tvdb:{_text(target_ids['tvdb'])}"
    chinese_title, english_title = series_titles(value)
    year = _text(target.get("year") or (value.get("identity") or {}).get("year"))
    return f"title:{(english_title or chinese_title).casefold()}:{year}"


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_items(items) -> bool:
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        role = _text(item.get("content_role"))
        if role and role not in CONTENT_KINDS:
            return False
        season = item.get("season_number")
        episode = item.get("episode_number")
        if season is None and episode is None:
            continue
        season = _integer(season)
        episode = _integer(episode)
        if season is None or season < 0 or episode is None or episode < 1:
            return False
    return True


def validate_media_metadata(value: object, require_confirmed: bool = False):
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return None
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None
    if not _text(value.get("metadata_id")):
        return None
    if require_confirmed and value.get("confirmed") is not True:
        return None

    identity = value.get("identity")
    relation = value.get("relation")
    placement = value.get("placement")
    if not isinstance(identity, dict) or not isinstance(relation, dict) or not isinstance(placement, dict):
        return None
    if not (_text(identity.get("chinese_title")) or _text(identity.get("english_title"))):
        return None
    if _text(identity.get("content_kind")) not in CONTENT_KINDS:
        return None
    if "external_ids" in identity and not isinstance(identity.get("external_ids"), dict):
        return None

    target = relation.get("target_series")
    if isinstance(target, dict) and (
        "external_ids" in target and not isinstance(target.get("external_ids"), dict)
    ):
        return None

    category_kind = placement.get("category_kind")
    library_type = placement.get("library_type")
    if (
        category_kind not in CATEGORY_LIBRARY_TYPES
        or CATEGORY_LIBRARY_TYPES[category_kind] != library_type
    ):
        return None
    mapping_kind = placement.get("mapping_kind")
    if mapping_kind not in MAPPING_KINDS:
        return None
    evidence = value.get("evidence")
    warnings = value.get("warnings")
    items = value.get("items")
    if not isinstance(evidence, dict):
        return None
    if not isinstance(warnings, list):
        return None
    if not isinstance(items, list) or not _valid_items(items):
        return None

    if mapping_kind in SERIES_EPISODE_MAPPINGS:
        season = _integer(placement.get("season_number"))
        episode = _integer(placement.get("episode_number"))
        if library_type != "series" or season != 0 or episode is None or episode < 1:
            return None
        if not isinstance(target, dict) or not (
            _text(target.get("chinese_title")) or _text(target.get("english_title"))
        ):
            return None
        if any(
            (
                _integer(item.get("season_number")),
                _integer(item.get("episode_number")),
            ) != (season, episode)
            for item in items
        ):
            return None

    if mapping_kind == "tvdb_official":
        target_ids = target.get("external_ids") or {}
        if not _text(target_ids.get("tvdb")) or not _text(placement.get("tvdb_episode_id")):
            return None

    if mapping_kind == "ai_inferred_tvdb" and not any(
        isinstance(warning, str) and warning.strip()
        for warning in warnings
    ):
        return None

    if mapping_kind == "temporary_related_special":
        source_entry = value.get("source_entry")
        episode = _integer(placement.get("episode_number"))
        if placement.get("season_number") != 0 or episode is None or episode < 100:
            return None
        if not isinstance(source_entry, dict) or not _text(source_entry.get("title")):
            return None
        if not (_text(source_entry.get("url")) or _text(source_entry.get("external_id"))):
            return None

    if mapping_kind == "standalone":
        if placement.get("season_number") is not None or placement.get("episode_number") is not None:
            return None
        if isinstance(target, dict) and (
            _text(target.get("chinese_title")) or _text(target.get("english_title"))
        ):
            return None
        if library_type == "series":
            if not items or any(
                item.get("season_number") is None or item.get("episode_number") is None
                for item in items
            ):
                return None

    return deepcopy(value)


def attach_media_metadata(metadata: dict | None, value: dict) -> dict:
    validated = validate_media_metadata(value, require_confirmed=True)
    if validated is None:
        raise ValueError("invalid confirmed media_metadata")
    result = deepcopy(metadata) if isinstance(metadata, dict) else {}
    if LEGACY_METADATA_KEY in result:
        raise ValueError("legacy metadata key is not allowed")
    result[MEDIA_METADATA_KEY] = validated
    return result


def extract_confirmed_media_metadata(metadata: dict | None):
    if not isinstance(metadata, dict):
        return None
    return validate_media_metadata(metadata.get(MEDIA_METADATA_KEY), require_confirmed=True)


def enrich_media_metadata_identity(
    metadata: dict | None,
    *,
    chinese_title: str,
    source: str,
    evidence: dict | None = None,
) -> dict:
    result = deepcopy(metadata) if isinstance(metadata, dict) else {}
    contract_present = MEDIA_METADATA_KEY in result
    contract = extract_confirmed_media_metadata(result)
    if contract is None:
        if contract_present:
            raise ValueError("invalid confirmed media_metadata")
        return result

    title = _text(chinese_title)
    if not title or _text((contract.get("identity") or {}).get("chinese_title")):
        return result

    contract["identity"]["chinese_title"] = title
    contract_evidence = deepcopy(contract.get("evidence") or {})
    backfills = list(contract_evidence.get("identity_backfills") or [])
    entry = {
        "field": "chinese_title",
        "source": _text(source) or "unknown",
    }
    for key, value in (evidence or {}).items():
        if _text(value):
            entry[str(key)] = deepcopy(value)
    backfills.append(entry)
    contract_evidence["identity_backfills"] = backfills
    contract["evidence"] = contract_evidence

    validated = validate_media_metadata(contract, require_confirmed=True)
    if validated is None:
        raise ValueError("identity enrichment produced invalid media_metadata")
    result[MEDIA_METADATA_KEY] = validated
    return result


def locked_episode(value: dict):
    placement = value.get("placement") if isinstance(value, dict) else None
    if not isinstance(placement, dict) or placement.get("library_type") != "series":
        return None
    season = _integer(placement.get("season_number"))
    episode = _integer(placement.get("episode_number"))
    if season is None or season < 0 or episode is None or episode < 1:
        return None
    return season, episode


def merge_resolved_items(value: dict, resolved_items: list[dict]) -> dict:
    contract = validate_media_metadata(value, require_confirmed=True)
    if contract is None:
        raise ValueError("invalid confirmed media_metadata")
    allowed = set()
    mapping_kind = contract["placement"]["mapping_kind"]
    if mapping_kind in SERIES_EPISODE_MAPPINGS:
        episode = locked_episode(contract)
        if episode:
            allowed.add(episode)
    else:
        allowed = {
            (int(item["season_number"]), int(item["episode_number"]))
            for item in contract.get("items") or []
            if item.get("season_number") is not None and item.get("episode_number") is not None
        }

    items = deepcopy(contract.get("items") or [])
    for resolved in resolved_items or []:
        season = _integer(resolved.get("season_number"))
        episode = _integer(resolved.get("episode_number"))
        if (season, episode) not in allowed:
            raise ValueError("resolved item changes locked target")
        match = next((
            item for item in items
            if _integer(item.get("season_number")) == season
            and _integer(item.get("episode_number")) == episode
        ), None)
        if match is None:
            match = {
                "item_id": _text(resolved.get("item_id")) or f"S{season:02d}E{episode:03d}",
                "content_role": _text(resolved.get("content_role")) or contract["identity"]["content_kind"],
                "season_number": season,
                "episode_number": episode,
            }
            items.append(match)
        for key in ("content_role", "source_relative_path", "final_path"):
            if key in resolved:
                match[key] = deepcopy(resolved[key])
    contract["items"] = items
    validated = validate_media_metadata(contract, require_confirmed=True)
    if validated is None:
        raise ValueError("resolved items make media_metadata invalid")
    return validated
