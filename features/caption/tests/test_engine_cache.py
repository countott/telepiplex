from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
import time

from telepiplex_caption.engine import CaptionEngine
from telepiplex_caption.models import MediaQuery
from test_engine import FixtureProvider, _candidate
from test_quality import make_srt


def _count_cpu(monkeypatch):
    import telepiplex_caption.engine as module
    counts = {"extract": 0, "quality": 0}
    for name, key in (("extract_subtitles", "extract"), ("inspect_subtitle", "quality")):
        original = getattr(module, name)
        def wrapper(*args, _original=original, _key=key):
            counts[_key] += 1
            time.sleep(0.01)
            return _original(*args)
        monkeypatch.setattr(module, name, wrapper)
    return counts


def test_identical_concurrent_retrievals_share_extraction_and_quality(monkeypatch):
    counts = _count_cpu(monkeypatch)
    provider = FixtureProvider(candidates=[_candidate()], downloads={"1": make_srt()})
    engine = CaptionEngine({}, providers=[provider])
    async def run():
        query = MediaQuery("Example", year=2020, original_language="zh")
        results = await asyncio.gather(*(engine.retrieve(query) for _ in range(12)))
        assert all(len(selected) == 1 for selected, _ in results)
        assert (await engine.retrieve(query))[0]
    asyncio.run(run())
    assert counts == {"extract": 1, "quality": 1}
    assert [kind for kind, _ in provider.requests].count("download") == 1


def test_quality_cache_never_reuses_different_video_timing_context(monkeypatch):
    counts = _count_cpu(monkeypatch)
    provider = FixtureProvider(candidates=[_candidate()], downloads={"1": make_srt()})
    engine = CaptionEngine({}, providers=[provider])
    async def run():
        query = MediaQuery("Example", year=2020, original_language="zh")
        assert (await engine.retrieve(query))[0]
        selected, report = await engine.retrieve(replace(query, duration_seconds=3600))
        assert not selected
        assert any("likely_partial_subtitle" in row["reason"] for row in report["rejections"])
    asyncio.run(run())
    assert counts == {"extract": 1, "quality": 2}


def test_rejected_quality_reports_and_archive_failures_are_not_cached(monkeypatch):
    import telepiplex_caption.engine as module
    counts = _count_cpu(monkeypatch)
    original = module.extract_subtitles
    attempts = 0
    def transient(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("transient_decoder_failure")
        return original(*args)
    monkeypatch.setattr(module, "extract_subtitles", transient)
    body = replace(make_srt(), content=b"not subtitle dialogue")
    provider = FixtureProvider(candidates=[_candidate()], downloads={"1": body})
    engine = CaptionEngine({}, providers=[provider])
    async def run():
        query = MediaQuery("Example", year=2020, original_language="zh")
        for _ in range(3):
            assert not (await engine.retrieve(query))[0]
    asyncio.run(run())
    assert attempts == 2 and counts == {"extract": 1, "quality": 2}


def test_cpu_cache_is_bounded_by_combined_bytes_items_and_ttl():
    from telepiplex_caption.cache import BoundedCPUCache
    now = [0.0]
    cache = BoundedCPUCache(max_bytes=4096, max_entries=2, ttl_seconds=600, clock=lambda: now[0])
    cache.put(("extract", 1), (b"body",))
    cache.put(("quality", 1), "normalized text")
    cache.put(("quality", 2), "other text")
    assert len(cache) == 2 and cache.get(("extract", 1)) is None
    assert cache.total_bytes <= 4096
    cache.put(("extract", 3), (b"x" * 4096,))
    assert len(cache) == 2 and cache.get(("extract", 3)) is None
    now[0] = 600
    assert cache.get(("quality", 2)) is None
    assert len(cache) == 0 and cache.total_bytes == 0


def test_cpu_cache_keeps_filenames_formats_and_extraction_limits_distinct(monkeypatch):
    from telepiplex_caption.archive import ExtractionLimits
    from telepiplex_caption.models import SubtitleDocument
    counts = _count_cpu(monkeypatch)
    engine = CaptionEngine({}, providers=[])
    content = make_srt().content
    async def run():
        first = await engine._extract_subtitles("chi.srt", content)
        second = await engine._extract_subtitles("cht.srt", content)
        third = await engine._extract_subtitles("chi.srt", content, ExtractionLimits(max_members=100))
        assert first[0].filename == third[0].filename == "chi.srt"
        assert second[0].filename == "cht.srt"
        query = MediaQuery("Example", original_language="zh")
        assert (await engine._inspect_subtitle(first[0], query)).accepted
        assert (await engine._inspect_subtitle(second[0], query)).accepted
        assert not (await engine._inspect_subtitle(SubtitleDocument("chi.srt", "ass", content), query)).accepted
    asyncio.run(run())
    assert counts == {"extract": 3, "quality": 3}


def test_cancelling_one_cpu_waiter_keeps_shared_work_for_another(monkeypatch):
    import telepiplex_caption.engine as module
    original = module.inspect_subtitle
    started, release = threading.Event(), threading.Event()
    calls = []
    def blocked(*args):
        calls.append(1)
        started.set()
        assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(module, "inspect_subtitle", blocked)
    engine = CaptionEngine({}, providers=[])
    async def run():
        document, query = make_srt(), MediaQuery("Example", original_language="zh")
        first = asyncio.create_task(engine._inspect_subtitle(document, query))
        second = asyncio.create_task(engine._inspect_subtitle(document, query))
        while not started.is_set():
            await asyncio.sleep(0.001)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert engine._cpu._value == 1
        release.set()
        assert (await second).accepted
        await asyncio.sleep(0)
        assert engine._cpu._value == 2
        assert not engine._workers and not engine._active_work
    asyncio.run(run())
    assert len(calls) == 1


def test_cancelled_cpu_worker_keeps_slot_and_can_be_rejoined(monkeypatch):
    import telepiplex_caption.engine as module
    original = module.inspect_subtitle
    started, release = threading.Event(), threading.Event()
    calls = []
    def blocked(*args):
        calls.append(1)
        started.set()
        assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(module, "inspect_subtitle", blocked)
    engine = CaptionEngine({}, providers=[])
    async def run():
        document, query = make_srt(), MediaQuery("Example", original_language="zh")
        abandoned = asyncio.create_task(engine._inspect_subtitle(document, query))
        while not started.is_set():
            await asyncio.sleep(0.001)
        abandoned.cancel()
        await asyncio.gather(abandoned, return_exceptions=True)
        assert engine._cpu._value == 1 and len(engine._workers) == 1
        replacement = asyncio.create_task(engine._inspect_subtitle(document, query))
        await asyncio.sleep(0)
        release.set()
        assert (await replacement).accepted
        await asyncio.sleep(0)
        assert not engine._workers and not engine._active_work and not engine._cpu_tasks
        assert engine._cpu._value == 2
    asyncio.run(run())
    assert len(calls) == 1
