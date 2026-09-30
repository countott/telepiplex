from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from telepiplex_caption.engine import CaptionEngine, Selection
from telepiplex_caption.models import MediaQuery, QualityReport, SubtitleCandidate, SubtitleDocument
from telepiplex_caption.service import CaptionFeature, CHUNK_BYTES
from telepiplex_plugin_sdk import FeatureError, FeatureRuntime
from telepiplex_plugin_sdk.media_metadata_v2 import build_media_metadata_v2_id


def metadata(*, series=False):
    result = {"schema_version": 2, "confirmed": True,
              "identity": {"primary_ref": {"provider": "tmdb_tv" if series else "tmdb_movie", "id": "42"},
                           "provider_refs": {"tmdb_tv" if series else "tmdb_movie": "42"},
                           "media_type": "series" if series else "movie", "title_zh": "测试作品", "title_en": "Test Work",
                           "title_original": "Test Work", "year": 2020},
              "scope": {"kind": "season" if series else "movie", "season_number": 1 if series else None, "episode_number": None},
              "placement": {"category_kind": "live_action_series" if series else "live_action_movie"}}
    result["metadata_id"] = build_media_metadata_v2_id(result)
    return result


class StubEngine:
    providers = []

    def __init__(self, *, content="字幕内容", report=None):
        self.content = content
        self.queries = []
        self.report = report

    async def retrieve(self, query, **kwargs):
        self.queries.append(query)
        if self.report is not None:
            return [], deepcopy(self.report)
        quality = QualityReport(True, language="chi", cue_count=25, format="srt", normalized_text=self.content)
        return [Selection(query, SubtitleCandidate("test", "one", query.title), SubtitleDocument("fixture.srt", "srt", self.content.encode()), quality, (300,))], {"status": "matched", "providers": []}


class Host:
    def __init__(self):
        self.calls, self.reports, self.chunks = [], [], []
        self.resolved = {"status": "resolved", "media_metadata": metadata(), "subtitle_context": {"original_language": "en"}}
        self.pages = [{"snapshot_complete": True, "total": 1, "media": [{"path": "/Movies/Test Work 2020.mkv", "name": "Test Work 2020.mkv", "is_dir": False}], "next_cursor": ""}]
        self.placement_status = "placed"

    async def report_operation(self, report):
        self.reports.append(deepcopy(report))
        return {"accepted": True}

    async def call_capability(self, capability, method, payload, **kwargs):
        self.calls.append((capability, method, deepcopy(payload)))
        if capability == "media.search":
            return deepcopy(self.resolved)
        if method == "scan_library":
            if not payload.get("root_path"):
                return {"roots": [{"name": "电影", "path": "/Movies"}]}
            return self.pages.pop(0)
        if method == "place_subtitle":
            self.chunks.append(base64.b64decode(payload["chunk_base64"], validate=True))
            if payload["chunk_index"] < payload["chunk_count"] - 1:
                return {"status": "uploading", "next_chunk_index": payload["chunk_index"] + 1}
            return {"status": self.placement_status, "path": "/字幕/Test Work.chi.srt"}
        raise AssertionError((capability, method))


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.host, self.engine = Host(), StubEngine()
        self.feature = CaptionFeature(config={}, host=self.host, state_path=Path(self.tmp.name), engine=self.engine)
        self.runtime = FeatureRuntime({}, "token")
        self.feature.bind_runtime(self.runtime)
        self.owner = {"chat_id": 10, "user_id": 1}

    async def asyncTearDown(self):
        tasks = [op.get("task") for op in self.feature.operations.values() if op.get("task")]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tmp.cleanup()

    async def run_command(self, args):
        result = await self.feature.command({**self.owner, "command": "caption", "args": args})
        operation = self.feature.operations[result["operation"]["operation_id"]]
        await operation["task"]
        return operation

    async def test_query_resolves_metadata_and_places_without_video(self):
        operation = await self.run_command(["Test", "Work", "2020"])
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(operation["result"]["added_count"], 1)
        self.assertEqual(self.engine.queries[0].original_language, "en")
        placement = next(c[2] for c in self.host.calls if c[1] == "place_subtitle")
        self.assertEqual(placement["video_path"], "")
        self.assertEqual(placement["language"], "chi")
        self.assertEqual(self.host.calls[0][0], "media.search")
        self.assertFalse(any(c[0] == "download.provider" for c in self.host.calls))
        self.assertIn("未验证", operation["status_text"])

    async def test_large_subtitle_transferred_in_bounded_chunks(self):
        self.engine.content = "字幕内容" * 100000
        result = await self.feature._process(metadata(), {"original_language": "en"})
        self.assertEqual(result["status"], "completed")
        self.assertTrue(all(len(chunk) <= CHUNK_BYTES for chunk in self.host.chunks))
        self.assertEqual(b"".join(self.host.chunks), self.engine.content.encode())
        self.assertGreater(len(self.host.chunks), 4)

    async def test_changed_destination_has_distinct_durable_transfer(self):
        selections, _ = await self.engine.retrieve(MediaQuery(title="Test Work", original_language="en"))
        original = selections[0]
        await self.feature._place(metadata(), original)
        self.feature.config["output_path"] = "/字幕B"
        await self.feature._place(metadata(), original)
        traditional = replace(original, quality=replace(original.quality, language="cht"))
        await self.feature._place(metadata(), traditional)
        ass = replace(traditional, document=replace(traditional.document, format="ass"))
        await self.feature._place(metadata(), ass)
        ids = [payload["transfer_id"] for _, method, payload in self.host.calls if method == "place_subtitle"]
        self.assertEqual(len(set(ids)), 4)

    async def test_corrected_standalone_naming_metadata_has_new_transfer_identity(self):
        selections, _ = await self.engine.retrieve(MediaQuery(title="Test Work", original_language="en"))
        original_metadata = metadata()
        corrected_metadata = deepcopy(original_metadata)
        corrected_metadata["identity"]["title_en"] = "Test Work Corrected"
        corrected_metadata["identity"]["year"] = 2021
        self.assertEqual(original_metadata["metadata_id"], corrected_metadata["metadata_id"])
        await self.feature._place(original_metadata, selections[0])
        await self.feature._place(corrected_metadata, selections[0])
        ids = [payload["transfer_id"] for _, method, payload in self.host.calls if method == "place_subtitle"]
        self.assertNotEqual(*ids)

    async def test_interrupted_transfer_resumes_from_first_missing_chunk(self):
        self.engine.content = "字幕内容" * 100000
        received = {}
        original_call = self.host.call_capability
        interrupted = False
        async def durable_call(capability, method, payload, **kwargs):
            nonlocal interrupted
            if method != "place_subtitle":
                return await original_call(capability, method, payload, **kwargs)
            index = payload["chunk_index"]
            received[index] = base64.b64decode(payload["chunk_base64"])
            if index == 1 and not interrupted:
                interrupted = True
                raise asyncio.CancelledError()
            missing = next((n for n in range(payload["chunk_count"]) if n not in received), None)
            if missing is None:
                return {"status": "placed", "path": "/字幕/Test Work.chi.srt"}
            return {"status": "uploading", "next_chunk_index": missing}
        self.host.call_capability = durable_call
        with self.assertRaises(asyncio.CancelledError):
            await self.feature._process(metadata(), {"original_language": "en"})
        self.assertEqual(set(received), {0, 1})
        result = await self.feature._process(metadata(), {"original_language": "en"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(b"".join(received[n] for n in sorted(received)), self.engine.content.encode())

    async def test_invalid_resume_acknowledgment_is_rejected(self):
        self.engine.content = "字幕内容" * 100000
        for bad_index in (0, -1, True, 1000):
            async def invalid_call(*args, **kwargs):
                return {"status": "uploading", "next_chunk_index": bad_index}
            self.host.call_capability = invalid_call
            result = await self.feature._process(metadata(), {"original_language": "en"})
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["placements"][0]["reason"], "upload_unverified")

    async def test_prepare_download_uses_confirmed_context_and_existing_receipt(self):
        self.host.placement_status = "exists"
        response = await self.feature.capability({"method": "prepare_download", "payload": {
            "metadata": {"media_metadata": metadata()}, "subtitle_context": {"original_language": "zh"},
            "file_tree": [{"path": "/Downloads/Test.mkv", "name": "Test.mkv", "is_dir": False}],
        }})
        self.assertEqual(response["added_count"], 0)
        self.assertEqual(response["existing_count"], 1)
        self.assertFalse(any(c[0] == "media.search" for c in self.host.calls))
        self.assertEqual(self.engine.queries[0].video_path, "/Downloads/Test.mkv")

    async def test_unknown_language_never_guessed_from_english_title(self):
        self.host.resolved["subtitle_context"] = {}
        operation = await self.run_command(["Test", "Work"])
        self.assertEqual(operation["state"], "awaiting_input")
        self.assertEqual(self.engine.queries, [])
        callback = f"language:{operation['operation_id'][:12]}:zh"
        await self.feature.callback({**self.owner, "payload": callback})
        await operation["task"]
        self.assertEqual(self.engine.queries[0].original_language, "zh")
        again = await self.feature.callback({**self.owner, "payload": callback})
        self.assertIn("失效", again["actions"][0]["text"])
        self.assertEqual(len(self.engine.queries), 1)

    async def test_ambiguous_identity_requires_owned_choice(self):
        self.host.resolved = {"status": "confirmation_required", "resolution_id": "resolved-1", "candidates": [{"ref": "tmdb:42", "title": "测试作品", "year": 2020}]}
        operation = await self.run_command(["Test", "Work"])
        self.assertEqual(operation["state"], "awaiting_input")
        choice = f"choose:{operation['operation_id'][:12]}:0"
        denied = await self.feature.callback({"chat_id": 11, "user_id": 2, "payload": choice})
        self.assertIn("失效", denied["actions"][0]["text"])
        self.host.resolved = {"status": "resolved", "media_metadata": metadata(), "subtitle_context": {"original_language": "en"}}
        await self.feature.callback({**self.owner, "payload": choice})
        await operation["task"]
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(next(c[2] for c in self.host.calls if c[1] == "confirm_metadata"), {"resolution_id": "resolved-1", "candidate_ref": "tmdb:42"})

    async def test_library_consumes_all_pages_before_slow_matching(self):
        self.host.pages = [{"snapshot_complete": True, "total": 2, "media": [{"path": "/Movies/Test Work 2020.mkv", "name": "Test Work 2020.mkv"}], "next_cursor": "page2"},
                           {"snapshot_complete": True, "total": 2, "media": [{"path": "/Movies/Test Work 2020.mp4", "name": "Test Work 2020.mp4"}], "next_cursor": ""}]
        operation = await self.run_command(["scan", "/Movies"])
        self.assertEqual(operation["result"]["processed_count"], 2)
        self.assertEqual([c[1] for c in self.host.calls[:2]], ["scan_library", "scan_library"])

    async def test_operational_failure_visible_and_not_reported_as_no_match(self):
        self.engine.report = {"status": "unavailable", "providers": [{"provider": "opensubtitles", "status": "auth_required"}], "rejections": []}
        operation = await self.run_command(["Test"])
        self.assertEqual(operation["state"], "failed")
        self.assertIn("待补充授权", operation["status_text"])
        self.assertNotIn("未找到通过", operation["status_text"])

    async def test_config_secrets_not_reflected_and_invalid_paths_not_written(self):
        await self.feature.command({**self.owner, "command": "caption_config"})
        await self.feature.callback({**self.owner, "payload": "config:assrt"})
        response = await self.feature.message({**self.owner, "text": "secret-fixture"})
        self.assertEqual(response["config_patch"], {"providers": {"assrt": {"token": "secret-fixture"}}})
        self.assertEqual(response["actions"], [])
        await self.feature.command({**self.owner, "command": "caption_config"})
        await self.feature.callback({**self.owner, "payload": "config:output_path"})
        invalid = await self.feature.message({**self.owner, "text": "/a/../b"})
        self.assertNotIn("config_patch", invalid)

    async def test_free_provider_keys_are_configurable_and_never_echoed(self):
        for provider in ("subdl", "subsource"):
            await self.feature.command({**self.owner, "command": "caption_config"})
            await self.feature.callback({**self.owner, "payload": "config:" + provider})
            response = await self.feature.message({**self.owner, "text": "free-personal-test-key"})
            self.assertEqual(response["config_patch"], {"providers": {provider: {"api_key": "free-personal-test-key"}}})
            self.assertEqual(response["actions"], [])
            await self.feature.command({**self.owner, "command": "caption_config"})
            await self.feature.callback({**self.owner, "payload": "config:" + provider})
            response = await self.feature.message({**self.owner, "text": "clear"})
            self.assertEqual(response["config_patch"], {"providers": {provider: {"api_key": ""}}})

    async def test_source_url_is_not_sent_to_metadata_search_and_is_retained(self):
        class DirectProvider:
            name = "subhd"
            def supports_detail_url(self, url):
                return url == "https://subhd.tv/a/Example"
        self.engine.providers = [DirectProvider()]
        seen = []
        original = self.engine.retrieve
        async def retrieve(query, *, source_url=""):
            seen.append(source_url)
            return await original(query)
        self.engine.retrieve = retrieve
        operation = await self.run_command(["Test Work 2020 --source https://subhd.tv/a/Example"])
        self.assertEqual(operation["state"], "completed")
        self.assertEqual(seen, ["https://subhd.tv/a/Example"])
        search_call = next(c for c in self.host.calls if c[0] == "media.search")
        self.assertEqual(search_call[2]["query"], "Test Work 2020")
        self.assertEqual(self.feature.store.get(operation["operation_id"])["source_url"], seen[0])

    async def test_unknown_source_url_does_not_start_operation(self):
        response = await self.feature.command({**self.owner, "command": "caption",
            "args": ["Test --source https://other.invalid/private?token=secret"]})
        self.assertNotIn("operation", response)
        self.assertNotIn("secret", str(response))

    async def test_cancel_before_write_keeps_existing_outputs(self):
        started = asyncio.Event()
        async def slow(query):
            started.set()
            await asyncio.Event().wait()
        self.engine.retrieve = slow
        result = await self.feature.command({**self.owner, "command": "caption", "args": ["Test"]})
        operation = self.feature.operations[result["operation"]["operation_id"]]
        await started.wait()
        await self.feature.operation_control({"operation_id": operation["operation_id"], "action": "cancel"})
        await operation["task"]
        self.assertEqual(operation["state"], "cancelled")
        self.assertFalse(any(c[1] == "place_subtitle" for c in self.host.calls))

    async def test_restart_snapshot_marks_inflight_interrupted(self):
        self.feature.store.save({"operation_id": "old", **self.owner, "state": "running", "stage": "caption", "status_text": "working", "control": "cancel", "revision": 1, "details": {}})
        snapshot = await self.feature.operation_snapshot({"operation_id": "old"})
        self.assertEqual(snapshot["operation"]["state"], "interrupted")

    async def test_auto_disabled_has_no_network_or_storage_calls(self):
        self.feature.config["automatic"] = False
        result = await self.feature.capability({"method": "prepare_download", "payload": {}})
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(self.host.calls, [])

    async def test_automatic_metadata_recovery_consumes_the_same_total_budget(self):
        self.feature.config["automatic_timeout_seconds"] = 0.1
        self.feature.engine = CaptionEngine({}, providers=[])
        cancelled = asyncio.Event()
        async def slow_resolve(query):
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.set()
        self.feature._resolve = slow_resolve
        result = await asyncio.wait_for(self.feature.prepare_download({"media_metadata": metadata(),
            "file_tree": [{"path": "/Movie/Test Work 2020.mkv", "name": "Test Work 2020.mkv", "is_dir": False}]}), 0.2)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(result["status"], "metadata_required")
        self.assertFalse(self.host.chunks)

    async def test_automatic_retrieval_reserves_time_for_rename_placement(self):
        captured = []
        original = self.engine.retrieve
        async def retrieve(query, **kwargs):
            captured.append(kwargs["timeout_seconds"])
            return await original(query, **kwargs)
        self.engine.retrieve = retrieve
        self.feature.config.update(automatic_timeout_seconds=100, retrieval_timeout_seconds=180)
        result = await self.feature.prepare_download({"media_metadata": metadata(),
            "subtitle_context": {"original_language": "en"},
            "file_tree": [{"path": "/Movie/Test Work 2020.mkv", "name": "Test Work 2020.mkv", "is_dir": False}]})
        self.assertEqual(result["added_count"], 1)
        self.assertTrue(0 < captured[0] <= 80)
