"""Real Caption/Download/Rename composition across Host Unix-socket RPC.

Only metadata lookup, subtitle transport, and the final cloud I/O are fixtures.
Archive extraction, quality inspection, capability permissions, chunk transfer,
Rename planning, storage identity checks, and operation rendering run normally.
"""
import asyncio
import base64
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from tests import test_operation_pipeline_e2e as host_fixture
from tests.business_flow_storage import Memory115

ROOT = Path(__file__).resolve().parents[1]


class SubtitleMemory115(Memory115):
    """Fake cloud upload transport with real bytes, identities and SHA1."""
    def __init__(self):
        super().__init__()
        self.subtitle_contents = {}

    def _remove_cached_file(self, path):
        pass

    @staticmethod
    def _item_sha1(item):
        return str(item.get("sha1") or "")

    def upload_subtitle(self, path, content):
        from telepiplex_plugin_sdk import FeatureError
        with self._lock:
            digest = hashlib.sha1(content).hexdigest()
            existing = self.nodes.get(path)
            if existing:
                if existing["sha1"] != digest:
                    raise FeatureError("target_conflict", "existing subtitle preserved")
                return {"status": "exists", "path": path, "sha1": digest, "file_id": existing["file_id"]}
            self.create_dir_recursive(str(PurePosixPath(path).parent))
            file_id = f"subtitle-{len(self.subtitle_contents) + 1}"
            self._put(path, directory=False, file_id=file_id)
            self.nodes[path].update(size=len(content), sha1=digest)
            self.subtitle_contents[file_id] = bytes(content)
            self.writes.append(("upload", path, digest))
            return {"status": "placed", "path": path, "sha1": digest, "file_id": file_id}


def _metadata():
    from telepiplex_plugin_sdk.media_metadata_v2 import build_media_metadata_v2_id
    value = {
        "schema_version": 2, "confirmed": True,
        "identity": {"primary_ref": {"provider": "tmdb_movie", "id": "275"},
                     "provider_refs": {"tmdb_movie": "275"}, "media_type": "movie",
                     "title_zh": "冰血暴", "title_en": "Fargo", "title_original": "Fargo", "year": 1996},
        "scope": {"kind": "movie", "season_number": None, "episode_number": None},
        "placement": {"category_kind": "live_action_movie"},
    }
    value["metadata_id"] = build_media_metadata_v2_id(value)
    return value


def _subtitle(language, extension, *, large=False):
    # Synthetic unique dialogue, not a copyrighted film subtitle.
    chinese = "这是我们的测试字幕，他们已经来到这里，说话时间会随着变化。"
    if language == "cht":
        chinese = "這是我們的測試字幕，他們已經來到這裡，說話時間會隨著變化。"
    if large:
        chinese *= 120
    lines = []
    if extension == "ass":
        lines = ["[Script Info]", "ScriptType: v4.00+", "[Events]",
                 "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for index in range(25):
        start, end = index * 3, index * 3 + 2
        text = f"{chinese} 第{index}条。"
        if extension == "ass":
            text += rf"\NThis is synthetic dialogue number {index}."
            lines.append(f"Dialogue: 0,0:{start // 60:02}:{start % 60:02}.00,0:{end // 60:02}:{end % 60:02}.00,Default,,0,0,0,,{text}")
        else:
            lines.append(f"{index + 1}\n00:{start // 60:02}:{start % 60:02},000 --> 00:{end // 60:02}:{end % 60:02},000\n{text}\n")
    return "\n".join(lines).encode()


@pytest.mark.parametrize("language,extension", [("chi", "ass"), ("cht", "srt")])
def test_caption_download_then_rename_and_standalone_across_host_rpc(tmp_path, monkeypatch, language, extension):
    for name in ("caption", "download", "rename"):
        monkeypatch.syspath_prepend(str(ROOT / "features" / name / "src"))

    async def exercise():
        from app.handlers import interaction_handler
        from app.runtime.plugin_manifest import PluginManifest
        from telepiplex_plugin_sdk.host_client import HostClient
        from telepiplex_caption.engine import CaptionEngine
        from telepiplex_caption.models import SubtitleCandidate
        from telepiplex_caption.providers import ProviderResult
        from telepiplex_caption.service import CaptionFeature
        from telepiplex_download.service import DownloadFeature
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_rename.service import RenameFeature
        from telepiplex_rename.jobs import RenameJobStore
        from telepiplex_rename.context import runtime_context as rename_context

        expected_content = _subtitle(language, extension, large=extension == "ass")
        class SyntheticProvider:
            # Exercise ASSRT's visible credit policy with synthetic transport.
            name = "assrt" if language == "chi" else "synthetic_fixture"
            def __init__(self):
                self.queries, self.downloads = [], []
            def search(self, query):
                self.queries.append(query)
                return ProviderResult(self.name, "ok", (SubtitleCandidate(
                    self.name, "synthetic-one", "Fargo", year=1996, media_type="movie",
                    # Labels deliberately disagree with the actual file body.
                    language="cht" if language == "chi" else "chi", subtitle_format=extension,
                    release_name="Fargo.1996.1080p", detail_url="https://assrt.net/xml/sub/42/fixture.html" if self.name == "assrt" else "https://fixture.invalid/subtitle",
                ),))
            def download(self, candidate):
                self.downloads.append(candidate.candidate_id)
                return expected_content, f"Fargo.1996.CHS.{extension}"
        provider = SyntheticProvider()
        harness = host_fixture.OperationPipelineEndToEndTest()
        await harness.asyncSetUp()
        storage, features, clients = SubtitleMemory115(), {}, {}
        names = ("search", "download", "rename", "caption")
        manifests = {name: PluginManifest.from_mapping(yaml.safe_load((ROOT / "features" / name / "manifest.yaml").read_text())) for name in names}
        hosts = {name: HostClient(harness.broker.socket_path, name + "-token") for name in names}
        contract = _metadata()
        metadata_calls = []
        async def resolve(request):
            assert request["method"] == "resolve_metadata"
            metadata_calls.append(deepcopy(request["payload"]))
            return {"status": "resolved", "media_metadata": deepcopy(contract), "subtitle_context": {"original_language": "en", "imdb_id": "tt0116282"}}

        message_ids = iter(range(300, 2000))
        async def send(**kwargs):
            return SimpleNamespace(message_id=next(message_ids))
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=send), send_photo=AsyncMock(side_effect=send),
            edit_message_text=AsyncMock(), edit_message_caption=AsyncMock(), edit_message_media=AsyncMock(),
            edit_message_reply_markup=AsyncMock(), delete_message=AsyncMock())
        app = SimpleNamespace(bot=bot, bot_data={interaction_handler.COORDINATOR_KEY: harness.coordinator,
                                               interaction_handler.ROUTER_KEY: harness.router})
        harness.operation_sink.attach(lambda record: interaction_handler.render_operation(app, harness.router, record))
        config = yaml.safe_load((ROOT / "features/rename/config.default.yaml").read_text())
        config.update(ai={"enable": False}, caption_timeout=20)
        rename_context.configure({**config, "media": {"unorganized_path": "/未整理"}})
        features["download"] = DownloadFeature(config={"poll_interval": .01}, host=hosts["download"], client=storage,
                                              jobs=DownloadJobStore(tmp_path / "download.db"))
        features["rename"] = RenameFeature(config=config, host=hosts["rename"], jobs=RenameJobStore(tmp_path / "rename.db"))
        features["caption"] = CaptionFeature(config={"output_path": "/字幕", "provider_timeout_seconds": 5},
            host=hosts["caption"], state_path=tmp_path / "caption", engine=CaptionEngine({"provider_timeout_seconds": 5}, providers=[provider]))
        handlers = {
            "search": {"capabilities": {"media.search": resolve}},
            "download": {"capabilities": {"download.provider": features["download"].download_capability,
                                           "storage.provider": features["download"].storage_capability}},
            "rename": {"capabilities": {"media.rename": features["rename"].rename_capability},
                       "events": {"download.completed": features["rename"].download_completed}},
            "caption": {"capabilities": {"subtitle.caption": features["caption"].capability},
                        "commands": {"caption": features["caption"].command},
                        "callbacks": {"caption": features["caption"].callback}},
        }
        frames, upload_chunks = [], []
        write = asyncio.StreamWriter.write
        def measure(writer, data):
            frames.append(len(data))
            return write(writer, data)
        original_storage = features["download"].storage_capability
        async def measure_storage(request):
            if request["method"] == "upload_subtitle_chunk":
                upload_chunks.append(deepcopy(request["payload"]))
            return await original_storage(request)
        handlers["download"]["capabilities"]["storage.provider"] = measure_storage

        async def drain(name):
            async with asyncio.timeout(30):
                while features[name].runtime._background_tasks:
                    await asyncio.gather(*tuple(features[name].runtime._background_tasks.values()))
            await harness.operation_sink.drain()

        try:
            with patch("telepiplex_download.subtitle_upload.upload", side_effect=lambda client, path, content: client.upload_subtitle(path, content)), \
                 patch.object(interaction_handler, "_segment_photo_media", AsyncMock(return_value=BytesIO(b"fixture"))), \
                 patch.object(asyncio.StreamWriter, "write", measure):
                for name in ("download", "search", "rename", "caption"):
                    clients[name] = await harness._start_runtime(manifests[name], name + "-token", **handlers[name])
                    if name in features:
                        features[name].bind_runtime(harness.runtimes[-1])
                submitted = await hosts["search"].call_capability("download.provider", "submit", {
                    "link": "magnet:?xt=urn:btih:" + "b" * 40,
                    "selected_path": "/Movies", "chat_id": 10, "user_id": 1,
                    "media_metadata": contract,
                }, deadline=20, idempotency_key="caption-e2e-download")
                await drain("download")
                async with asyncio.timeout(30):
                    while not features["rename"].jobs.get("caption-e2e-download") or harness.coordinator.get(submitted["operation_id"]).state not in {"completed", "failed", "cancelled"}:
                        await asyncio.sleep(.01)
                await drain("rename")
                renamed = features["rename"].jobs.get("caption-e2e-download")
                assert renamed["state"] == "completed", renamed
                canonical = f"/Movies/冰血暴 (1996) ⋯ Fargo/Fargo.{language}.{extension}"
                assert canonical in storage.nodes, sorted(storage.nodes)
                assert storage.nodes[canonical]["sha1"] == hashlib.sha1(expected_content).hexdigest()
                assert storage.subtitle_contents[storage.nodes[canonical]["file_id"]] == expected_content
                assert "/Movies/冰血暴 (1996) ⋯ Fargo/Fargo.mkv" in storage.nodes
                assert next(write[0] for write in storage.writes if write[0] in {"upload", "rename", "move"}) == "upload", storage.writes
                assert renamed["result"]["event_payload"]["caption_result"]["added_count"] == 1
                assert renamed["result"]["event_payload"]["media_metadata"] == contract
                if provider.name == "assrt":
                    credit = "字幕服务由 assrt.net 提供"
                    source_page = "https://assrt.net/xml/sub/42/fixture.html"
                    terminal = renamed["result"]["terminal_operation_report"]["status_text"]
                    assert credit in terminal and source_page in terminal
                    assert any(credit in str(call.kwargs.get("text", "")) for call in bot.edit_message_text.call_args_list)
                assert provider.queries[0].video_path.endswith("Fargo.1996.1080p.mkv")
                assert provider.queries[0].original_language == "en"
                assert all(len(base64.b64decode(chunk["chunk_base64"])) <= 192 * 1024 for chunk in upload_chunks)
                if extension == "ass":
                    assert len(upload_chunks) > 1
                writes_before_replay = list(storage.writes)
                completed = features["download"].jobs.get("caption-e2e-download")["result"]
                replay = await clients["rename"].request("event.deliver", {
                    "event_type": "download.completed", "event_id": "duplicate-caption-e2e", "payload": completed,
                }, deadline=20)
                await drain("rename")
                assert replay["duplicate"] is True and storage.writes == writes_before_replay

                # Scanning is read-only until the user confirms the fixed batch.
                # Exercise the actual RPC callback instead of bypassing the UI
                # state transition or restoring the pre-confirmation behavior.
                queries_before_scan = list(provider.queries)
                downloads_before_scan = list(provider.downloads)
                metadata_before_scan = deepcopy(metadata_calls)
                chunks_before_scan = deepcopy(upload_chunks)
                inventory = await clients["caption"].request("command.dispatch", {
                    "command": "caption", "args": ["scan", "/Movies"], "chat_id": 10, "user_id": 1,
                }, deadline=20)
                await harness.operation_sink("caption", inventory["operation"])
                await drain("caption")
                scanned = features["caption"].operations[inventory["operation"]["operation_id"]]
                assert scanned["state"] == "awaiting_input", scanned
                assert scanned["stage"] == "inventory_confirmation"
                assert scanned["control"] == "exit"
                assert scanned["details"]["total"] == 1
                assert harness.coordinator.get(scanned["operation_id"]).state == "awaiting_input"
                assert provider.queries == queries_before_scan
                assert provider.downloads == downloads_before_scan
                assert metadata_calls == metadata_before_scan
                assert upload_chunks == chunks_before_scan
                assert storage.writes == writes_before_replay

                start = next(button for row in scanned["details"]["keyboard"] for button in row
                             if button["text"].startswith("开始补字幕"))
                namespace, payload = start["callback_data"].split(":", 1)
                confirmed = await clients["caption"].request("callback.dispatch", {
                    "namespace": namespace, "payload": payload, "chat_id": 10, "user_id": 1,
                }, deadline=20)
                assert confirmed["operation"]["operation_id"] == scanned["operation_id"]
                assert confirmed["session"]["state"] == "open"
                await harness.operation_sink("caption", confirmed["operation"])
                await drain("caption")

                # The same verified subtitle is an idempotent hit after confirmation.
                assert scanned["state"] == "completed", scanned
                assert harness.coordinator.get(scanned["operation_id"]).state == "completed"
                assert scanned["result"]["added_count"] == 0
                assert scanned["result"]["existing_count"] == 1
                assert storage.writes == writes_before_replay
                assert provider.queries[-1].video_path == "/Movies/冰血暴 (1996) ⋯ Fargo/Fargo.mkv"

                # Standalone query follows media.search -> caption -> media.rename,
                # without Download submission or the existence of a video file.
                submitted_before = list(storage.added)
                result = await clients["caption"].request("command.dispatch", {
                    "command": "caption", "args": ["Fargo", "1996"], "chat_id": 10, "user_id": 1,
                }, deadline=20)
                await harness.operation_sink("caption", result["operation"])
                await drain("caption")
                operation = features["caption"].operations[result["operation"]["operation_id"]]
                assert operation["state"] == "completed", operation
                standalone = f"/字幕/冰血暴 (1996) ⋯ Fargo/Fargo.{language}.{extension}"
                assert standalone in storage.nodes
                assert operation["result"]["added_count"] == 1
                assert operation["result"]["results"][0]["placements"][0]["language"] == language
                assert operation["result"]["results"][0]["placements"][0]["cue_count"] == 25
                if provider.name == "assrt":
                    assert credit in operation["status_text"] and source_page in operation["status_text"]
                assert provider.queries[-1].video_path == ""
                assert storage.added == submitted_before
                assert not any(path.startswith('/字幕/') and path.endswith('.mkv') for path in storage.nodes)
                assert len(metadata_calls) >= 3
                assert frames and max(frames) <= 1_048_576
        finally:
            for feature in features.values():
                for handle in getattr(feature, "session_expiry_handles", {}).values():
                    handle.cancel()
            await harness.asyncTearDown()

    asyncio.run(exercise())
