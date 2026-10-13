"""Search real sources, inspect real subtitle contents, then apply user priorities."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
import json
import hashlib
import logging
import sys
import time

from .archive import ExtractionLimits, extract_subtitles
from .cache import BoundedCPUCache
from .catalog_providers import mapped_episode
from .matching import episode_coordinates, match_candidate, match_document
from .models import MediaQuery, QualityReport, SubtitleCandidate, SubtitleDocument
from .providers import ProviderError, ProviderResult, build_providers, provider_request_scope
from .quality import inspect_subtitle, rank_subtitle, subtitle_priority
from .source_policy import source_search_decision

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
        self.source_profile = str(config.get("source_profile", "balanced"))
        if self.source_profile not in {"balanced", "all"}:
            raise ValueError("invalid_subtitle_source_profile")
        self.providers = list(providers) if providers is not None else build_providers(config)
        self.timeout = float(config.get("provider_timeout_seconds", 30))
        self.limit = max(1, min(100, int(config.get("max_candidates", 30))))
        self._downloads: dict[tuple, tuple[float, bytes, str]] = {}
        self._search_cache = OrderedDict()
        self._download_tasks = {}
        self._search_tasks = {}
        self._cpu_tasks = {}
        self._cpu_cache = BoundedCPUCache()
        self._cooldowns = {}
        self._workers = set()
        self._active_work = {}
        self._network = asyncio.Semaphore(3)
        self._inspection = asyncio.Semaphore(2)
        self._cpu = asyncio.Semaphore(2)
        # Both raw bodies and decoded strings count towards retained selections.
        # A large whole-series request stops with an explicit partial warning.
        self._selection_budget = 64 * 1024 * 1024

    async def _blocking(self, function, *args, cpu=False, skip_if=None, work_key=None, queued=None):
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
            if queued is not None:
                queued.set()
            return await asyncio.wait_for(asyncio.shield(existing), self.timeout)
        if queued is None:
            await asyncio.wait_for(semaphore.acquire(), queue_budget)
        else:
            reservation = asyncio.create_task(semaphore.acquire())
            try:
                # The reservation runs first, either acquiring a slot or joining
                # its FIFO queue. Only then may this request enqueue downloads.
                await asyncio.sleep(0)
                queued.set()
                await asyncio.wait_for(reservation, queue_budget)
            except BaseException:
                if reservation.done() and not reservation.cancelled() and reservation.exception() is None:
                    semaphore.release()
                else:
                    reservation.cancel()
                    await asyncio.gather(reservation, return_exceptions=True)
                raise
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

    async def _shared(self, tasks, key, factory, *, queued=None, queueable=False):
        entry = tasks.get(key)
        if entry is not None and entry["task"].cancelling():
            entry = None
        if entry is None:
            queue_state = asyncio.Event()
            task = asyncio.create_task(factory(queue_state) if queueable else factory())
            entry = {"task": task, "waiters": 0, "queued": queue_state}
            tasks[key] = entry

            def finished(completed):
                if tasks.get(key) is entry:
                    tasks.pop(key, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        task = entry["task"]
        entry["waiters"] += 1
        queue_waiter = None
        try:
            if queued is not None:
                queue_waiter = asyncio.create_task(entry["queued"].wait())
                await asyncio.wait((queue_waiter, task), return_when=asyncio.FIRST_COMPLETED)
                queued.set()
            return await asyncio.shield(task)
        finally:
            if queued is not None:
                queued.set()
            if queue_waiter is not None:
                queue_waiter.cancel()
                await asyncio.gather(queue_waiter, return_exceptions=True)
            entry["waiters"] -= 1
            if not entry["waiters"] and not task.done():
                # Keep a shared call for remaining users; discard orphaned
                # queued requests when the last interested task is cancelled.
                task.cancel()

    async def _cached_cpu(self, key, function, *args, accepted=lambda value: True):
        cached = self._cpu_cache.get(key)
        if cached is not None:
            return cached

        async def compute():
            value = await self._blocking(function, *args, cpu=True, work_key=("cpu", key))
            if key[0] == "extract":
                value = tuple(value)
            if accepted(value):
                self._cpu_cache.put(key, value)
            return value

        return await self._shared(self._cpu_tasks, key, compute)

    async def _extract_subtitles(self, filename, content, limits=None):
        key = ("extract", hashlib.sha256(content).digest(), filename, limits or ExtractionLimits())
        # Preserve the decoder's default call signature for existing integrations.
        args = (filename, content) if limits is None else (filename, content, limits)
        return await self._cached_cpu(key, extract_subtitles, *args)

    async def _inspect_subtitle(self, document, query):
        context = json.dumps(query.to_dict() if query else None, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"))
        key = ("quality", hashlib.sha256(document.content).digest(), document.filename, document.format, context)
        return await self._cached_cpu(key, inspect_subtitle, document, query, accepted=lambda report: report.accepted)

    async def _search_source(self, provider, query, *, queued=None):
        def cooldown():
            value = self._cooldowns.get(id(provider))
            return value[1] if value and value[0] > time.monotonic() else None

        cooling = cooldown()
        if cooling is not None:
            if queued is not None:
                queued.set()
            return cooling
        identity = query.to_dict()
        for name in ("video_path", "duration_seconds", "expected_duration_seconds", "fps", "file_size"):
            identity.pop(name, None)
        key = (id(provider), json.dumps(identity, sort_keys=True, ensure_ascii=False))
        cached = self._search_cache.get(key)
        if cached and cached[0] > time.monotonic():
            self._search_cache.move_to_end(key)
            if queued is not None:
                queued.set()
            return cached[1]

        async def fetch(queue_state):
            result = await self._blocking(provider.search, query, skip_if=cooldown,
                                          work_key=("search", key), queued=queue_state)
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

        return await self._shared(self._search_tasks, key, fetch, queued=queued, queueable=True)

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

    async def _detail_source(self, provider, url, *, queued=None):
        def quota_cooldown():
            cooling = self._cooldowns.get(id(provider))
            if cooling and cooling[0] > time.monotonic() and cooling[1].status == "rate_limited":
                return cooling[1]
            return None

        cooling = quota_cooldown()
        if cooling is not None:
            if queued is not None:
                queued.set()
            return cooling

        async def fetch(queue_state):
            result = await self._blocking(provider.lookup_detail, url, skip_if=quota_cooldown,
                                          work_key=("detail", id(provider), url), queued=queue_state)
            if getattr(result, "status", "") == "rate_limited":
                self._cooldowns[id(provider)] = (time.monotonic() + 60, result)
            return result

        return await self._shared(self._search_tasks, (id(provider), "detail", url), fetch,
                                  queued=queued, queueable=True)

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

        # Decide locally before allocating a worker or a network slot. A source
        # allowed in config can still be irrelevant to this work or lack input.
        decisions = []
        searched_sources = []
        for provider in self.providers:
            name = source_name(provider)
            if source_url:
                settings = getattr(provider, "config", {}) or {}
                if settings.get("enabled", True) is False:
                    skipped = ProviderResult(name, "disabled", message="此字幕来源已停用")
                elif not getattr(provider, "supports_detail_url", lambda _: False)(source_url):
                    skipped = ProviderResult(name, "skipped", message="本次仅检索指定的字幕详情链接")
                else:
                    skipped = None
            else:
                decision = source_search_decision(provider, query, profile=self.source_profile)
                skipped = None if decision.search else ProviderResult(name, decision.status, message=decision.message)
            decisions.append(skipped)
            if skipped is None:
                searched_sources.append(name)

        async def search(provider, queued):
            try:
                if source_url and getattr(provider, "supports_detail_url", lambda _: False)(source_url):
                    result = await self._detail_source(provider, source_url, queued=queued)
                else:
                    result = await self._search_source(provider, query, queued=queued)
                return provider, result, ""
            except asyncio.TimeoutError:
                return provider, None, "timeout"
            except Exception as exc:
                logger.warning("字幕来源查询失败：%s (%s)", source_name(provider), type(exc).__name__)
                return provider, None, str(getattr(exc, "status", None) or getattr(exc, "code", "source_error"))
            finally:
                queued.set()

        def parse_outcome(provider, result, error):
            rejected = []
            name = source_name(provider)
            if error:
                return {"provider": name, "status": error, "count": 0}, (), ()
            try:
                report = result.to_dict() if hasattr(result, "to_dict") else dict(result)
                raw = getattr(result, "candidates", report.get("candidates", []))
                if not isinstance(raw, (list, tuple)):
                    raise TypeError("invalid candidates")
            except (TypeError, ValueError, AttributeError):
                return {"provider": name, "status": "invalid_response", "count": 0}, (), ()
            source_report = {"provider": report.get("provider", name), "status": report.get("status", "ok"),
                             "message": report.get("message", ""), "count": len(raw)}
            group = []
            for candidate in raw:
                try:
                    if isinstance(candidate, dict):
                        candidate = SubtitleCandidate.from_dict(candidate)
                    if not isinstance(candidate, SubtitleCandidate):
                        raise TypeError("invalid candidate")
                    match = match_candidate(query, candidate)
                except (TypeError, ValueError, AttributeError):
                    source_report["invalid_candidates"] = source_report.get("invalid_candidates", 0) + 1
                    continue
                if match.accepted:
                    group.append((provider, candidate, match.score))
                else:
                    rejected.append((candidate, ",".join(match.reasons)))
            def inspection_order(entry):
                candidate = entry[1]
                # Source labels only allocate the bounded download budget.
                # They are never used to accept or finally rank a subtitle.
                advertised = QualityReport(True, language=candidate.language,
                    bilingual=bool(candidate.metadata.get("bilingual")), format=candidate.subtitle_format)
                return (subtitle_priority(query, advertised), entry[2], candidate.downloads)
            # Snapshot once: speculative and final intake use exactly this pool.
            return source_report, tuple(sorted(group, key=inspection_order, reverse=True)), tuple(rejected)
        async def inspect_bounded(entry, tie_order):
            async with self._inspection:
                return await inspect(entry, tie_order)

        async def inspect(entry, tie_order):
            provider, candidate, _ = entry
            try:
                content, filename = await self._download(provider, candidate)
                documents = await self._extract_subtitles(filename, content)
                documents_seen.add(candidate.provider)
                for member_index, document in enumerate(documents):
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
                    quality = await self._inspect_subtitle(document, scoped)
                    rank = rank_subtitle(scoped, candidate, document, quality)
                    if rank is not None:
                        remember(Selection(scoped, candidate, document, quality, rank), (*tie_order, member_index))
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
        best_order: dict[tuple, tuple] = {}
        selected_bytes, selection_budget_reached = 0, False

        def memory_size(selection):
            return len(selection.document.content) + sys.getsizeof(selection.quality.normalized_text)

        def remember(selection, tie_order):
            nonlocal selected_bytes, selection_budget_reached
            # Commit each inspected member immediately, including members of
            # a season pack whose remaining files may exceed the deadline.
            key = (selection.query.season, selection.query.episode)
            if key in best:
                if selection.rank < best[key].rank:
                    return
                if selection.rank == best[key].rank and tie_order >= best_order[key]:
                    return
            previous = memory_size(best[key]) if key in best else 0
            next_size = selected_bytes - previous + memory_size(selection)
            if next_size > self._selection_budget:
                selection_budget_reached = True
                reject(selection.candidate, "selection_memory_limit", selection.document.filename)
                return
            best[key] = selection
            best_order[key] = tie_order
            selected_bytes = next_size

        inspections = {}

        def start_inspection(index, rank, entry):
            key = (index, rank)
            if key not in inspections:
                # Source speed never wins equal business ranks. The immutable
                # round-robin order is pool rank, configured source, then member.
                inspections[key] = asyncio.create_task(inspect_bounded(entry, (rank, index)))

        queue_states = [asyncio.Event() for _ in self.providers]
        searches = [asyncio.create_task(search(provider, queue_states[index])) if skipped is None else None
                    for index, (provider, skipped) in enumerate(zip(self.providers, decisions))]
        active = {task for task in searches if task is not None}
        ordinals = {index: ordinal for ordinal, index in enumerate(
            index for index, skipped in enumerate(decisions) if skipped is None)}
        source_count = len(ordinals)
        parsed = {}
        search_deadline = time.monotonic() + budget * 0.45
        pending = set(active)
        try:
            if active:
                await asyncio.wait_for(asyncio.gather(*(queue_states[index].wait() for index in ordinals)),
                                       max(0.001, search_deadline - time.monotonic()))
            while pending:
                done, pending = await asyncio.wait(pending, timeout=max(0, search_deadline - time.monotonic()),
                                                   return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    break
                for index, task in enumerate(searches):
                    if task not in done or task.cancelled():
                        continue
                    parsed[index] = parse_outcome(*task.result())
                    for rank, entry in enumerate(parsed[index][1]):
                        # This is an upper bound on the eventual round-robin
                        # position, even if every other source returns candidates.
                        if rank * source_count + ordinals[index] + 1 <= self.limit:
                            start_inspection(index, rank, entry)
            retrieval_limited = bool(pending)
        except TimeoutError:
            retrieval_limited = True
        except BaseException:
            for task in inspections.values():
                task.cancel()
            await asyncio.gather(*inspections.values(), return_exceptions=True)
            raise
        finally:
            outcomes = [(provider, skipped, "") if skipped is not None else
                        task.result() if task.done() and not task.cancelled() else (provider, None, "timeout")
                        for provider, skipped, task in zip(self.providers, decisions, searches)]
            for task in active:
                if not task.done():
                    task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)

        # Preserve the original final round-robin admission, including unused
        # slots from empty/timed-out sources. Never inspect speculative extras.
        groups = []
        for index, outcome in enumerate(outcomes):
            source_report, pool, rejected = parsed.get(index) or parse_outcome(*outcome)
            reports.append(source_report)
            for candidate, reason in rejected:
                reject(candidate, reason)
            groups.append([(index, rank, entry) for rank, entry in enumerate(pool)])
        while any(groups) and len(candidates) < self.limit:
            for group in groups:
                if group and len(candidates) < self.limit:
                    candidates.append(group.pop(0))
        for index, rank, entry in candidates:
            start_inspection(index, rank, entry)
        tasks = set(inspections.values())
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
        attempted_reports = [r for r in reports if r["provider"] in searched_sources]
        if not candidates and attempted_reports and all(r["status"] not in {"ok", "no_results", "no_match", "completed"} for r in attempted_reports):
            status = "unavailable"
        return values, {"status": status, "providers": reports, "examined_candidates": len(candidates),
                        "source_profile": self.source_profile, "searched_sources": searched_sources,
                        "source_mode": "detail" if source_url else "search",
                        "reason": "" if searched_sources else "no_eligible_sources",
                        "candidate_limit_reached": any(groups), "selection_budget_reached": selection_budget_reached,
                        "retrieval_budget_reached": retrieval_limited,
                        "warnings": (["selection_memory_limit"] if selection_budget_reached else []) +
                                    (["retrieval_budget_reached"] if retrieval_limited else []), "rejections": rejections}
