import asyncio
import copy
import json
import tempfile
import threading
import pytest
from pathlib import Path


def test_central_scan_returns_candidate_without_activating_tokens():
    from telepiplex_download.client import Open115Client
    class Response:
        status_code = 200
        headers = {}
        def raise_for_status(self):
            pass
        def json(self):
            return {"state": True, "data": {"status": 2, "access_token": "new", "refresh_token": "new-r"}}
    class Session:
        def post(self, *args, **kwargs):
            return Response()
        def get(self, *args, **kwargs):
            return Response()
    client = Open115Client({"access_token": "old", "refresh_token": "old-r"}, session=Session(), defer_authorization_commit=True)
    tokens = client.complete_device_authorization({"uid": "user", "time": 1, "sign": "sign", "code_verifier": "code"})
    assert tokens == {"access_token": "new", "refresh_token": "new-r"}
    assert (client.access_token, client.refresh_token) == ("old", "old-r")


def test_delayed_refresh_cannot_overwrite_new_authorization(tmp_path):
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        asyncio.run(_delayed_refresh(Path(directory)))


@pytest.mark.parametrize("authorization_mode", ["direct", "scan", "cancel_empty"])
def test_authorization_after_refresh_commit_wins_local_install(authorization_mode):
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        asyncio.run(_delayed_refresh(Path(directory), scenario="after_commit", authorization_mode=authorization_mode))


@pytest.mark.parametrize("scenario", ["success", "write_failure"])
def test_refresh_persists_or_surfaces_storage_failure(scenario):
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        asyncio.run(_delayed_refresh(Path(directory), scenario=scenario))


async def _delayed_refresh(root, scenario="conflict", authorization_mode="direct"):
    from telepiplex_download.client import Open115Client
    from telepiplex_download.config_store import HostConfigStore
    from telepiplex_plugin_sdk import HostClient
    state = {"access_token": "old", "refresh_token": "old-r", "auth_mode": "direct"}
    started, release = threading.Event(), threading.Event()
    persisted, install = threading.Event(), threading.Event()
    class Response:
        status_code = 200
        headers = {}
        def raise_for_status(self):
            pass
        def json(self):
            return {"state": True, "data": {"access_token": "delayed", "refresh_token": "delayed-r"}}
    class Session:
        def post(self, url, *, data, **kwargs):
            assert data == {"refresh_token": "old-r"}
            started.set()
            assert release.wait(5)
            return Response()
    async def serve(reader, writer):
        request = json.loads(await reader.readline())
        params = request["params"]
        ok = True
        if request["method"] == "config.patch":
            ok = all(state.get(k) == v for k, v in params["expected"].items())
            if scenario == "write_failure":
                ok = False
            if ok:
                state.update(params["patch"])
        error_code = "config_write_failed" if scenario == "write_failure" else "config_conflict"
        writer.write((json.dumps({"id": request["id"], "ok": ok, "result": state, "error": {"code": error_code, "message": "changed"}}) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_unix_server(serve, path=str(root / "host.sock"))
    try:
        store = HostConfigStore(HostClient(root / "host.sock", "token"), state)
        def persist_then_pause(access_token, refresh_token, expected):
            result = store.persist_refreshed_tokens(access_token, refresh_token, expected)
            if scenario == "after_commit":
                persisted.set()
                assert install.wait(5)
            return result
        client = Open115Client(dict(state), session=Session(), on_tokens_refreshed=persist_then_pause)
        task = asyncio.create_task(asyncio.to_thread(client.refresh_access_token))
        assert await asyncio.to_thread(started.wait, 2)
        if scenario == "conflict":
            await asyncio.to_thread(store.write_tokens, "authorized", "authorized-r", auth_mode="scan")
            client.set_tokens("authorized", "authorized-r")
        release.set()
        if scenario == "after_commit":
            assert await asyncio.to_thread(persisted.wait, 2)
            if authorization_mode == "cancel_empty":
                await asyncio.to_thread(store.restore, {
                    "config": {"access_token": "", "refresh_token": ""},
                    "owned": {"access_token": "delayed", "refresh_token": "delayed-r"},
                })
                client.clear_tokens()
            else:
                await asyncio.to_thread(store.write_tokens, "authorized", "authorized-r", auth_mode=authorization_mode)
                client.set_tokens("authorized", "authorized-r")
            install.set()
        if scenario == "write_failure":
            from telepiplex_plugin_sdk import FeatureError
            with pytest.raises(FeatureError) as error:
                await task
            assert error.value.code == "config_write_failed"
            assert (client.access_token, client.refresh_token) == ("old", "old-r")
            return
        await task
        if authorization_mode == "cancel_empty":
            assert state["access_token"] == state["refresh_token"] == ""
            assert client.access_token == client.refresh_token == ""
            assert client.config["access_token"] == client.config["refresh_token"] == ""
            return
        if scenario in {"conflict", "after_commit"}:
            assert state == {"access_token": "authorized", "refresh_token": "authorized-r", "auth_mode": "scan" if scenario == "conflict" else authorization_mode}
            assert (client.access_token, client.refresh_token) == ("authorized", "authorized-r")
            assert (client.config["access_token"], client.config["refresh_token"]) == ("authorized", "authorized-r")
        else:
            assert state == {"access_token": "delayed", "refresh_token": "delayed-r", "auth_mode": "direct"}
            assert (client.access_token, client.refresh_token) == ("delayed", "delayed-r")
    finally:
        release.set()
        install.set()
        server.close()
        await server.wait_closed()

def test_host_store_scopes_writes_and_cancel_preserves_new_tokens(tmp_path):
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        asyncio.run(_cancel_preserves_new_tokens(Path(directory)))


async def _cancel_preserves_new_tokens(tmp_path):
    from telepiplex_plugin_sdk import HostClient
    from telepiplex_download.config_store import HostConfigStore
    state = {"auth_mode": "direct", "access_token": "old", "refresh_token": "old-r", "minimum_video_size_mib": 10}
    requests = []
    async def serve(reader, writer):
        request = json.loads(await reader.readline())
        requests.append(request)
        params = request["params"]
        ok = True
        if request["method"] == "config.patch":
            ok = all(state.get(key) == value for key, value in params["expected"].items())
            if ok:
                state.update(params["patch"])
        response = {"id": request["id"], "ok": ok, "result": copy.deepcopy(state), "error": {"code": "config_conflict", "message": "changed"}}
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    socket = tmp_path / "host.sock"
    server = await asyncio.start_unix_server(serve, path=str(socket))
    try:
        store = HostConfigStore(HostClient(socket, "instance"), state)
        snapshot = store.snapshot()
        await asyncio.to_thread(store.write_authorization_tokens, snapshot, "authorized", "authorized-r", auth_mode="scan")
        patch = next(item for item in requests if item["method"] == "config.patch")["params"]
        assert patch == {"patch": {"access_token": "authorized", "refresh_token": "authorized-r", "auth_mode": "scan"}, "expected": {"access_token": "old", "refresh_token": "old-r", "auth_mode": "direct"}}
        state.update(access_token="refreshed", refresh_token="refreshed-r", minimum_video_size_mib=99)
        restored = await asyncio.to_thread(store.restore, snapshot)
        assert restored["access_token"] == "refreshed"
        assert state["minimum_video_size_mib"] == 99
        assert not (tmp_path / "config.yaml").exists()
    finally:
        server.close()
        await server.wait_closed()


def test_host_restore_reverts_only_owned_auth_fields(tmp_path):
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        asyncio.run(_restore_owned(Path(directory)))


async def _restore_owned(tmp_path):
    from telepiplex_download.config_store import HostConfigStore
    from telepiplex_plugin_sdk import HostClient
    state = {"auth_mode": "direct", "access_token": "old", "refresh_token": "old-r", "minimum_video_size_mib": 10}
    async def serve(reader, writer):
        request = json.loads(await reader.readline())
        if request["method"] == "config.patch":
            assert all(state.get(k) == v for k, v in request["params"]["expected"].items())
            state.update(request["params"]["patch"])
        writer.write((json.dumps({"id": request["id"], "ok": True, "result": state}) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_unix_server(serve, path=str(tmp_path / "host.sock"))
    try:
        store = HostConfigStore(HostClient(tmp_path / "host.sock", "token"), state)
        snapshot = store.snapshot()
        await asyncio.to_thread(store.write_tokens, "refresh", "refresh-r", auth_mode="direct")
        await asyncio.to_thread(store.write_authorization_tokens, snapshot, "new", "new-r", auth_mode="scan")
        state["minimum_video_size_mib"] = 20
        await asyncio.to_thread(store.restore, snapshot)
        assert state == {"auth_mode": "direct", "access_token": "refresh", "refresh_token": "refresh-r", "minimum_video_size_mib": 20}
    finally:
        server.close()
        await server.wait_closed()
