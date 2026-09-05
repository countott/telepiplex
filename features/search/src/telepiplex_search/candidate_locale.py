"""Hydrate exact locale bindings without running fuzzy candidate search."""

from __future__ import annotations

from copy import deepcopy
import re


class CandidateLocaleError(ValueError):
    def __init__(self, code: str):
        self.code = str(code or "douban_exact_binding_failed")
        super().__init__(self.code)


def commit_candidate_localization(plan: dict, snapshot: dict, localized: dict) -> dict:
    """Commit a pre-confirmation transaction only to its unchanged revision.

    Locale includes validated source bindings, titles and aliases, not just a
    poster patch. Nothing is attached to an already displayed/confirmed plan.
    Comparing the complete snapshot also freezes owner, candidate order, scope,
    source references and retrieval queries while this transaction is running.
    """
    if plan != snapshot:
        return deepcopy(plan)
    original_ids = [
        (item.get("candidate_id"), item.get("candidate_key"))
        for item in snapshot.get("candidates") or ()
    ]
    localized_ids = [
        (item.get("candidate_id"), item.get("candidate_key"))
        for item in localized.get("candidates") or ()
    ]
    if original_ids != localized_ids:
        return deepcopy(plan)
    return localized


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


_HAN = re.compile(r"[\u3400-\u9fff]")


def _localized_chinese_title(fact: dict) -> str:
    title = _text(fact.get("chinese_title"))
    return title if _HAN.search(title) else ""


def _apply_douban_locale(
    candidate: dict,
    fact: dict,
    *,
    match_mode: str,
    expected_subject_id: str = "",
) -> dict:
    result = deepcopy(candidate)
    identity = (
        (result.get("media_metadata") or {}).get("identity")
        if isinstance(result.get("media_metadata"), dict)
        else None
    )
    if not isinstance(identity, dict) or not isinstance(fact, dict):
        raise CandidateLocaleError("douban_binding_failed")
    subject_id = _text(
        fact.get("subject_id")
        or (fact.get("external_ids") or {}).get("douban_subject")
    )
    if not subject_id or (
        expected_subject_id and subject_id != expected_subject_id
    ):
        raise CandidateLocaleError("douban_binding_failed")
    expected_type = _text(identity.get("content_kind")).casefold()
    fact_type = _text(fact.get("media_type")).casefold()
    if (
        expected_type in {"movie", "series"}
        and fact_type in {"movie", "series"}
        and fact_type != expected_type
    ):
        raise CandidateLocaleError("douban_binding_failed")
    chinese_title = _localized_chinese_title(fact)
    if not chinese_title:
        raise CandidateLocaleError("douban_chinese_title_missing")

    previous_title = _text(identity.get("chinese_title"))
    aliases = [
        _text(value) for value in identity.get("aliases") or () if _text(value)
    ]
    if (
        previous_title
        and previous_title != chinese_title
        and previous_title not in aliases
    ):
        aliases.append(previous_title)
    identity["chinese_title"] = chinese_title
    identity["aliases"] = aliases
    identity.setdefault("external_ids", {})["douban_subject"] = subject_id
    identity.setdefault("field_sources", {})["chinese_title"] = {
        "provider": "douban",
        "subject_id": subject_id,
        "match_mode": match_mode,
        "raw_title": _text(fact.get("douban_title_raw")),
    }
    if not _text(identity.get("poster_url")) and _text(fact.get("cover_url")):
        identity["poster_url"] = _text(fact.get("cover_url"))
        result["poster_url"] = _text(fact.get("cover_url"))

    links = [
        dict(value) for value in result.get("source_links") or ()
        if isinstance(value, dict)
    ]
    if not any(
        _text(value.get("provider")).casefold() == "douban"
        and _text((value.get("external_ids") or {}).get("douban_subject"))
        == subject_id
        for value in links
    ):
        role = "movie" if expected_type == "movie" else "series_root"
        links.append({
            "provider": "douban",
            "fact_id": f"douban:{subject_id}",
            "url": _text(fact.get("url"))
            or f"https://movie.douban.com/subject/{subject_id}/",
            "external_ids": {
                **dict(fact.get("external_ids") or {}),
                "douban_subject": subject_id,
            },
            "role": role,
            "season_number": None,
            "episode_number": None,
            "verification": match_mode,
            "proposed_season_number": None,
            "proposed_episode_number": None,
        })
    result["source_links"] = links
    result["localization"] = {
        "provider": "douban",
        "subject_id": subject_id,
        "match_mode": match_mode,
    }
    result["douban_match_mode"] = match_mode
    return result


def localize_candidate_from_verified_douban(
    candidate: dict,
    fact: dict,
    *,
    match_mode: str,
) -> dict:
    """Apply one already uniqueness-verified Douban localization fact."""

    normalized_mode = _text(match_mode).casefold()
    if normalized_mode not in {
        "wikidata_exact",
        "strong_fields",
        "imdb_exact",
    }:
        raise CandidateLocaleError("douban_match_mode_unverified")
    return _apply_douban_locale(
        candidate,
        fact,
        match_mode=normalized_mode,
    )


def localize_candidate_from_exact_douban(
    candidate: dict,
    fact: dict,
) -> dict:
    """Apply a P4529-bound Douban title to a frozen candidate preview."""

    identity = (
        (candidate.get("media_metadata") or {}).get("identity")
        if isinstance(candidate.get("media_metadata"), dict)
        else None
    )
    if not isinstance(identity, dict):
        raise CandidateLocaleError("douban_exact_binding_failed")
    expected_id = _text(
        (identity.get("external_ids") or {}).get("douban_subject")
    )
    if not expected_id:
        raise CandidateLocaleError("douban_exact_binding_failed")
    try:
        return _apply_douban_locale(
            candidate,
            fact,
            match_mode="wikidata_exact",
            expected_subject_id=expected_id,
        )
    except CandidateLocaleError as exc:
        raise CandidateLocaleError("douban_exact_binding_failed") from exc
