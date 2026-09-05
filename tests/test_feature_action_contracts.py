"""Feature buttons must survive the real Host manifest, renderer and gateway."""
import asyncio
import functools
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from app.handlers import plugin_handler
from app.runtime.capability_router import CapabilityRouter
from app.runtime.plugin_manifest import PluginManifest

ROOT = Path(__file__).resolve().parents[1]


def _load_sync_fixtures():
    # Keep Feature imports isolated from similarly named tests in other modules.
    import sys
    sys.path.insert(0, str(ROOT / "features/sync/src"))
    spec = importlib.util.spec_from_file_location(
        "sync_action_contract_fixtures", ROOT / "features/sync/tests/test_feature_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sync_host(tmp_path):
    fixtures = _load_sync_fixtures()
    jobs = fixtures.PlexJobRepository(tmp_path / "jobs.db")
    service = fixtures.FakeService(jobs)
    class CapturingRuntime(fixtures.FakeRuntime):
        def spawn(self, awaitable, *, task_id):
            previous = self.tasks.get(task_id)
            if previous is not None:
                previous.close()
            super().spawn(awaitable, task_id=task_id)

    runtime = CapturingRuntime()
    feature = fixtures.SyncFeature(
        config={}, host=fixtures.FakeHost(), state_path=tmp_path / "state",
        repository=jobs, service_factory=lambda: service,
    )
    manifest = PluginManifest.from_mapping(yaml.safe_load(
        (ROOT / "features/sync/manifest.yaml").read_text()
    ))
    dispatched = []

    async def request(method, params, **kwargs):
        dispatched.append((method, params))
        assert method == "callback.dispatch"
        return await feature.callback(params)

    router = CapabilityRouter()
    router.activate("sync", manifest, SimpleNamespace(request=request))
    yield SimpleNamespace(
        feature=feature, service=service, jobs=jobs, router=router,
        route=router.callback_route("sync"), dispatched=dispatched, runtime=runtime,
    )
    for task in runtime.tasks.values():
        if hasattr(task, "close"):
            task.close()


def keyboard(host, result):
    actions = [action for action in result["actions"] if (action.get("data") or {}).get("keyboard")]
    assert actions
    callbacks = []
    for action in actions:
        markup = plugin_handler._keyboard_markup(host.route, action["data"])
        assert markup is not False, "Host rejected current Feature buttons"
        callbacks.extend(button.callback_data for row in markup.inline_keyboard for button in row)
    assert all(host.router.callback_route(data.partition(":")[0]) is host.route for data in callbacks)
    return callbacks


async def click(host, data):
    query = SimpleNamespace(data=data, answer=AsyncMock())
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=10), update_id=123,
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={
        plugin_handler.ROUTER_KEY: host.router,
    }))
    captured = []

    async def handle(update, context, route, result):
        assert route is host.route
        captured.append(result)

    with patch.object(plugin_handler.init, "check_user", return_value=True), patch.object(
        plugin_handler, "handle_feature_result", side_effect=handle
    ):
        await plugin_handler.dynamic_callback_gateway(update, context)
    assert len(captured) == 1
    return captured[0]


def run_async(test):
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


@run_async
async def test_sync_scan_buttons_render_and_dispatch_through_host(sync_host):
    host = sync_host
    host.feature.bind_runtime(host.runtime)
    host.service.libraries = [{"id": str(i), "title": f"Library {i}"} for i in range(1, 10)]
    first = await host.feature.command({"command": "scan", "chat_id": 10, "user_id": 1})
    assert "scan" in [command.name for command in host.route.manifest.commands]
    buttons = keyboard(host, first)
    page = await click(host, next(data for data in buttons if ":page:" in data))
    page_buttons = keyboard(host, page)
    returned = await click(host, next(data for data in page_buttons if ":page:" in data))
    keyboard(host, returned)
    for data in ("sync:scan:all", "sync:scan:1", "sync:scan:cancel"):
        assert data in buttons
        result = await click(host, data)
        assert result is not None
    assert host.router.callback_route("plex") is None


@run_async
@pytest.mark.parametrize("section", ["plex", "tmdb", "fanart"])
async def test_sync_config_buttons_render_and_dispatch_through_host(sync_host, section):
    host = sync_host
    host.feature.bind_runtime(host.runtime)
    owner = {"chat_id": 10, "user_id": 1}
    menu = await host.feature.command({**owner, "command": "sync_config"})
    buttons = keyboard(host, menu)
    selected = await click(host, next(data for data in buttons if data == f"sync:config:{section}"))
    keyboard(host, selected)
    if section == "plex":
        prompt = await host.feature.message({**owner, "text": "http://plex:32400"})
        keyboard(host, prompt)
    confirmation = await host.feature.message({**owner, "text": "local-test-secret"})
    confirms = keyboard(host, confirmation)
    saved = await click(host, next(data for data in confirms if data.endswith(":confirm")))
    assert section in saved["config_patch"]
    menu = await host.feature.command({**owner, "command": "sync_config"})
    exited = await click(host, next(data for data in keyboard(host, menu) if data.endswith(":cancel")))
    assert exited["session"]["state"] == "close"


@run_async
@pytest.mark.parametrize("kind", ["artwork", "audio", "subtitle"])
async def test_sync_selection_buttons_render_and_dispatch_through_host(sync_host, kind):
    host = sync_host
    host.feature.bind_runtime(host.runtime)
    owner = {"chat_id": 10, "user_id": 1}
    job = host.jobs.create_or_get(f"waiting-{kind}", {**owner, "resource_name": "Choice"})
    candidates = [{"id": i, "url": f"https://example.com/{i}.jpg", "title": f"Candidate {i}"} for i in range(12)]
    waiting = {"kind": kind, "target_id": "target-1", "rating_key": "42", "part_id": 11,
               "candidates": candidates, "candidate_index": 0, "selection_nonce": "nonce"}
    host.jobs.update(job["id"], state="awaiting_selection", step_results={kind: {
        "status": "awaiting_selection", "waiting": waiting,
    }})
    result = await host.feature.command({**owner, "command": "sync", "args": [str(job["id"])]})
    buttons = keyboard(host, result)
    # Every generated navigation/select/confirm button reaches the real Feature parser.
    for data in buttons:
        if data == "sync:cancel":
            continue
        followup = await click(host, data)
        if any((action.get("data") or {}).get("keyboard") for action in followup.get("actions", [])):
            keyboard(host, followup)
    cancelled = await click(host, "sync:cancel")
    assert cancelled.get("operation") is not None
