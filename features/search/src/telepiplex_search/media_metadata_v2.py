"""Project Search-private provider evidence to the minimal public v2 contract."""

from __future__ import annotations

from telepiplex_plugin_sdk.media_metadata import sanitize_contract_name
from telepiplex_plugin_sdk.media_metadata_v2 import (
    PROVIDER_REF_KEYS,
    build_media_metadata_v2_id,
    validate_media_metadata_v2_detailed,
)


_VERIFIED_LINK_STATES = frozenset({
    "fact_verified",
    "verified",
    "provider_verified",
    "wikipedia_explicit_link",
    "exact",
})


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _media_type(contract: dict) -> str:
    identity = contract.get("identity") or {}
    placement = contract.get("placement") or {}
    retrieval = contract.get("retrieval") or {}
    value = _text(
        retrieval.get("media_type")
        or placement.get("library_type")
        or identity.get("media_type")
        or identity.get("content_kind")
    ).casefold()
    if value in {"series", "main_episode", "episode"}:
        return "series"
    if value == "movie" or value.endswith("_movie"):
        return "movie"
    return value


def _provider_refs(candidate: dict, media_type: str) -> tuple[dict, dict]:
    refs: dict[str, str] = {}
    anchor_refs: dict[str, str] = {}
    anchor_fact_id = _text(candidate.get("anchor_fact_id"))
    for link in candidate.get("source_links") or ():
        if not isinstance(link, dict):
            continue
        verification = _text(link.get("verification")).casefold()
        if verification not in _VERIFIED_LINK_STATES:
            continue
        provider = _text(link.get("provider")).casefold()
        external_ids = link.get("external_ids") or {}
        if not isinstance(external_ids, dict):
            continue
        mapped: dict[str, str] = {}
        for key in PROVIDER_REF_KEYS:
            if _text(external_ids.get(key)):
                mapped[key] = _text(external_ids[key])
        if _text(external_ids.get("wikidata")):
            mapped["wikidata"] = _text(external_ids["wikidata"])
        if provider == "wikidata":
            raw = external_ids.get("wikidata") or link.get("entity_id")
            if _text(raw):
                mapped["wikidata"] = _text(raw)
        elif provider == "tmdb":
            raw = external_ids.get("tmdb") or link.get("entity_id")
            if _text(raw):
                mapped[f"tmdb_{'movie' if media_type == 'movie' else 'tv'}"] = _text(raw)
        elif provider == "tvdb":
            raw = external_ids.get("tvdb") or link.get("entity_id")
            if _text(raw):
                mapped[f"tvdb_{'movie' if media_type == 'movie' else 'series'}"] = _text(raw)
        elif provider == "douban":
            raw = external_ids.get("douban") or link.get("entity_id")
            if _text(raw):
                mapped["douban_subject"] = _text(raw)
        elif provider == "anilist":
            raw = external_ids.get("anilist") or link.get("entity_id")
            if _text(raw):
                mapped["anilist"] = _text(raw)
        elif provider == "wikipedia":
            language = _text(link.get("language")).casefold()
            page_id = link.get("page_id")
            if _text(page_id) and language.startswith(("zh", "en")):
                mapped[f"{language[:2]}wiki_page_id"] = _text(page_id)
        refs.update(mapped)
        if anchor_fact_id and _text(link.get("fact_id")) == anchor_fact_id:
            anchor_refs.update(mapped)
    contract = candidate.get("media_metadata") or {}
    identity = contract.get("identity") or {}
    legacy_refs = identity.get("external_ids") or {}
    if contract.get("confirmed") is True and isinstance(legacy_refs, dict):
        for key in PROVIDER_REF_KEYS:
            if _text(legacy_refs.get(key)):
                refs.setdefault(key, _text(legacy_refs[key]))
        aliases = {
            "wikidata": "wikidata",
            "douban": "douban_subject",
            "douban_subject": "douban_subject",
            "anilist": "anilist",
        }
        for source_key, target_key in aliases.items():
            if _text(legacy_refs.get(source_key)):
                refs.setdefault(target_key, _text(legacy_refs[source_key]))
        for source_key, target_key in (
            ("tmdb", f"tmdb_{'movie' if media_type == 'movie' else 'tv'}"),
            ("tvdb", f"tvdb_{'movie' if media_type == 'movie' else 'series'}"),
        ):
            if _text(legacy_refs.get(source_key)):
                refs.setdefault(target_key, _text(legacy_refs[source_key]))
    return refs, anchor_refs


def _primary_ref(refs: dict, anchor_refs: dict) -> dict:
    preferred = (
        "wikidata",
        "tvdb_series",
        "tvdb_movie",
        "tmdb_tv",
        "tmdb_movie",
        "douban_subject",
        "zhwiki_page_id",
        "enwiki_page_id",
        "anilist",
    )
    pool = anchor_refs or refs
    for provider in preferred:
        if provider in pool:
            return {"provider": provider, "id": pool[provider]}
    provider = next(iter(pool), "")
    if not provider:
        raise ValueError("verified_provider_ref_required")
    return {"provider": provider, "id": pool[provider]}


def project_confirmed_media_metadata_v2(
    candidate: dict,
    *,
    requested_scope: dict,
) -> dict:
    """Freeze one confirmed rich candidate without leaking provider facts."""

    if not isinstance(candidate, dict):
        raise ValueError("candidate_required")
    contract = candidate.get("media_metadata") or {}
    identity = contract.get("identity") or {}
    placement = contract.get("placement") or {}
    media_type = _media_type(contract)
    refs, anchor_refs = _provider_refs(candidate, media_type)
    primary_ref = _primary_ref(refs, anchor_refs)
    title_zh = sanitize_contract_name(identity.get("chinese_title"))
    title_original = sanitize_contract_name(
        identity.get("original_title")
        or identity.get("official_original_title")
        or identity.get("english_title")
    )
    try:
        year = int(identity.get("year")) if identity.get("year") else None
    except (TypeError, ValueError):
        year = None
    scope = {
        "kind": _text((requested_scope or {}).get("kind")),
        "season_number": (requested_scope or {}).get("season_number"),
        "episode_number": (requested_scope or {}).get("episode_number"),
    }
    result = {
        "schema_version": 2,
        "confirmed": True,
        "identity": {
            "primary_ref": primary_ref,
            "provider_refs": refs,
            "media_type": media_type,
            "title_zh": title_zh,
            "title_original": title_original,
            "year": year,
        },
        "scope": scope,
        "placement": {
            "category_kind": _text(placement.get("category_kind")),
        },
    }
    result["metadata_id"] = build_media_metadata_v2_id(result)
    validated, issue = validate_media_metadata_v2_detailed(result)
    if validated is None:
        issue = issue or {}
        raise ValueError(
            f"media_metadata_v2_invalid:{issue.get('reason_code') or 'unknown'}"
        )
    return validated
