import asyncio
import itertools
import threading
import time

import pytest

from telepiplex_caption.engine import CaptionEngine
from telepiplex_caption.models import MediaQuery
from test_engine import FixtureProvider, _candidate
from test_quality import make_srt


def test_guaranteed_candidate_download_overlaps_other_source_search():
    events = {}
    class Fast(FixtureProvider):
        def download(self, candidate):
            events["download"] = time.monotonic()
            time.sleep(0.04)
            return super().download(candidate)
    class Slow(FixtureProvider):
        def search(self, query):
            time.sleep(0.04)
            events["slow_search_finished"] = time.monotonic()
            return super().search(query)
    first = Fast("first", [_candidate("a", "first")], {"a": make_srt()})
    second = Slow("second")
    selected, report = asyncio.run(CaptionEngine({"max_candidates": 2}, providers=[first, second]).retrieve(
        MediaQuery("Example", year=2020, original_language="zh")))
    assert selected and report["examined_candidates"] == 1
    assert events["download"] < events["slow_search_finished"]


def test_all_this_requests_sources_queue_before_its_first_download():
    queued, downloads = set(), []
    lock = threading.Lock()
    class Source(FixtureProvider):
        def search(self, query):
            time.sleep(0.002 if self.name == "0" else 0.04)
            return super().search(query)
        def download(self, candidate):
            with lock:
                downloads.append(set(queued))
            return super().download(candidate)
    providers = [Source(str(i), [_candidate(str(i), str(i))], {str(i): make_srt()}) for i in range(6)]
    engine = CaptionEngine({"max_candidates": 6}, providers=providers)
    original = engine._blocking
    async def trace(function, *args, **kwargs):
        flag = kwargs.get("queued")
        if function.__name__ == "search" and flag is not None:
            name = function.__self__.name
            class QueueFlag:
                def set(self):
                    with lock:
                        queued.add(name)
                    flag.set()
            kwargs["queued"] = QueueFlag()
        return await original(function, *args, **kwargs)
    engine._blocking = trace
    selected, _ = asyncio.run(engine.retrieve(
        MediaQuery("Example", year=2020, original_language="zh")))
    assert selected and downloads
    assert downloads[0] == {str(i) for i in range(6)}


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_pipeline_preserves_round_robin_pool_and_never_downloads_extra_candidates(order):
    delays = {index: position * 0.01 for position, index in enumerate(order)}
    class Source(FixtureProvider):
        def search(self, query):
            time.sleep(delays[int(self.name)])
            return super().search(query)
    providers = [Source(str(i), [_candidate(f"{i}-{j}", str(i), downloads=j) for j in range(count)],
        {f"{i}-{j}": make_srt() for j in range(count)}) for i, count in enumerate((4, 0, 3))]
    selected, report = asyncio.run(CaptionEngine({"max_candidates": 3}, providers=providers).retrieve(
        MediaQuery("Example", year=2020, original_language="zh")))
    actual = {(provider.name, value) for provider in providers for kind, value in provider.requests if kind == "download"}
    assert actual == {("0", "0-3"), ("2", "2-2"), ("0", "0-2")}
    assert report["examined_candidates"] == 3 and report["candidate_limit_reached"]
    assert selected[0].candidate.candidate_id == "0-3"


@pytest.mark.parametrize("search_delays,download_delays", [
    ((0, 0.04), (0, 0)),
    ((0.04, 0), (0, 0)),
    ((0, 0), (0.04, 0)),
    ((0, 0), (0, 0.04)),
])
def test_equal_quality_uses_round_robin_order_despite_completion_order(search_delays, download_delays):
    class Source(FixtureProvider):
        def search(self, query):
            time.sleep(search_delays[int(self.name)])
            return super().search(query)
        def download(self, candidate):
            time.sleep(download_delays[int(self.name)])
            return super().download(candidate)
    providers = [Source(str(i), [_candidate("a", str(i))], {"a": make_srt()}) for i in range(2)]
    selected, _ = asyncio.run(CaptionEngine({"max_candidates": 2}, providers=providers).retrieve(
        MediaQuery("Example", year=2020, original_language="zh")))
    assert selected[0].candidate.provider == "0"
    assert selected[0].rank == (200, 55, -1, 25, 0)


def test_tie_uses_round_robin_rank_before_source_order_with_empty_and_skipped_sources():
    from test_quality import make_ass
    class Slow(FixtureProvider):
        def search(self, query):
            time.sleep(0.04)
            return super().search(query)
    first = FixtureProvider("first", [_candidate("bad", "first", downloads=2), _candidate("good", "first")],
        {"bad": make_ass(bilingual=True), "good": make_srt()})
    skipped = FixtureProvider("disabled")
    skipped.config = {"enabled": False}
    empty = FixtureProvider("empty")
    second = Slow("second", [_candidate("good", "second")], {"good": make_srt()})
    selected, report = asyncio.run(CaptionEngine({"max_candidates": 4}, providers=[first, skipped, empty, second]).retrieve(
        MediaQuery("Example", year=2020, original_language="zh")))
    assert selected[0].candidate.provider == "second"
    assert report["examined_candidates"] == 3
    assert not skipped.requests


def test_equal_quality_archive_members_keep_original_member_order():
    from dataclasses import replace
    from test_engine import _zip
    documents = [replace(make_srt(), filename=f"Example.2020.{name}.srt") for name in ("first", "second")]
    provider = FixtureProvider(candidates=[_candidate()], downloads={"1": (_zip(*documents), "Example.zip")})
    selected, _ = asyncio.run(CaptionEngine({}, providers=[provider]).retrieve(
        MediaQuery("Example", year=2020, original_language="zh")))
    assert selected[0].document.filename == documents[0].filename
