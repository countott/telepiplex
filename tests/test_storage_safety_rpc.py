"""Copy acknowledgements cannot authorize deletion across real Feature RPC."""
import asyncio
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from tests import test_operation_pipeline_e2e as host_fixture

ROOT = Path(__file__).resolve().parents[1]


class NoWait:
    def acquire(self, category):
        return 0.0


class Response:
    headers = {}
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return deepcopy(self.payload)


class CopyAckTransport:
    """Successful copy acknowledgement with a missing or incorrect target."""
    def __init__(self, wrong_target, *, wrapped=False, failed_lookup=False):
        self.wrong_target = wrong_target
        self.wrapped = wrapped
        self.failed_lookup = failed_lookup
        self.failed_hits = 0
        self.calls = []
        self.nodes = {
            "/Downloads/Show.mkv": {
                "file_id": "source-video", "file_category": "1",
                "file_name": "Show.mkv", "size_byte": 1024, "sha1": "a" * 40,
            },
            "/Series/Show": {"file_id": "target-dir", "file_category": "0"},
        }

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        self.calls.append((method, path, kwargs))
        if path == "/open/folder/get_info":
            params = kwargs.get("data") or kwargs.get("params") or {}
            if "path" in params:
                assert method == "POST"  # Path information is a read-only POST.
                if self.failed_lookup and params["path"] == "/Series/Show/Show.mkv":
                    self.failed_hits += 1
                    return Response({"state": self.failed_lookup == "contradictory", "code": 500, "data": []})
                node = self.nodes.get(params["path"])
            else:
                assert method == "GET"
                node = next((row for row in self.nodes.values()
                             if row["file_id"] == str(params.get("file_id"))), None)
            data = [node] if node and self.wrapped else node if node else []
            return Response({"state": True, "code": 0, "data": data})
        if path == "/open/ufile/copy":
            if self.wrong_target:
                self.nodes["/Series/Show/Show.mkv"] = {
                    "file_id": "wrong-video", "file_category": "1",
                    "file_name": "Show.mkv", "size_byte": 1024, "sha1": "b" * 40,
                }
            return Response({"state": True, "code": 0, "data": {}})
        if path == "/open/ufile/delete":
            requested = str(kwargs["data"]["file_ids"])
            self.nodes = {name: row for name, row in self.nodes.items()
                          if row["file_id"] != requested}
            return Response({"state": True, "code": 0, "data": {}})
        raise AssertionError((method, path))


@pytest.mark.parametrize("wrong_target", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("failed_lookup", [False, True, "contradictory"])
def test_real_download_storage_rpc_retains_source_when_copy_target_is_unverified(tmp_path, monkeypatch, wrong_target, wrapped, failed_lookup):
    monkeypatch.syspath_prepend(str(ROOT / "features/download/src"))

    async def exercise():
        from app.runtime.plugin_manifest import PluginManifest
        from telepiplex_download.client import Open115Client
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature
        from telepiplex_plugin_sdk.host_client import HostClient
        from telepiplex_plugin_sdk import FeatureError

        harness = host_fixture.OperationPipelineEndToEndTest()
        await harness.asyncSetUp()
        transport = CopyAckTransport(wrong_target, wrapped=wrapped, failed_lookup=failed_lookup)
        client = Open115Client({"access_token": "fixture"}, session=transport, pacer=NoWait())
        feature = DownloadFeature(config={}, host=HostClient(harness.broker.socket_path, "download-token"),
                                  client=client, jobs=DownloadJobStore(tmp_path / "download.db"))
        try:
            download = PluginManifest.from_mapping(yaml.safe_load(
                (ROOT / "features/download/manifest.yaml").read_text()
            ))
            await harness._start_runtime(download, "download-token", capabilities={
                "storage.provider": feature.storage_capability,
                "download.provider": feature.download_capability,
            })
            feature.bind_runtime(harness.runtimes[-1])
            await harness._start_runtime(harness._manifest("rename", requires=("storage.provider",)), "rename-token")
            caller = HostClient(harness.broker.socket_path, "rename-token")
            try:
                result = await caller.call_capability("storage.provider", "move_file_detailed", {
                    "args": ["/Downloads/Show.mkv", "/Series/Show"], "kwargs": {},
                }, deadline=10)
            except FeatureError:
                assert failed_lookup
            else:
                value = result["value"]
                assert value["state"] not in {"moved", "no_op"}, value
                assert value["source_deleted"] is False
            assert transport.nodes["/Downloads/Show.mkv"]["file_id"] == "source-video"
            assert len([row for row in transport.calls if row[1] == "/open/ufile/copy"]) == (0 if failed_lookup else 1)
            assert not [row for row in transport.calls if row[1] == "/open/ufile/delete"]
            if failed_lookup:
                assert transport.failed_hits == 1
            assert client.timeout == 30  # Per-call verification cannot mutate shared client configuration.
        finally:
            await harness.asyncTearDown()

    asyncio.run(exercise())


@pytest.mark.parametrize("listing_kind", ["count", "continuation", "nested_error", "aliases", "replacement", "lookup_error", "lookup_contradictory"])
def test_real_directory_listing_rpc_preserves_contradictory_count_and_never_deletes(tmp_path, monkeypatch, listing_kind):
    """115 outer pagination evidence must survive the full storage RPC path."""
    monkeypatch.syspath_prepend(str(ROOT / "features/download/src"))
    monkeypatch.syspath_prepend(str(ROOT / "features/rename/src"))

    class ContradictoryDirectoryTransport(CopyAckTransport):
        def __init__(self):
            super().__init__(False, wrapped=True)
            self.nodes["/Downloads/Release"] = {
                "file_id": "release-dir", "file_category": "0",
            }

        def request(self, method, url, **kwargs):
            if (listing_kind in {"lookup_error", "lookup_contradictory"}
                    and urlsplit(url).path == "/open/folder/get_info"
                    and (kwargs.get("data") or {}).get("path") == "/Downloads/Release"):
                self.calls.append((method, urlsplit(url).path, kwargs))
                return Response({"state": listing_kind == "lookup_contradictory", "code": 50001, "data": []})
            if urlsplit(url).path == "/open/ufile/files":
                self.calls.append((method, urlsplit(url).path, kwargs))
                payload = {"state": True, "code": 0, "data": [], "offset": 0}
                if listing_kind == "count":
                    payload["count"] = 1
                elif listing_kind == "continuation":
                    payload["has_more"] = True
                elif listing_kind == "nested_error":
                    payload["data"] = {"code": 50001, "list": []}
                elif listing_kind == "aliases":
                    payload["list"] = []
                    payload["data"] = [{"file_id": "notes", "fn": "notes.txt", "size": 1}]
                else:
                    # Simulate a provider path replacement after observing the
                    # original directory, plus invalidation by another write.
                    self.nodes["/Downloads/Release"] = {
                        "file_id": "replacement-dir", "file_category": "0",
                    }
                    self.nodes["/Downloads/Release/notes.txt"] = {
                        "file_id": "notes", "file_category": "1", "size": 1,
                    }
                    self.client._remove_cached_file("/Downloads/Release")
                    payload["count"] = 0
                return Response(payload)
            return super().request(method, url, **kwargs)

    async def exercise():
        from app.runtime.plugin_manifest import PluginManifest
        from telepiplex_download.client import Open115Client
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature
        from telepiplex_plugin_sdk.host_client import HostClient
        from telepiplex_rename.file_executor import cleanup_source_directories
        from telepiplex_rename.file_plan import FileResolution
        from telepiplex_rename.service import StorageProxy

        harness = host_fixture.OperationPipelineEndToEndTest()
        await harness.asyncSetUp()
        transport = ContradictoryDirectoryTransport()
        client = Open115Client({"access_token": "fixture"}, session=transport, pacer=NoWait())
        transport.client = client
        feature = DownloadFeature(config={}, host=HostClient(harness.broker.socket_path, "download-token"),
                                  client=client, jobs=DownloadJobStore(tmp_path / "download.db"))
        try:
            download = PluginManifest.from_mapping(yaml.safe_load(
                (ROOT / "features/download/manifest.yaml").read_text()
            ))
            await harness._start_runtime(download, "download-token", capabilities={
                "storage.provider": feature.storage_capability,
                "download.provider": feature.download_capability,
            })
            feature.bind_runtime(harness.runtimes[-1])
            await harness._start_runtime(harness._manifest("rename", requires=("storage.provider",)), "rename-token")
            caller = HostClient(harness.broker.socket_path, "rename-token")
            proxy = StorageProxy(caller, asyncio.get_running_loop())
            resolution = FileResolution(
                "video", "/Downloads/Release/Show.mkv", "resolved",
                {"source": "tvdb", "external_id": "123"}, {},
                "/Series/Show/Show.mkv", "move_only", (),
            )
            summary = await asyncio.to_thread(
                cleanup_source_directories, proxy, [resolution],
                selected_root="/Downloads/Release", include_selected_root=True,
            )
            assert not [row for row in transport.calls if row[1] == "/open/ufile/delete"]
            expected_id = "replacement-dir" if listing_kind == "replacement" else "release-dir"
            assert transport.nodes["/Downloads/Release"]["file_id"] == expected_id
            assert not summary.complete
            assert summary.outcomes[0].state == ("delete_failed" if listing_kind == "replacement" else "lookup_failed")
        finally:
            await harness.asyncTearDown()

    asyncio.run(exercise())
