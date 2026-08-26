"""Strict minimal media identity contract for new telepiplex operations."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from .media_metadata import (
    CATEGORY_LIBRARY_TYPES,
    MEDIA_METADATA_KEY,
)


SCHEMA_VERSION = 2
PROVIDER_REF_KEYS = frozenset({
    "wikidata",
    "zhwiki_page_id",
    "enwiki_page_id",
    "douban_subject",
    "tmdb_movie",
    "tmdb_tv",
    "tvdb_movie",
    "tvdb_series",
    "anilist",
})
TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "metadata_id",
    "confirmed",
    "identity",
    "scope",
    "placement",
})
IDENTITY_KEYS = frozenset({
    "primary_ref",
    "provider_refs",
    "media_type",
    "title_zh",
    "title_en",
    "title_original",
    "year",
})
PRIMARY_REF_KEYS = frozenset({"provider", "id"})
SCOPE_KEYS = frozenset({"kind", "season_number", "episode_number"})
PLACEMENT_KEYS = frozenset({"category_kind"})


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _issue(path: str, reason_code: str, detail: str) -> dict:
    return {
        "path": path,
        "reason_code": reason_code,
        "detail": detail,
    }


def _exact_keys(value: object, expected: frozenset[str]) -> bool:
    return isinstance(value, dict) and set(value) == expected


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _id_payload(value: dict) -> dict:
    identity = value.get("identity") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "primary_ref": deepcopy(identity.get("primary_ref")),
        "media_type": identity.get("media_type"),
        "scope": deepcopy(value.get("scope")),
    }


def build_media_metadata_v2_id(contract_without_id: dict) -> str:
    """Build the stable identity-and-scope id, excluding display metadata."""

    if not isinstance(contract_without_id, dict):
        raise ValueError("media_metadata v2 must be an object")
    canonical = json.dumps(
        _id_payload(contract_without_id),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "media-v2:" + hashlib.sha256(canonical).hexdigest()


def _diagnose(
    value: object,
    *,
    require_confirmed: bool,
) -> dict | None:
    if not isinstance(value, dict):
        return _issue("$", "object_required", "media_metadata v2 must be an object")
    if set(value) != TOP_LEVEL_KEYS:
        return _issue("$", "keys_invalid", "media_metadata v2 top-level keys must be exact")
    if value.get("schema_version") != SCHEMA_VERSION:
        return _issue("$.schema_version", "schema_version_unsupported", "schema_version must be 2")
    if require_confirmed and value.get("confirmed") is not True:
        return _issue("$.confirmed", "confirmation_required", "confirmed must be true")

    identity = value.get("identity")
    if not _exact_keys(identity, IDENTITY_KEYS):
        return _issue("$.identity", "keys_invalid", "identity keys must be exact")
    primary_ref = identity.get("primary_ref")
    if not _exact_keys(primary_ref, PRIMARY_REF_KEYS):
        return _issue("$.identity.primary_ref", "keys_invalid", "primary_ref keys must be exact")
    provider = _text(primary_ref.get("provider"))
    provider_id = _text(primary_ref.get("id"))
    if provider not in PROVIDER_REF_KEYS or not provider_id:
        return _issue("$.identity.primary_ref", "primary_ref_invalid", "primary provider and id must be supported and nonblank")

    provider_refs = identity.get("provider_refs")
    if not isinstance(provider_refs, dict) or not provider_refs:
        return _issue("$.identity.provider_refs", "provider_refs_invalid", "provider_refs must be a nonempty object")
    for key, raw_id in provider_refs.items():
        if key not in PROVIDER_REF_KEYS or not isinstance(raw_id, str) or not _text(raw_id):
            return _issue("$.identity.provider_refs", "provider_ref_invalid", "provider refs must use supported keys and nonblank string ids")
    if provider_refs.get(provider) != provider_id:
        return _issue("$.identity.primary_ref", "provider_identity_conflict", "primary_ref must match provider_refs")

    media_type = identity.get("media_type")
    if media_type not in {"movie", "series"}:
        return _issue("$.identity.media_type", "media_type_invalid", "media_type must be movie or series")
    title_zh = identity.get("title_zh")
    title_en = identity.get("title_en")
    title_original = identity.get("title_original")
    if (
        not isinstance(title_zh, str)
        or not isinstance(title_en, str)
        or not isinstance(title_original, str)
    ):
        return _issue("$.identity", "title_invalid", "titles must be strings")
    if not _text(title_en):
        return _issue(
            "$.identity.title_en",
            "english_title_required",
            "a verified English title is required",
        )
    if not (_text(title_zh) or _text(title_original)):
        return _issue("$.identity", "title_required", "at least one title is required")
    year = identity.get("year")
    if year is not None and not _positive_integer(year):
        return _issue("$.identity.year", "year_invalid", "year must be a positive integer or null")

    scope = value.get("scope")
    if not _exact_keys(scope, SCOPE_KEYS):
        return _issue("$.scope", "keys_invalid", "scope keys must be exact")
    kind = scope.get("kind")
    season = scope.get("season_number")
    episode = scope.get("episode_number")
    valid_scope = (
        kind == "movie" and media_type == "movie"
        and season is None and episode is None
    ) or (
        kind == "whole_series" and media_type == "series"
        and season is None and episode is None
    ) or (
        kind == "season" and media_type == "series"
        and _positive_integer(season) and episode is None
    ) or (
        kind == "episode" and media_type == "series"
        and _positive_integer(season) and _positive_integer(episode)
    )
    if not valid_scope:
        return _issue("$.scope", "scope_invalid", "scope coordinates do not match media_type and kind")

    placement = value.get("placement")
    if not _exact_keys(placement, PLACEMENT_KEYS):
        return _issue("$.placement", "keys_invalid", "placement keys must be exact")
    category_kind = placement.get("category_kind")
    if CATEGORY_LIBRARY_TYPES.get(category_kind) != media_type:
        return _issue("$.placement.category_kind", "category_kind_invalid", "category kind must exist and match media_type")

    try:
        expected_id = build_media_metadata_v2_id(value)
    except (TypeError, ValueError):
        return _issue("$", "json_invalid", "contract must be finite JSON")
    if value.get("metadata_id") != expected_id:
        return _issue("$.metadata_id", "metadata_id_mismatch", "metadata_id does not match the frozen identity and scope")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return _issue("$", "json_invalid", "contract must be finite JSON")
    return None


def validate_media_metadata_v2_detailed(
    value: object,
    *,
    require_confirmed: bool = True,
) -> tuple[dict | None, dict | None]:
    issue = _diagnose(value, require_confirmed=require_confirmed)
    if issue is not None:
        return None, issue
    return deepcopy(value), None


def validate_media_metadata_v2(
    value: object,
    *,
    require_confirmed: bool = True,
) -> dict | None:
    validated, _issue_value = validate_media_metadata_v2_detailed(
        value,
        require_confirmed=require_confirmed,
    )
    return validated


def attach_media_metadata_v2(metadata: dict | None, value: dict) -> dict:
    validated = validate_media_metadata_v2(value)
    if validated is None:
        raise ValueError("invalid confirmed media_metadata v2")
    result = deepcopy(metadata) if isinstance(metadata, dict) else {}
    result[MEDIA_METADATA_KEY] = validated
    return result


def extract_confirmed_media_metadata_v2(metadata: dict | None) -> dict | None:
    if not isinstance(metadata, dict):
        return None
    return validate_media_metadata_v2(metadata.get(MEDIA_METADATA_KEY))
