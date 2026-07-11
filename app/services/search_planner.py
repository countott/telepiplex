from __future__ import annotations

import asyncio
from collections.abc import Callable

from app.utils.ai import infer_download_plan_with_ai, infer_search_hypotheses_with_ai
from app.utils.search_plan import TemporarySpecialAllocator, finalize_download_plan


class SearchPlanningError(RuntimeError):
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
        raise SearchPlanningError("ai_hypothesis_unavailable")

    sources = await collect_evidence(hypotheses, providers)
    context = {
        "raw_query": raw_query,
        "plan_id": plan_id,
        "hypotheses": hypotheses,
        "sources": sources,
    }
    draft = await asyncio.to_thread(infer_download_plan_with_ai, context)
    if not isinstance(draft, dict):
        raise SearchPlanningError("ai_download_plan_unavailable")

    draft["plan_id"] = plan_id
    occupied = set(occupied_loader(draft) or set())
    try:
        return finalize_download_plan(draft, allocator, occupied)
    except ValueError as exc:
        raise SearchPlanningError("invalid_download_plan") from exc
