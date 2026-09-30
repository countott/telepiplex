from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import threading
import time
import zipfile

from telepiplex_caption.engine import CaptionEngine
from telepiplex_caption.models import MediaQuery, SubtitleCandidate
from telepiplex_caption.providers import AssrtProvider, HttpResponse, ProviderError, ProviderResult
from test_quality import make_ass, make_srt


def _zip(*documents):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for document in documents:
            archive.writestr(document.filename, document.content)
    return output.getvalue()


class FixtureProvider:
    def __init__(self, name="fixture", candidates=(), downloads=None, status="ok"):
        self.name, self.candidates, self.downloads, self.status = name, tuple(candidates), downloads or {}, status
        self.requests = []

    def search(self, query):
        self.requests.append(("search", query))
        return ProviderResult(self.name, self.status, self.candidates)

    def download(self, candidate):
        self.requests.append(("download", candidate.candidate_id))
        result = self.downloads[candidate.candidate_id]
        if isinstance(result, Exception):
            raise result
        if hasattr(result, "content"):
            return result.content, result.filename
        return result


def _candidate(id="1", provider="fixture", **kwargs):
    return SubtitleCandidate(provider, id, "Example 2020", year=2020, **kwargs)


def retrieve(provider, query=None, config=None):
    return asyncio.run(CaptionEngine(config or {}, providers=[provider]).retrieve(query or MediaQuery("Example", year=2020, original_language="en")))


def test_user_priority_depends_on_body_not_provider_label_or_download_count():
    provider = FixtureProvider(candidates=[
        _candidate("mono", language="chi", subtitle_format="ass", metadata={"bilingual": True}, downloads=1_000_000),
        _candidate("bilingual", language="cht", subtitle_format="srt", downloads=0),
    ], downloads={"mono": make_srt(), "bilingual": make_ass(bilingual=True)})
    choices, report = retrieve(provider)
    assert report["status"] == "matched"
    assert choices[0].candidate.candidate_id == "bilingual"
    assert choices[0].summary()["language"] == "chi"
    assert choices[0].summary()["bilingual"] is True


def test_provider_round_robin_reserves_candidate_budget_for_other_sources():
    first = FixtureProvider("first", [_candidate(str(i), "first") for i in range(5)], {str(i): make_srt() for i in range(5)})
    second = FixtureProvider("second", [_candidate("b", "second")], {"b": make_ass(bilingual=True)})
    query = MediaQuery("Example", year=2020, original_language="en")
    choices, report = asyncio.run(CaptionEngine({"max_candidates": 2}, providers=[first, second]).retrieve(query))
    assert report["examined_candidates"] == 2 and report["candidate_limit_reached"]
    assert choices[0].candidate.provider == "second"
    assert ("download", "b") in second.requests


def test_unknown_original_language_makes_no_network_requests():
    provider = FixtureProvider()
    choices, report = retrieve(provider, MediaQuery("Example"))
    assert not choices and report["status"] == "metadata_required"
    assert provider.requests == []


def test_no_video_season_zip_selects_one_best_subtitle_per_episode():
    documents = [
        replace(make_srt(), filename="Example.S01E01.srt"),
        replace(make_ass(bilingual=True), filename="Example.S01E01.ass"),
        replace(make_srt(bilingual=True), filename="Example.S01E02.srt"),
        replace(make_srt(bilingual=True), filename="Example.S02E01.srt"),
        replace(make_srt(), filename="chs.srt"),
    ]
    provider = FixtureProvider(candidates=[_candidate(season=1)], downloads={"1": (_zip(*documents), "Example.S01.zip")})
    choices, report = retrieve(provider, MediaQuery("Example", year=2020, media_type="series", season=1, original_language="en"))
    assert [(x.query.season, x.query.episode, x.document.format) for x in choices] == [(1, 1, "ass"), (1, 2, "srt")]
    assert all(not choice.query.video_path for choice in choices)
    assert {r["reason"] for r in report["rejections"]} >= {"document_season_mismatch", "episode_mapping_unresolved"}


def test_whole_series_does_not_guess_multiepisode_member_or_missing_coordinates():
    documents = [replace(make_srt(), filename="Example.S01E01-E02.srt"), replace(make_srt(), filename="Example.S02E01.srt")]
    provider = FixtureProvider(candidates=[_candidate()], downloads={"1": (_zip(*documents), "Example.zip")})
    choices, report = retrieve(provider, MediaQuery("Example", year=2020, media_type="series", original_language="zh"))
    assert [(x.query.season, x.query.episode) for x in choices] == [(2, 1)]
    assert any(r["reason"] == "document_multi_episode" for r in report["rejections"])


def test_candidate_season_plus_explicit_episode_only_member_is_supported():
    provider = FixtureProvider(candidates=[_candidate(season=2)], downloads={"1": (_zip(replace(make_srt(), filename="E03.srt")), "Example.S02.zip")})
    choices, _ = retrieve(provider, MediaQuery("Example", year=2020, media_type="series", season=2, original_language="zh"))
    assert [(x.query.season, x.query.episode) for x in choices] == [(2, 3)]


def test_bad_source_does_not_break_good_source_and_preserves_actionable_status():
    class BrokenProvider:
        name = "broken"
        def search(self, query):
            raise ProviderError("auth_required", "token missing")
    good = FixtureProvider(candidates=[_candidate()], downloads={"1": make_srt()})
    choices, report = asyncio.run(CaptionEngine({}, providers=[BrokenProvider(), good]).retrieve(MediaQuery("Example", year=2020, original_language="en")))
    assert len(choices) == 1
    assert report["providers"][0]["status"] == "auth_required"


def test_download_challenge_reason_is_not_hidden_as_exception_type():
    provider = FixtureProvider(candidates=[_candidate()], downloads={"1": ProviderError("user_action_required", "captcha")})
    choices, report = retrieve(provider)
    assert not choices and report["status"] == "unavailable"
    assert report["rejections"][0]["reason"] == "user_action_required"
    assert report["providers"][0]["status"] == "user_action_required"
    assert report["providers"][0]["search_status"] == "ok"


def test_actual_content_rejection_remains_no_match_despite_another_source_challenge():
    provider = FixtureProvider(candidates=[_candidate("invalid"), _candidate("challenge")],
        downloads={"invalid": (b"not subtitle contents", "Example.2020.srt"), "challenge": ProviderError("user_action_required", "captcha")})
    choices, report = retrieve(provider)
    assert not choices and report["status"] == "no_match"
    assert report["providers"][0]["status"] == "ok"
    assert report["providers"][0]["download_failures"] == ["user_action_required"]


def test_malformed_result_isolated_and_all_inaccessible_sources_are_unavailable():
    class BrokenProvider:
        provider = "broken-name"
        def search(self, query):
            return None
    choices, report = retrieve(BrokenProvider())
    assert not choices and report["status"] == "unavailable"
    assert report["providers"] == [{"provider": "broken-name", "status": "invalid_response", "count": 0}]


def test_rejected_identity_and_member_are_reported():
    provider = FixtureProvider(candidates=[replace(_candidate("wrong"), year=2021), _candidate("right")],
        downloads={"right": replace(make_srt(), filename="Other.Movie.2020.srt")})
    choices, report = retrieve(provider)
    assert not choices
    assert {r["reason"] for r in report["rejections"]} == {"year_mismatch", "document_title_mismatch"}
    assert ("download", "wrong") not in provider.requests


def test_identical_body_can_still_choose_better_release_evidence():
    doc = make_srt()
    provider = FixtureProvider(candidates=[_candidate("generic"), _candidate("exact", release_name="Example.2020.WEB-DL")], downloads={"generic": doc, "exact": doc})
    choices, _ = retrieve(provider, MediaQuery("Example", year=2020, original_language="en", release_name="Example.2020.WEB-DL.mkv"))
    assert choices[0].candidate.candidate_id == "exact"


def test_unapplied_provider_timing_offset_rejected_before_download():
    provider = FixtureProvider(candidates=[_candidate(metadata={"delay_ms": 500})], downloads={"1": make_srt()})
    choices, report = retrieve(provider)
    assert not choices and report["rejections"][0]["reason"] == "timing_offset_unapplied"
    assert not any(request[0] == "download" for request in provider.requests)


def test_real_assrt_adapter_and_archive_quality_pipeline_with_fake_http():
    body = _zip(make_ass(bilingual=True), make_srt())
    page = '''<div class="subitem"><a class="introtitle" title="Example.2020" href="/xml/sub/123/123456.xml">Example</a>
    <span>格式：ASS 语言：简 双语</span><a href="#" onclick="location.href='/download/123456/Example.2020.zip';return false;">下载</a></div>'''
    class HttpFixture:
        def __init__(self): self.calls = []
        def request(self, url, **kwargs):
            self.calls.append(url)
            return HttpResponse(body if "/download/" in url else page.encode())
    transport = HttpFixture()
    choices, report = retrieve(AssrtProvider(transport=transport))
    assert report["status"] == "matched" and len(transport.calls) == 2
    assert choices[0].document.format == "ass" and choices[0].quality.bilingual


def test_extraction_concurrency_is_bounded(monkeypatch):
    import telepiplex_caption.engine as engine_module
    original = engine_module.extract_subtitles
    lock, state = threading.Lock(), {"active": 0, "peak": 0}
    def delayed(*args, **kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(0.025)
            return original(*args, **kwargs)
        finally:
            with lock: state["active"] -= 1
    monkeypatch.setattr(engine_module, "extract_subtitles", delayed)
    provider = FixtureProvider(candidates=[_candidate(str(i)) for i in range(7)], downloads={str(i): make_srt() for i in range(7)})
    choices, _ = retrieve(provider)
    assert choices and state["peak"] == 2


def test_selection_memory_budget_is_explicit():
    provider = FixtureProvider(candidates=[_candidate()], downloads={"1": make_srt()})
    engine = CaptionEngine({}, providers=[provider])
    engine._selection_budget = 1
    choices, report = asyncio.run(engine.retrieve(MediaQuery("Example", original_language="en")))
    assert not choices and report["selection_budget_reached"]
    assert report["warnings"] == ["selection_memory_limit"]


def test_concurrent_retrievals_share_network_work_and_short_lived_urls():
    class Delayed(FixtureProvider):
        def search(self, query):
            time.sleep(0.02)
            return super().search(query)
        def download(self, candidate):
            time.sleep(0.02)
            return super().download(candidate)
    provider = Delayed(candidates=[_candidate(download_url="https://fixture.invalid/a?expires=1")], downloads={"1": make_srt()})
    engine = CaptionEngine({}, providers=[provider])
    async def run():
        query = MediaQuery("Example", year=2020, original_language="en")
        results = await asyncio.gather(*(engine.retrieve(query) for _ in range(20)))
        assert all(len(choices) == 1 for choices, _ in results)
        engine._search_cache.clear()
        provider.candidates = (replace(provider.candidates[0], download_url="https://fixture.invalid/a?expires=2"),)
        assert (await engine.retrieve(query))[0]
    asyncio.run(run())
    assert sum(r[0] == "search" for r in provider.requests) == 2
    assert sum(r[0] == "download" for r in provider.requests) == 1


def test_repeated_timeouts_do_not_release_still_running_network_workers():
    lock, counts = threading.Lock(), {"active": 0, "peak": 0, "calls": 0}
    class Slow:
        name = "slow"
        def search(self, query):
            with lock:
                counts["active"] += 1
                counts["calls"] += 1
                counts["peak"] = max(counts["peak"], counts["active"])
            try:
                time.sleep(0.15)
                return ProviderResult(self.name, "no_results")
            finally:
                with lock: counts["active"] -= 1
    engine = CaptionEngine({"provider_timeout_seconds": 0.025}, providers=[Slow()])
    async def run():
        results = await asyncio.gather(*(engine.retrieve(MediaQuery(f"Work {i}", original_language="en")) for i in range(30)))
        assert all(report["status"] == "unavailable" for _, report in results)
        assert counts["active"] == 3
        await asyncio.sleep(0.18)
        assert not engine._workers and not engine._search_tasks
    asyncio.run(run())
    assert counts == {"active": 0, "peak": 3, "calls": 3}


def test_cancelling_one_waiter_does_not_cancel_another_shared_query():
    class Delayed(FixtureProvider):
        def search(self, query):
            time.sleep(0.04)
            return super().search(query)
    provider = Delayed(candidates=[_candidate()], downloads={"1": make_srt()})
    engine = CaptionEngine({}, providers=[provider])
    async def run():
        query = MediaQuery("Example", year=2020, original_language="en")
        first = asyncio.create_task(engine.retrieve(query))
        second = asyncio.create_task(engine.retrieve(query))
        await asyncio.sleep(0.01)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert (await second)[0]
        assert not engine._search_tasks and not engine._download_tasks
    asyncio.run(run())
    assert sum(r[0] == "search" for r in provider.requests) == 1


def test_free_quota_backoff_preserves_status_without_repeated_source_calls():
    provider = FixtureProvider(status="rate_limited")
    engine = CaptionEngine({}, providers=[provider])
    async def run():
        for title in ("Example", "Different", "Another"):
            selected, report = await engine.retrieve(MediaQuery(title, original_language="en"))
            assert not selected and report["providers"][0]["status"] == "rate_limited"
        assert len(provider.requests) == 1
        engine._cooldowns[id(provider)] = (time.monotonic() - 1, ProviderResult(provider.name, "rate_limited"))
        await engine.retrieve(MediaQuery("Example", original_language="en"))
        assert len(provider.requests) == 2
    asyncio.run(run())


def test_direct_source_still_matches_real_detail_identity():
    class Detail(FixtureProvider):
        def supports_detail_url(self, url):
            return url == "https://fixture.invalid/subs/1.html"
        def lookup_detail(self, url):
            self.requests.append(("detail", url))
            return ProviderResult(self.name, "ok", self.candidates)
    provider = Detail(candidates=[replace(_candidate(), title="Unrelated movie", year=1998)], downloads={"1": make_srt()})
    engine = CaptionEngine({}, providers=[provider])
    choices, report = asyncio.run(engine.retrieve(MediaQuery("Example", year=2020, original_language="en"),
        source_url="https://fixture.invalid/subs/1.html"))
    assert not choices and report["rejections"]
    assert [r[0] for r in provider.requests] == ["detail"]


def test_all_eighteen_sources_get_a_turn_despite_queue_wait():
    started = []
    class SlowEmpty(FixtureProvider):
        def search(self, query):
            started.append(self.name)
            time.sleep(0.04)
            return ProviderResult(self.name, "no_results")
    providers = [SlowEmpty(str(i)) for i in range(18)]
    engine = CaptionEngine({"provider_timeout_seconds": 0.12}, providers=providers)
    _, report = asyncio.run(engine.retrieve(MediaQuery("Example", original_language="en")))
    assert len(started) == 18
    assert all(item["status"] == "no_results" for item in report["providers"])


def test_retry_after_timeout_reuses_still_running_source_worker():
    state = {"started": 0}
    class Slow(FixtureProvider):
        def search(self, query):
            state["started"] += 1
            time.sleep(0.15)
            return ProviderResult(self.name, "no_results")
    engine = CaptionEngine({"provider_timeout_seconds": 0.03}, providers=[Slow()])
    async def run():
        query = MediaQuery("Example", original_language="en")
        for _ in range(2):
            _, report = await engine.retrieve(query)
            assert report["providers"][0]["status"] == "timeout"
        assert state["started"] == 1
        await asyncio.sleep(0.17)
        assert not engine._workers and not engine._active_work
    asyncio.run(run())


def test_retrieval_deadline_keeps_checked_winner_and_cancels_pending_work():
    class SlowDownloads(FixtureProvider):
        def download(self, candidate):
            if candidate.candidate_id != "first":
                time.sleep(0.18)
            return super().download(candidate)
    names = ["first", *[str(i) for i in range(10)]]
    provider = SlowDownloads(candidates=[_candidate(name) for name in names],
                             downloads={name: make_srt() for name in names})
    engine = CaptionEngine({"provider_timeout_seconds": 1}, providers=[provider])
    async def run():
        choices, report = await engine.retrieve(MediaQuery("Example", original_language="en"), timeout_seconds=0.09)
        assert choices and choices[0].candidate.candidate_id == "first"
        assert report["retrieval_budget_reached"]
        assert "retrieval_budget_reached" in report["warnings"]
        await asyncio.sleep(0.22)
        assert not engine._workers and not engine._active_work
        assert len([request for request in provider.requests if request[0] == "download"]) <= 3
    asyncio.run(run())


def test_explicit_catalog_mapping_applies_episode_offset_to_whole_pack():
    document = replace(make_srt(), filename="Example EP13.srt")
    provider = FixtureProvider(candidates=[_candidate(season=2,
        metadata={"episode_mapping": {"season": 2, "offset": -12}, "document_aliases": ["Example"]})],
        downloads={"1": (_zip(document), "Example.zip")})
    choices, _ = retrieve(provider, MediaQuery("Example", year=2020, original_language="ja", media_type="series", season=2))
    assert [(x.query.season, x.query.episode) for x in choices] == [(2, 1)]


def test_deadline_retains_already_checked_members_of_one_season_archive(monkeypatch):
    import telepiplex_caption.engine as engine_module
    original = engine_module.inspect_subtitle
    def slow(document, query):
        time.sleep(0.025)
        return original(document, query)
    monkeypatch.setattr(engine_module, "inspect_subtitle", slow)
    documents = [replace(make_srt(), filename=f"Example.S01E{i:02d}.srt") for i in range(1, 13)]
    provider = FixtureProvider(candidates=[_candidate(season=1)], downloads={"1": (_zip(*documents), "Example.zip")})
    engine = CaptionEngine({}, providers=[provider])
    async def run():
        selected, report = await engine.retrieve(MediaQuery("Example", original_language="zh", media_type="series", season=1), timeout_seconds=0.1)
        assert 1 <= len(selected) < 12
        assert report["status"] == "matched" and report["retrieval_budget_reached"]
        assert all(item.quality.accepted for item in selected)
        await asyncio.sleep(0.1)
        assert not engine._workers
    asyncio.run(run())
