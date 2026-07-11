from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.utils.ai import infer_download_plan_with_ai, infer_search_hypotheses_with_ai
from app.utils.search_plan import TemporarySpecialAllocator, finalize_download_plan


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
    hypotheses: dict, providers: dict[str, Callable]
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


async def build_confirmable_plan(
    raw_query: str,
    plan_id: str,
    providers: dict[str, Callable],
    occupied_loader: Callable[[dict], set[int]],
    allocator: TemporarySpecialAllocator,
) -> dict:
    hypotheses = await asyncio.to_thread(infer_search_hypotheses_with_ai, raw_query)
    if not isinstance(hypotheses, dict):
        _log_info(f"ai_stage=hypothesis status=unavailable plan_id={plan_id}")
        raise SearchPlanningError("ai_hypothesis_unavailable")
    _log_info(
        "ai_stage=hypothesis status=ok "
        f"plan_id={plan_id} hypotheses={len(hypotheses.get('hypotheses') or [])}"
    )

    sources = await collect_evidence(hypotheses, providers)
    context = {
        "raw_query": raw_query,
        "plan_id": plan_id,
        "hypotheses": hypotheses,
        "sources": sources,
    }
    draft = await asyncio.to_thread(infer_download_plan_with_ai, context)
    if not isinstance(draft, dict):
        _log_info(f"ai_stage=download_plan status=unavailable plan_id={plan_id}")
        raise SearchPlanningError("ai_download_plan_unavailable")
    _log_info(f"ai_stage=download_plan status=ok plan_id={plan_id}")

    draft["plan_id"] = plan_id
    occupied = set(occupied_loader(draft) or set())
    try:
        plan = finalize_download_plan(draft, allocator, occupied)
    except ValueError as exc:
        _log_info(f"search_plan status=invalid plan_id={plan_id}")
        raise SearchPlanningError("invalid_download_plan") from exc
    relation = plan.get("relation") or {}
    query = next(
        (
            str(item).strip()
            for item in (plan.get("prowlarr_queries") or [])
            if str(item).strip()
        ),
        "",
    )
    _log_info(
        "search_plan status=ready "
        f"plan_id={plan_id} relation_source={relation.get('source') or 'ai'} "
        f"query={query}"
    )
    return plan
