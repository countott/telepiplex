"""Exercise cloud storage identity and absence checks across real Feature RPC."""
import asyncio
from copy import deepcopy
from pathlib import Path, PurePosixPath
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


class SeriesRetryTransport:
    """115-shaped cloud state after one episode was renamed before a failure."""

    release = (
        "/真人剧集/The Wire (2002) Season 1-5 S01-S05 "
        "(1080p BluRay x265 HEVC 10bit AAC 5.1 Silence)/Season 5"
    )
    destination = "/真人剧集/火线 (2002) ⋯ The Wire/The Wire Season 05"
    renamed_source = f"{release}/The Wire S05E10.mkv"
    original_source = (
        f"{release}/The Wire (2002) - S05E09 - Late Editions "
        "(1080p BluRay x265 Silence).mkv"
    )

    def __init__(self, scenario):
        self.scenario = scenario
        self.calls = []
        self.missing_paths = set()
        self.nodes = {
            "/真人剧集": {"file_id": "category", "file_category": "0"},
            self.release: {"file_id": "release-season", "file_category": "0"},
            self.renamed_source: {
                "file_id": "episode-10", "file_category": "1",
                "size_byte": 1024, "sha1": "a" * 40,
            },
            self.original_source: {
                "file_id": "episode-09", "file_category": "1",
                "size_byte": 2048, "sha1": "b" * 40,
            },
        }
        for name, row in self.nodes.items():
            row["file_name"] = PurePosixPath(name).name

    def _path_by_id(self, file_id):
        return next(name for name, row in self.nodes.items()
                    if row["file_id"] == str(file_id))

    def request(self, method, url, **kwargs):
        endpoint = urlsplit(url).path
        self.calls.append((method, endpoint, deepcopy(kwargs)))
        if endpoint == "/open/folder/get_info":
            params = kwargs.get("data") or kwargs.get("params") or {}
            assert method == "POST" and "path" in params
            name = params["path"]
            if name == self.destination and self.scenario in {"failed_lookup", "contradictory_missing"}:
                return Response({
                    "state": self.scenario == "contradictory_missing",
                    "code": 430004 if self.scenario == "contradictory_missing" else 50001,
                    "data": [],
                })
            if name not in self.nodes:
                self.missing_paths.add(name)
                return Response({"state": False, "code": 430004, "data": []})
            return Response({"state": True, "code": 0, "data": [self.nodes[name]]})
        if endpoint == "/open/ufile/files":
            params = kwargs["params"]
            parent = self._path_by_id(params["cid"])
            rows = [dict(row, fn=PurePosixPath(name).name)
                    for name, row in self.nodes.items()
                    if str(PurePosixPath(name).parent) == parent]
            offset, limit = int(params.get("offset", 0)), int(params.get("limit", 1000))
            return Response({"state": True, "code": 0, "data": rows[offset:offset + limit],
                             "offset": offset, "count": len(rows)})
        if endpoint == "/open/folder/add":
            data = kwargs["data"]
            parent = self._path_by_id(data["pid"])
            name = str(PurePosixPath(parent) / data["file_name"])
            assert name not in self.nodes
            self.nodes[name] = {
                "file_id": f"created-{len(self.nodes)}", "file_category": "0",
                "file_name": data["file_name"],
            }
            return Response({"state": True, "code": 0, "data": self.nodes[name]})
        if endpoint == "/open/ufile/update":
            data = kwargs["data"]
            previous = self._path_by_id(data["file_id"])
            name = str(PurePosixPath(previous).parent / data["file_name"])
            self.nodes[name] = self.nodes.pop(previous)
            self.nodes[name]["file_name"] = data["file_name"]
            return Response({"state": True, "code": 0, "data": {}})
        if endpoint == "/open/ufile/move":
            fields = kwargs["files"]
            parent = self._path_by_id(fields["to_cid"][1])
            for file_id in fields["file_ids"][1].split(","):
                previous = self._path_by_id(file_id)
                name = str(PurePosixPath(parent) / PurePosixPath(previous).name)
                self.nodes[name] = self.nodes.pop(previous)
                if self.scenario == "changed_identity":
                    self.nodes[name]["file_id"] = f"foreign-{file_id}"
            return Response({"state": True, "code": 0, "data": {}})
        raise AssertionError((method, endpoint))


@pytest.mark.parametrize("scenario", ["missing", "failed_lookup", "contradictory_missing", "changed_identity"])
def test_real_series_retry_creates_missing_season_and_verifies_native_move(tmp_path, monkeypatch, scenario):
    monkeypatch.syspath_prepend(str(ROOT / "features/download/src"))
    monkeypatch.syspath_prepend(str(ROOT / "features/rename/src"))

    async def exercise():
        from app.runtime.plugin_manifest import PluginManifest
        from telepiplex_download.client import Open115Client
        from telepiplex_download.jobs import DownloadJobStore
        from telepiplex_download.service import DownloadFeature
        from telepiplex_plugin_sdk import FeatureError
        from telepiplex_plugin_sdk.host_client import HostClient
        from telepiplex_rename.file_executor import execute_file_resolutions
        from telepiplex_rename.file_plan import FileResolution
        from telepiplex_rename.service import StorageProxy

        harness = host_fixture.OperationPipelineEndToEndTest()
        await harness.asyncSetUp()
        transport = SeriesRetryTransport(scenario)
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
            proxy = StorageProxy(HostClient(harness.broker.socket_path, "rename-token"),
                                 asyncio.get_running_loop())
            resolutions = [
                FileResolution("episode-10", transport.renamed_source, "resolved",
                               {"source": "tvdb", "external_id": "79126"},
                               {"season_number": 5, "episode_number": 10},
                               f"{transport.destination}/The Wire S05E10.mkv", "move_only", ()),
                FileResolution("episode-09", transport.original_source, "resolved",
                               {"source": "tvdb", "external_id": "79126"},
                               {"season_number": 5, "episode_number": 9},
                               f"{transport.destination}/The Wire S05E09.mkv", "rename_and_move", ()),
            ]
            if scenario in {"failed_lookup", "contradictory_missing"}:
                with pytest.raises(FeatureError):
                    await asyncio.to_thread(execute_file_resolutions, proxy, resolutions,
                                            selected_root=transport.release)
                assert set(transport.nodes) == {
                    "/真人剧集", transport.release, transport.renamed_source, transport.original_source,
                }
                assert not [row for row in transport.calls
                            if row[1] in {"/open/folder/add", "/open/ufile/update", "/open/ufile/move",
                                          "/open/ufile/copy", "/open/ufile/delete"}]
                return

            summary = await asyncio.to_thread(execute_file_resolutions, proxy, resolutions,
                                              selected_root=transport.release)
            assert transport.destination in transport.missing_paths
            additions = [row[2]["data"]["file_name"] for row in transport.calls
                         if row[1] == "/open/folder/add"]
            assert additions == ["火线 (2002) ⋯ The Wire", "The Wire Season 05"]
            renames = [row[2]["data"] for row in transport.calls if row[1] == "/open/ufile/update"]
            assert renames == [{"file_id": "episode-09", "file_name": "The Wire S05E09.mkv"}]
            moves = [row[2]["files"] for row in transport.calls if row[1] == "/open/ufile/move"]
            assert len(moves) == 1
            assert moves[0]["file_ids"] == (None, "episode-10,episode-09")
            assert moves[0]["to_cid"] == (None, transport.nodes[transport.destination]["file_id"])
            assert not [row for row in transport.calls
                        if row[1] in {"/open/ufile/copy", "/open/ufile/delete"}]
            assert transport.renamed_source not in transport.nodes
            assert transport.original_source not in transport.nodes
            for episode in ("09", "10"):
                expected = f"episode-{episode}"
                if scenario == "changed_identity":
                    expected = f"foreign-{expected}"
                assert transport.nodes[f"{transport.destination}/The Wire S05E{episode}.mkv"]["file_id"] == expected
            assert summary.organized_files == (0 if scenario == "changed_identity" else 2)
            assert summary.failed_files == (2 if scenario == "changed_identity" else 0)
            if scenario == "changed_identity":
                assert all(outcome.reason_codes == ("target_missing_after_move",)
                           for outcome in summary.outcomes)
        finally:
            await harness.asyncTearDown()

    asyncio.run(exercise())
