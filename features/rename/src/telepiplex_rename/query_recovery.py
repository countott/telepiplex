"""Recover low-confidence file-tree identities without escaping evidence."""

from __future__ import annotations

from copy import deepcopy
import re
import unicodedata

from .ai import recover_query_with_ai


def _text(value) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _key(value) -> str:
    return "".join(
        character
        for character in unicodedata.normalize(
            "NFKC", _text(value)
        ).casefold()
        if character.isalnum()
    )


def _bounded_context(probe: dict) -> dict:
    evidence = []
    for raw in probe.get("query_evidence") or ():
        if not isinstance(raw, dict) or len(evidence) >= 12:
            continue
        item = {
            "source": _text(raw.get("source"))[:80],
            "candidate": _text(raw.get("candidate"))[:240],
        }
        path = _text(raw.get("relative_path"))
        if len(path) > 512:
            path = "…" + path[-511:]
        if path:
            item["relative_path"] = path
        evidence.append(item)
    paths = []
    for item in evidence:
        path = _text(item.get("relative_path"))
        if len(path) > 512:
            path = "…" + path[-511:]
        if path and path not in paths:
            paths.append(path)
    return {
        "identity_candidates": [
            _text(item)[:240]
            for item in probe.get("identity_candidates") or ()
            if _text(item)
        ][:8],
        "query_evidence": evidence,
        "representative_paths": paths[:8],
        "year_hint": _text(probe.get("year_hint")),
        "content_shape": _text(probe.get("content_shape")),
        "observed_seasons": list(probe.get("observed_seasons") or ())[:100],
        "observed_episodes": [
            dict(item)
            for item in probe.get("observed_episodes") or ()
            if isinstance(item, dict)
        ][:200],
        "video_count": int(probe.get("video_count") or 0),
        "recovery_reasons": [
            _text(item)
            for item in probe.get("recovery_reasons") or ()
            if _text(item)
        ][:8],
    }


def _supported_identity(query: str, context: dict) -> bool:
    query_key = _key(query)
    if not query_key:
        return False
    tokens = re.findall(r"[\w]+", _text(query).casefold())
    if len(tokens) == 1 and tokens[0] in {
        "anime", "episode", "film", "movie", "season", "series", "show",
    }:
        return False
    candidates = list(context.get("identity_candidates") or ())
    candidates.extend(
        _text(item.get("candidate"))
        for item in context.get("query_evidence") or ()
        if isinstance(item, dict)
    )
    for candidate in candidates:
        candidate_key = _key(candidate)
        if (
            candidate_key
            and min(len(query_key), len(candidate_key)) >= 2
            and (
                query_key == candidate_key
                or query_key in candidate_key
                or candidate_key in query_key
            )
        ):
            return True
    return False


def recover_metadata_probe(probe: dict) -> dict:
    """Return a recovered copy, or the original low-confidence probe."""

    result = deepcopy(probe if isinstance(probe, dict) else {})
    if result.get("identity_query") and not result.get("requires_recovery"):
        return result
    if "identity_conflict" in set(result.get("recovery_reasons") or ()):
        result["recovery_status"] = "blocked_identity_conflict"
        return result
    context = _bounded_context(result)
    recovered = recover_query_with_ai(context)
    if not isinstance(recovered, dict):
        result["recovery_status"] = "unavailable"
        return result
    query = _text(recovered.get("identity_query"))
    year = _text(recovered.get("year_hint"))
    supported_year = _text(context.get("year_hint"))
    if (
        recovered.get("status") != "ok"
        or not _supported_identity(query, context)
        or (year and year != supported_year)
    ):
        result["recovery_status"] = "rejected"
        return result
    result.update({
        "identity_query": query,
        "year_hint": year or supported_year,
        "query_confidence": "medium",
        "requires_recovery": False,
        "recovery_source": "ai_evidence_bound",
        "recovery_status": "accepted",
    })
    evidence = list(result.get("query_evidence") or ())[:11]
    evidence.append({
        "source": "ai_recovery",
        "candidate": query,
    })
    result["query_evidence"] = evidence
    candidates = list(result.get("identity_candidates") or ())
    if not any(_key(item) == _key(query) for item in candidates):
        candidates.insert(0, query)
    result["identity_candidates"] = candidates[:8]
    return result
