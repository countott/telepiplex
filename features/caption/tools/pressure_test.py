"""Offline load against the real CaptionEngine; never load-tests public sites.

PYTHONPATH=src:../../sdk/src python tools/pressure_test.py --requests 500 --output /tmp/caption-pressure.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import resource
import statistics
import sys
import threading
import time
import tracemalloc

from telepiplex_caption.engine import CaptionEngine
from telepiplex_caption.models import MediaQuery, SubtitleCandidate
from telepiplex_caption.providers import ProviderResult


def subtitle_bytes():
    cues = []
    for number in range(1, 81):
        second = (number - 1) * 3
        start = f"00:{second // 60:02d}:{second % 60:02d},000"
        stop = f"00:{(second + 2) // 60:02d}:{(second + 2) % 60:02d},000"
        cues.append(f"{number}\n{start} --> {stop}\n这是第{number}段简体字幕，我们已经确认这个世界仍然安静。\n")
    return "\n".join(cues).encode()


class FixtureSource:
    name = "pressure_fixture"

    def __init__(self, broken=False):
        self.name = "pressure_unavailable" if broken else "pressure_fixture"
        self.broken = broken
        self.lock = threading.Lock()
        self.active = self.peak = self.searches = self.downloads = 0
        self.content = subtitle_bytes()

    def _begin(self):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.002)

    def _end(self):
        with self.lock:
            self.active -= 1

    def search(self, query):
        self._begin()
        try:
            with self.lock:
                self.searches += 1
            if self.broken:
                return ProviderResult(self.name, "rate_limited", message="synthetic free-quota limit")
            identifier = f"{query.title}:{query.year}:{query.season}:{query.episode}"
            return ProviderResult(self.name, "ok", (SubtitleCandidate(
                self.name, identifier, query.title, year=query.year,
                season=query.season, episode=query.episode, media_type=query.media_type,
                language="chi", subtitle_format="srt", release_name=query.release_name,
            ),))
        finally:
            self._end()

    def download(self, candidate):
        self._begin()
        try:
            with self.lock:
                self.downloads += 1
            return self.content, "chi.srt"
        finally:
            self._end()


async def run(args):
    source_path = Path(__file__).resolve().parents[1] / "docs/library-samples-2026-09-28.json"
    samples = json.loads(source_path.read_text())["cases"]
    queries = [MediaQuery.from_dict({**row, "release_name": row["inventory_filename"]}) for row in samples]
    good, bad = FixtureSource(), FixtureSource(broken=True)
    engine = CaptionEngine({"provider_timeout_seconds": 20, "max_candidates": 12}, providers=[bad, good])
    semaphore = asyncio.Semaphore(args.concurrency)
    latencies, failures, matches = [], [], 0
    tracemalloc.start()
    started = time.perf_counter()

    async def request(index):
        nonlocal matches
        async with semaphore:
            query = queries[index % len(queries)]
            begin = time.perf_counter()
            selected, report = await engine.retrieve(query)
            latencies.append(time.perf_counter() - begin)
            if (len(selected) != 1 or selected[0].quality.language != "chi"
                    or selected[0].quality.bilingual or selected[0].quality.timing_verified):
                failures.append({"index": index, "reason": "selection_invariant", "report": report})
            elif not any(r["provider"] == bad.name and r["status"] == "rate_limited" for r in report["providers"]):
                failures.append({"index": index, "reason": "source_failure_hidden"})
            else:
                matches += 1

    await asyncio.gather(*(request(i) for i in range(args.requests)))
    elapsed = time.perf_counter() - started
    _, allocated_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # All caller-visible work is done; there must be no orphan source activity.
    await asyncio.sleep(0)
    metrics = {
        "requests": args.requests, "caller_concurrency": args.concurrency,
        "matched": matches, "failures": failures, "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(args.requests / elapsed, 2),
        "latency_p50_seconds": round(statistics.median(latencies), 3),
        "latency_p95_seconds": round(sorted(latencies)[min(len(latencies)-1, int(len(latencies)*.95))], 3),
        "python_peak_allocated_bytes": allocated_peak,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
        "good_source_searches": good.searches, "good_source_downloads": good.downloads,
        "good_source_peak_active": good.peak, "failing_source_searches": bad.searches,
        "search_cache_entries": len(engine._search_cache),
        "download_cache_bytes": sum(len(v[1]) for v in engine._downloads.values()),
        "remaining_workers": len(engine._workers),
        "remaining_active_work": len(engine._active_work),
        "remaining_shared_requests": len(engine._search_tasks) + len(engine._download_tasks),
    }
    metrics["passed"] = (matches == args.requests and not failures
        and len(queries) <= good.downloads <= len(queries) * (1 + int(elapsed / 600))
        and len(queries) <= good.searches <= len(queries) * (1 + int(elapsed / 120))
        and not engine._workers and not engine._active_work and not engine._search_tasks
        and not engine._download_tasks and len(engine._search_cache) <= 128)
    return {"scope": "Offline synthetic subtitle bodies and fault injection against real engine, using 16 inventory title identities. No public-site traffic, media writes or playback verification.",
        "verified_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "load": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 16 <= args.requests <= 10000 or not 1 <= args.concurrency <= 64:
        parser.error("requests must be 16..10000 and concurrency 1..64")
    result = asyncio.run(run(args))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["load"], ensure_ascii=False))
    raise SystemExit(0 if result["load"]["passed"] else 1)


if __name__ == "__main__":
    main()
