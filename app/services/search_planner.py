from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.utils.ai import (
    infer_media_metadata_draft_with_ai,
    infer_search_hypotheses_with_ai,
)
from app.utils.search_plan import (
    TEMPORARY_MAPPING_KIND,
    TemporarySpecialAllocator,
    finalize_search_plan,
)


class SearchPlanningError(RuntimeError):
    pass


def _log_info(message: str):
    try:
        import init

        logger = getattr(init, "logger", None)
        if logger:
            logger.info(message)
    except Exception:
        pass


def _provider_failure(name: str, exc: Exception) -> dict:
    return {
        "source": name,
        "status": "server_down",
        "facts": [],
        "source_urls": [],
        "error": str(exc),
    }


async def collect_evidence(
    hypotheses: dict,
    providers: dict[str, Callable],
) -> list[dict]:
    names = list(providers)
    tasks = [asyncio.to_thread(providers[name], hypotheses) for name in names]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    evidence = []
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            evidence.append(_provider_failure(name, result))
        elif isinstance(result, dict):
            evidence.append(result)
        else:
            evidence.append(
                _provider_failure(name, RuntimeError("invalid provider response"))
            )
    for item in evidence:
        _log_info(
            "search_evidence "
            f"source={item.get('source')} status={item.get('status')} "
            f"facts={len(item.get('facts') or [])}"
        )
    return evidence


def _verified_tvdb_episode_keys(sources: list[dict]) -> list[str]:
    verified = set()
    for source in sources:
        if not (
            isinstance(source, dict)
            and source.get("source") == "tvdb"
            and source.get("status") == "ok"
        ):
            continue
        for fact in source.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            episodes_by_series = fact.get("episodes_by_series") or {}
            if not isinstance(episodes_by_series, dict):
                continue
            for series_id, episodes in episodes_by_series.items():
                for episode in episodes or []:
                    if not isinstance(episode, dict):
                        continue
                    episode_id = str(
                        episode.get("tvdb_episode_id") or episode.get("id") or ""
                    ).strip()
                    if episode_id:
                        verified.add(f"{series_id}:{episode_id}")
    return sorted(verified)


async def build_confirmable_search_plan(
    raw_query: str,
    plan_id: str,
    providers: dict[str, Callable],
    occupied_loader: Callable[[dict], set[int]],
    allocator: TemporarySpecialAllocator,
) -> dict:
    hypotheses = await asyncio.to_thread(infer_search_hypotheses_with_ai, raw_query)
    if not isinstance(hypotheses, dict):
        _log_info(f"ai_stage=hypothesis status=unavailable metadata_id={plan_id}")
        raise SearchPlanningError("ai_hypothesis_unavailable")
    _log_info(f"ai_stage=hypothesis status=ok metadata_id={plan_id}")
    sources = await collect_evidence(hypotheses, providers)
    context = {
        "raw_query": raw_query,
        "plan_id": plan_id,
        "hypotheses": hypotheses,
        "sources": sources,
    }
    draft = await asyncio.to_thread(infer_media_metadata_draft_with_ai, context)
    if not isinstance(draft, dict):
        _log_info(f"ai_stage=media_metadata status=unavailable metadata_id={plan_id}")
        raise SearchPlanningError("ai_media_metadata_unavailable")
    _log_info(f"ai_stage=media_metadata status=ok metadata_id={plan_id}")
    draft["plan_id"] = plan_id
    contract = (
        draft.get("media_metadata")
        if isinstance(draft.get("media_metadata"), dict)
        else {}
    )
    evidence = contract.get("evidence")
    if not isinstance(evidence, dict):
        raise SearchPlanningError("invalid_media_metadata")
    evidence["provider_statuses"] = {
        str(item.get("source") or ""): str(item.get("status") or "invalid")
        for item in sources
        if isinstance(item, dict) and str(item.get("source") or "")
    }
    evidence["verified_tvdb_episode_keys"] = _verified_tvdb_episode_keys(sources)
    try:
        occupied = (
            set(occupied_loader(contract) or set())
            if (contract.get("placement") or {}).get("mapping_kind")
            == TEMPORARY_MAPPING_KIND
            else set()
        )
    except Exception as exc:
        raise SearchPlanningError("temporary_occupancy_unavailable") from exc
    try:
        plan = finalize_search_plan(draft, allocator, occupied)
    except ValueError as exc:
        raise SearchPlanningError("invalid_media_metadata") from exc
    placement = plan["media_metadata"]["placement"]
    _log_info(
        "search_plan status=ready "
        f"metadata_id={plan_id} mapping_kind={placement.get('mapping_kind')} "
        f"query={(plan.get('prowlarr_queries') or [''])[0]}"
    )
    return plan
