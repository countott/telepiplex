"""Search real sources, inspect real subtitle contents, then apply user priorities."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
import json
import logging
import sys
import time

from .archive import extract_subtitles
from .catalog_providers import mapped_episode
from .matching import episode_coordinates, match_candidate, match_document
from .models import MediaQuery, QualityReport, SubtitleCandidate, SubtitleDocument
from .providers import ProviderError, ProviderResult, build_providers, provider_request_scope
from .quality import inspect_subtitle, rank_subtitle, subtitle_priority

logger = logging.getLogger("telepiplex.caption")


@dataclass(frozen=True)
class Selection:
    query: MediaQuery
    candidate: SubtitleCandidate
    document: SubtitleDocument
    quality: QualityReport
    rank: tuple

    def summary(self) -> dict:
        return {
            "provider": self.candidate.provider,
            "candidate_id": self.candidate.candidate_id,
            "source_page": self.candidate.detail_url,
            "source_filename": self.document.filename,
            "language": self.quality.language,
            "format": self.document.format,
            "bilingual": self.quality.bilingual,
            "cue_count": self.quality.cue_count,
            "timing_verified": self.quality.timing_verified,
            "warnings": list(dict.fromkeys((*self.quality.warnings, *match_document(self.query, self.candidate, self.document).warnings))),
            "season": self.query.season,
            "episode": self.query.episode,
        }


class CaptionEngine:
    def __init__(self, config: dict, *, providers=None):
        self.config = config
        self.providers = list(providers) if providers is not None else build_providers(config)
        self.timeout = float(config.get("provider_timeout_seconds", 30))
        self.limit = max(1, min(100, int(config.get("max_candidates", 30))))
        self._downloads: dict[tuple, tuple[float, bytes, str]] = {}
        self._search_cache = OrderedDict()
        self._download_tasks = {}
        self._search_tasks = {}
        self._cooldowns = {}
        self._workers = set()
        self._active_work = {}
        self._network = asyncio.Semaphore(3)
        self._inspection = asyncio.Semaphore(2)
        self._cpu = asyncio.Semaphore(2)
        # Both raw bodies and decoded strings count towards retained selections.
        # A large whole-series request stops with an explicit partial warning.
        self._selection_budget = 64 * 1024 * 1024

    async def _blocking(self, function, *args, cpu=False, skip_if=None, work_key=None):
        """Keep capacity reserved until a timed-out blocking worker really ends.

        Cancelling asyncio.to_thread does not stop its OS thread. Releasing a
        semaphore at the caller's timeout would permit unbounded overlapping
        source calls during repeated scans/cancellations.
        """
        semaphore = self._cpu if cpu else self._network
        # Queue time is not time spent talking to a provider. Give each source
        # a turn; retrieve() owns the overall search/inspection deadline.
        queue_budget = self.timeout * max(2, (len(self.providers) + 2) // 3)
        existing = self._active_work.get(work_key) if work_key else None
        if existing is not None and not existing.done():
            return await asyncio.wait_for(asyncio.shield(existing), self.timeout)
        await asyncio.wait_for(semaphore.acquire(), queue_budget)
        existing = self._active_work.get(work_key) if work_key else None
        if existing is not None and not existing.done():
            semaphore.release()
            return await asyncio.wait_for(asyncio.shield(existing), self.timeout)
        if skip_if is not None:
            skipped = skip_if()
            if skipped is not None:
                semaphore.release()
                return skipped
        if cpu:
            worker = asyncio.create_task(asyncio.to_thread(function, *args))
        else:
            with provider_request_scope(self.timeout):
                worker = asyncio.create_task(asyncio.to_thread(function, *args))
        self._workers.add(worker)
        if work_key:
            self._active_work[work_key] = worker

        def finished(task):
            semaphore.release()
            self._workers.discard(task)
            if work_key and self._active_work.get(work_key) is task:
                self._active_work.pop(work_key, None)
            if not task.cancelled():
                task.exception()  # A caller may already have timed out.

        worker.add_done_callback(finished)
        return await asyncio.wait_for(asyncio.shield(worker), self.timeout)

    async def _shared(self, tasks, key, factory):
        entry = tasks.get(key)
        if entry is not None and entry["task"].cancelling():
            entry = None
        if entry is None:
            task = asyncio.create_task(factory())
            entry = {"task": task, "waiters": 0}
            tasks[key] = entry

            def finished(completed):
                if tasks.get(key) is entry:
                    tasks.pop(key, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        task = entry["task"]
        entry["waiters"] += 1
        try:
            return await asyncio.shield(task)
        finally:
            entry["waiters"] -= 1
            if not entry["waiters"] and not task.done():
                # Keep a shared call for remaining users; discard orphaned
                # queued requests when the last interested task is cancelled.
                task.cancel()

    async def _search_source(self, provider, query):
        def cooldown():
            value = self._cooldowns.get(id(provider))
            return value[1] if value and value[0] > time.monotonic() else None

        cooling = cooldown()
        if cooling is not None:
            return cooling
        identity = query.to_dict()
        for name in ("video_path", "duration_seconds", "expected_duration_seconds", "fps", "file_size"):
            identity.pop(name, None)
        key = (id(provider), json.dumps(identity, sort_keys=True, ensure_ascii=False))
        cached = self._search_cache.get(key)
        if cached and cached[0] > time.monotonic():
            self._search_cache.move_to_end(key)
            return cached[1]

        async def fetch():
            result = await self._blocking(provider.search, query, skip_if=cooldown, work_key=("search", key))
            # Preserve the exact operational state during backoff. Returning a
            # visible quota/auth failure is different from caching "no match".
            if getattr(result, "status", "") in {"rate_limited", "auth_required", "access_denied", "user_action_required"}:
                self._cooldowns[id(provider)] = (time.monotonic() + 60, result)
            if getattr(result, "status", "") in {"ok", "no_results"}:
                self._search_cache[key] = (time.monotonic() + 120, result)
                self._search_cache.move_to_end(key)
                while len(self._search_cache) > 128:
                    self._search_cache.popitem(last=False)
            return result

        return await self._shared(self._search_tasks, key, fetch)

    async def _download(self, provider, candidate):
        # Refreshable signed URLs are not stable identities. The provider's
        # item ID identifies the cached file within this configured instance.
        key = (id(provider), candidate.provider, candidate.candidate_id)
        now = time.monotonic()
        for stale in [k for k, v in self._downloads.items() if v[0] <= now]:
            self._downloads.pop(stale, None)
        cached = self._downloads.get(key)
        if cached:
            return cached[1:]

        async def fetch():
            cooling = self._cooldowns.get(id(provider))
            if cooling and cooling[0] > time.monotonic():
                raise ProviderError(cooling[1].status, cooling[1].message)
            try:
                content, filename = await self._blocking(provider.download, candidate, work_key=("download", key))
            except ProviderError as exc:
                # Login and file-specific challenges can vary between uploads;
                # only a quota failure pauses every download at this source.
                if exc.status == "rate_limited":
                    self._cooldowns[id(provider)] = (time.monotonic() + 60,
                        ProviderResult(candidate.provider, exc.status, message=exc.message))
                raise
            if len(content) > int(self.config.get("max_download_bytes", 16 * 1024 * 1024)):
                raise ValueError("download_too_large")
            if sum(len(v[1]) for v in self._downloads.values()) + len(content) > 48 * 1024 * 1024:
                self._downloads.clear()
            self._downloads[key] = (time.monotonic() + 600, content, filename)
            return content, filename

        return await self._shared(self._download_tasks, key, fetch)

    async def retrieve(self, query: MediaQuery, *, source_url: str = "", timeout_seconds: float | None = None) -> tuple[list[Selection], dict]:
        budget = max(0.01, float(timeout_seconds if timeout_seconds is not None else self.config.get("retrieval_timeout_seconds", 180)))
        deadline = time.monotonic() + budget
        retrieval_limited = False
        if source_url and not any(getattr(p, "supports_detail_url", lambda _: False)(source_url) for p in self.providers):
            raise ValueError("unsupported_subtitle_source_url")
        if not query.original_language:
            return [], {"status": "metadata_required", "reason": "original_language_unknown", "providers": [], "rejections": []}
        reports, candidates = [], []
        rejections: list[dict] = []
        download_failures: dict[str, list[str]] = {}
        documents_seen: set[str] = set()
        operational_errors = {"auth_required", "user_action_required", "access_denied", "rate_limited",
                              "unavailable", "timeout", "download_timeout", "unsafe_url", "source_error", "invalid_response"}

        def reject(candidate, reason, filename=""):
            if len(rejections) < 50:
                rejections.append({"provider": candidate.provider, "candidate_id": candidate.candidate_id,
                                   "filename": filename, "reason": reason[:150]})

        def source_name(provider):
            return str(getattr(provider, "name", None) or getattr(provider, "provider", None) or type(provider).__name__)

        async def search(provider):
            try:
                if source_url and getattr(provider, "supports_detail_url", lambda _: False)(source_url):
                    result = await self._shared(self._search_tasks, (id(provider), "detail", source_url),
                        lambda: self._blocking(provider.lookup_detail, source_url, work_key=("detail", id(provider), source_url)))
                else:
                    result = await self._search_source(provider, query)
                return provider, result, ""
            except asyncio.TimeoutError:
                return provider, None, "timeout"
            except Exception as exc:
                logger.warning("字幕来源查询失败：%s (%s)", source_name(provider), type(exc).__name__)
                return provider, None, str(getattr(exc, "status", None) or getattr(exc, "code", "source_error"))

        searches = [asyncio.create_task(search(p)) for p in self.providers]
        try:
            if searches:
                _, pending = await asyncio.wait(searches, timeout=budget * 0.45)
                retrieval_limited = bool(pending)
            outcomes = [task.result() if task.done() and not task.cancelled() else (provider, None, "timeout")
                        for provider, task in zip(self.providers, searches)]
        finally:
            for task in searches:
                if not task.done():
                    task.cancel()
            if searches:
                await asyncio.gather(*searches, return_exceptions=True)
        # Round-robin candidate intake prevents a large low-quality source from
        # consuming the whole download budget before another source is inspected.
        groups = []
        for provider, result, error in outcomes:
            name = source_name(provider)
            if error:
                reports.append({"provider": name, "status": error, "count": 0})
                continue
            try:
                report = result.to_dict() if hasattr(result, "to_dict") else dict(result)
                raw = getattr(result, "candidates", report.get("candidates", []))
                if not isinstance(raw, (list, tuple)):
                    raise TypeError("invalid candidates")
            except (TypeError, ValueError, AttributeError):
                reports.append({"provider": name, "status": "invalid_response", "count": 0})
                continue
            reports.append({"provider": report.get("provider", name), "status": report.get("status", "ok"),
                            "message": report.get("message", ""), "count": len(raw)})
            group = []
            for candidate in raw:
                try:
                    if isinstance(candidate, dict):
                        candidate = SubtitleCandidate.from_dict(candidate)
                    if not isinstance(candidate, SubtitleCandidate):
                        raise TypeError("invalid candidate")
                    match = match_candidate(query, candidate)
                except (TypeError, ValueError, AttributeError):
                    reports[-1]["invalid_candidates"] = reports[-1].get("invalid_candidates", 0) + 1
                    continue
                if match.accepted:
                    group.append((provider, candidate, match.score))
                else:
                    reject(candidate, ",".join(match.reasons))
            def inspection_order(entry):
                candidate = entry[1]
                # Source labels only allocate the bounded download budget.
                # They are never used to accept or finally rank a subtitle.
                advertised = QualityReport(True, language=candidate.language,
                    bilingual=bool(candidate.metadata.get("bilingual")), format=candidate.subtitle_format)
                return (subtitle_priority(query, advertised), entry[2], candidate.downloads)
            groups.append(sorted(group, key=inspection_order, reverse=True))
        while any(groups) and len(candidates) < self.limit:
            for group in groups:
                if group and len(candidates) < self.limit:
                    candidates.append(group.pop(0))
        async def inspect_bounded(entry):
            async with self._inspection:
                return await inspect(entry)

        async def inspect(entry):
            provider, candidate, _ = entry
            try:
                content, filename = await self._download(provider, candidate)
                documents = await self._blocking(extract_subtitles, filename, content, cpu=True)
                documents_seen.add(candidate.provider)
                for document in documents:
                    scoped = query
                    if query.media_type in {"series", "tv"} and query.episode is None:
                        season, episodes = episode_coordinates(document.filename)
                        mapped = mapped_episode(candidate, document.filename)
                        if mapped is not None:
                            season, number = mapped
                            episodes = {number}
                        elif candidate.metadata.get("episode_mapping"):
                            reject(candidate, "episode_mapping_unresolved", document.filename)
                            continue
                        episode = next(iter(episodes)) if len(episodes) == 1 else None
                        if len(episodes) > 1:
                            reject(candidate, "document_multi_episode", document.filename)
                            continue
                        if episode is None:
                            episode = candidate.episode
                        if season is None:
                            season = candidate.season
                        if season is None or episode is None:
                            reject(candidate, "episode_mapping_unresolved", document.filename)
                            continue
                        if query.season is not None and season != query.season:
                            reject(candidate, "document_season_mismatch", document.filename)
                            continue
                        scoped = replace(query, season=season, episode=episode)
                    match = match_document(scoped, candidate, document)
                    if not match.accepted:
                        reject(candidate, ",".join(match.reasons), document.filename)
                        continue
                    quality = await self._blocking(inspect_subtitle, document, scoped, cpu=True)
                    rank = rank_subtitle(scoped, candidate, document, quality)
                    if rank is not None:
                        remember(Selection(scoped, candidate, document, quality, rank))
                    else:
                        reject(candidate, ",".join(quality.reasons) or "outside_language_priority", document.filename)
                return []
            except asyncio.TimeoutError:
                reason = "download_timeout"
            except Exception as exc:
                reason = str(getattr(exc, "status", None) or getattr(exc, "code", "")) or (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
            reject(candidate, reason)
            if reason in operational_errors:
                download_failures.setdefault(candidate.provider, []).append(reason)
            return []

        best: dict[tuple, Selection] = {}
        selected_bytes, selection_budget_reached = 0, False

        def memory_size(selection):
            return len(selection.document.content) + sys.getsizeof(selection.quality.normalized_text)

        def remember(selection):
            nonlocal selected_bytes, selection_budget_reached
            # Commit each inspected member immediately, including members of
            # a season pack whose remaining files may exceed the deadline.
            key = (selection.query.season, selection.query.episode)
            if key in best and selection.rank <= best[key].rank:
                return
            previous = memory_size(best[key]) if key in best else 0
            next_size = selected_bytes - previous + memory_size(selection)
            if next_size > self._selection_budget:
                selection_budget_reached = True
                reject(selection.candidate, "selection_memory_limit", selection.document.filename)
                return
            best[key] = selection
            selected_bytes = next_size

        tasks = {asyncio.create_task(inspect_bounded(entry)) for entry in candidates}
        for task in tasks:
            task.add_done_callback(tasks.discard)
        try:
            for completed in asyncio.as_completed(tasks, timeout=max(0.001, deadline - time.monotonic())):
                await completed
        except TimeoutError:
            # Keep already checked winners when a slow source exhausts the
            # operation budget. Explicit user cancellation still propagates.
            retrieval_limited = True
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        values = sorted(best.values(), key=lambda s: (s.query.season or 0, s.query.episode or 0))
        status = "matched" if values else "no_match"
        if not values and retrieval_limited:
            status = "unavailable"
        for report in reports:
            failures = download_failures.get(report["provider"], [])
            if failures:
                report["download_failures"] = list(dict.fromkeys(failures))
                if report["provider"] not in documents_seen:
                    # Preserve the fact that search succeeded while making the
                    # subsequent authorization/challenge failure visible.
                    report["search_status"] = report["status"]
                    report["status"] = failures[0]
        if not values and candidates and not documents_seen and sum(map(len, download_failures.values())) == len(candidates):
            status = "unavailable"
        if not candidates and reports and all(r["status"] not in {"ok", "no_results", "no_match", "completed"} for r in reports):
            status = "unavailable"
        return values, {"status": status, "providers": reports, "examined_candidates": len(candidates),
                        "candidate_limit_reached": any(groups), "selection_budget_reached": selection_budget_reached,
                        "retrieval_budget_reached": retrieval_limited,
                        "warnings": (["selection_memory_limit"] if selection_budget_reached else []) +
                                    (["retrieval_budget_reached"] if retrieval_limited else []), "rejections": rejections}
