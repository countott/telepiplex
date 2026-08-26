from __future__ import annotations

from copy import deepcopy


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _verified_scope_link(
    candidate: dict,
    *,
    scope: str,
    season_number: int | None,
    episode_number: int | None,
) -> bool:
    for link in candidate.get("source_links") or ():
        if not isinstance(link, dict):
            continue
        verification = str(link.get("verification") or "").casefold()
        if verification in {"", "unresolved_scope_link"}:
            continue
        role = str(link.get("role") or "").casefold()
        link_season = _positive_int(link.get("season_number"))
        link_episode = _positive_int(link.get("episode_number"))
        if scope == "season" and role == "season":
            if season_number is None or link_season == season_number:
                return True
        if scope == "episode" and role == "episode":
            if (
                (season_number is None or link_season == season_number)
                and (episode_number is None or link_episode == episode_number)
            ):
                return True
    return False


def needs_authoritative_scope_enrichment(candidate: dict) -> bool:
    """Return whether a frozen candidate still lacks required series scope facts."""
    if not isinstance(candidate, dict):
        return False
    contract = candidate.get("media_metadata") or candidate
    if not isinstance(contract, dict):
        return False
    retrieval = contract.get("retrieval") or {}
    identity = contract.get("identity") or {}
    placement = contract.get("placement") or {}
    media_type = str(
        retrieval.get("media_type")
        or identity.get("content_kind")
        or placement.get("library_type")
        or ""
    ).casefold()
    if media_type != "series":
        return False

    evidence = contract.get("evidence") or {}
    decision = evidence.get("decision") or {}
    scope = str(
        retrieval.get("scope")
        or candidate.get("intended_scope")
        or decision.get("scope")
        or "work"
    ).casefold()
    season_number = _positive_int(
        decision.get("season_number")
        or candidate.get("requested_season_number")
    )
    episode_number = _positive_int(
        decision.get("episode_number")
        or candidate.get("requested_episode_number")
    )

    items = [
        item for item in contract.get("items") or ()
        if isinstance(item, dict)
    ]
    coordinates = {
        (
            _positive_int(item.get("season_number")),
            _positive_int(item.get("episode_number")),
        )
        for item in items
    }
    inventory = evidence.get("series_inventory") or {}
    season_totals = inventory.get("season_totals") or {}
    known_seasons = {
        number
        for value in season_totals
        if (number := _positive_int(value)) is not None
        and _positive_int(season_totals[value]) is not None
    }

    if scope == "episode":
        if season_number is None or episode_number is None:
            return True
        if (season_number, episode_number) in coordinates:
            return False
        return not _verified_scope_link(
            candidate,
            scope=scope,
            season_number=season_number,
            episode_number=episode_number,
        )
    if scope == "season":
        if season_number is None:
            return True
        if season_number in known_seasons or any(
            item_season == season_number
            for item_season, _item_episode in coordinates
        ):
            return False
        # A verified season page establishes identity, not episode inventory.
        # Season-level selection still needs an authoritative source before the
        # UI can decide whether individual episodes or a whole-season option
        # are available.
        return True
    return not bool(items or known_seasons)


def apply_deferred_presentation(contract: dict, enrichment: dict) -> dict:
    """Fill optional presentation blanks without changing business authority."""
    result = deepcopy(contract)
    if not isinstance(result, dict) or not isinstance(enrichment, dict):
        return result
    source = enrichment.get("media_metadata") or enrichment
    if not isinstance(source, dict):
        return result
    target_identity = result.get("identity")
    source_identity = source.get("identity")
    if not isinstance(target_identity, dict) or not isinstance(
        source_identity, dict
    ):
        return result

    if not str(target_identity.get("chinese_title") or "").strip():
        chinese_title = " ".join(
            str(source_identity.get("chinese_title") or "").split()
        )
        if chinese_title:
            target_identity["chinese_title"] = chinese_title
    if not str(target_identity.get("poster_url") or "").strip():
        poster_url = str(source_identity.get("poster_url") or "").strip()
        if poster_url.startswith("https://"):
            target_identity["poster_url"] = poster_url
    return result
