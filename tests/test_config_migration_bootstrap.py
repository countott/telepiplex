import asyncio

import pytest
import yaml
from tests.test_bot_runtime_startup import load_bot_module
from tests.test_config_semantics_migration import contracts


def test_real_bootstrap_keeps_blocked_v2_recoverable_and_retries(tmp_path):
    from app.runtime.config_projection import ProjectionContract
    config_path = tmp_path / 'config.yaml'
    config = {'config_version': 2, 'plugins': {'root': str(tmp_path/'plugins'), 'runtime_root': str(tmp_path/'runtime')},
              'features': {'caption': {'output_path': '/a//b'}, 'search': {'metadata': {'tmdb': {'timeout': 300}}}}}
    config_path.write_text(yaml.safe_dump(config))
    original = config_path.read_bytes()
    manager = load_bot_module().build_plugin_manager(config, tmp_path/'host.db', config_path=config_path)
    try:
        assert manager.migration_status()['blockers']
        assert config_path.read_bytes() == original
        store = manager.store.central_config
        assert store.effective('search', {}, contract=ProjectionContract({}))['metadata']['tmdb']['timeout'] == 60
        with pytest.raises(Exception, match='timeout'):
            store.prepare_patch('search', {'metadata': {'tmdb': {'timeout': 299}}}, contract=ProjectionContract({}))
        store.commit(store.prepare_patch('caption', {'output_path': '/safe'}))
        assert not asyncio.run(manager.retry_semantic_migration())['blockers']
        assert store.read()['config_version'] == 3
    finally:
        asyncio.run(manager.close())


def test_cold_restore_reload_and_upgrade_search_while_other_feature_blocks(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tests.test_plugin_manager import PluginManagerTest
    from tests.test_central_config_lifecycle import SnapshotSupervisor
    from app.runtime import config_semantics_migration as migration
    from app.runtime.plugin_manager import PluginOperationError
    packages = contracts()
    monkeypatch.setattr(migration, 'load_builtin_contracts', lambda: packages)
    bot = load_bot_module()
    config = {'config_version': 2, 'plugins': {'root': str(tmp_path/'plugins'), 'runtime_root': str(tmp_path/'runtime')},
              'features': {'caption': {'output_path': '/a//b'}, 'search': {'metadata': {'tmdb': {'timeout': 300}}}}}
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(config))
    original = path.read_bytes()
    fixture = SimpleNamespace(root=tmp_path)

    def start_host():
        manager = bot.build_plugin_manager(config, tmp_path/'host.db', config_path=path)
        manager.supervisor = SnapshotSupervisor()
        manager._venv_installer = lambda staged: PluginManagerTest._install_venv(fixture, staged)
        manager.stabilize_seconds = 0
        return manager

    def package(version):
        defaults, schema = packages['search']
        return PluginManagerTest._artifact(fixture, 'search', version, config_default=defaults, config_schema=schema)

    async def exercise():
        manager = start_host()
        try:
            await manager.install(package('1.0.0'))
        finally:
            await manager.close()
        manager = start_host()
        try:
            assert manager.migration_status()['blockers']
            assert '60' in str(manager.migration_status()['notices'])
            await manager.restore_active()
            assert manager.supervisor.process('search').config['metadata']['tmdb']['timeout'] == 60
            await manager.reload_config('search')
            await manager.update(package('1.1.0'))
            assert path.read_bytes() == original
            config['features']['search']['metadata']['tmdb']['timeout'] = 299
            path.write_text(yaml.safe_dump(config))
            with pytest.raises(PluginOperationError, match='timeout'):
                await manager.reload_config('search')
            assert manager.supervisor.process('search').config['metadata']['tmdb']['timeout'] == 60
        finally:
            await manager.close()
    asyncio.run(exercise())


async def _start_with_frozen_releases(tmp_path, config, names):
    from types import SimpleNamespace
    from tests.mixed_config_releases import release
    from tests.test_plugin_manager import PluginManagerTest
    from tests.test_central_config_lifecycle import SnapshotSupervisor
    from app.runtime.plugin_store import PluginStore
    from app.runtime.plugin_artifact import verify_tpx

    config = {**config, "plugins": {"root": str(tmp_path/"plugins"), "runtime_root": str(tmp_path/"runtime")}}
    path = tmp_path/"config.yaml"
    path.write_text(yaml.safe_dump(config))
    fixture = SimpleNamespace(root=tmp_path)
    async def install_venv(staged):
        return await PluginManagerTest._install_venv(fixture, staged)

    store = PluginStore(tmp_path/"plugins")
    for name in names:
        staged = store.stage(verify_tpx(release(tmp_path, name, old=True)))
        await install_venv(staged)
        store.set_active(store.commit(staged))
    manager = load_bot_module().build_plugin_manager(config, tmp_path/"host.db", config_path=path)
    manager.supervisor = SnapshotSupervisor()
    manager._venv_installer = install_venv
    manager.stabilize_seconds = 0
    return manager


def test_host_first_cold_restore_and_independent_upgrades_with_frozen_old_contracts(tmp_path):
    from types import SimpleNamespace
    from tests.mixed_config_releases import release
    names = ("download", "search", "rename", "sync", "caption")
    menu = [{"name": "Archive", "path": "library/archive"}, {"name": "Queue", "path": "incoming"}]
    config = {"config_version": 2,
              "features": {"download": {"save_directories": menu}, "rename": {"unorganized_path": "/incoming"}}}
    path = tmp_path/"config.yaml"
    async def exercise():
        manager = await _start_with_frozen_releases(tmp_path, config, names)
        supervisor = manager.supervisor
        try:
            assert yaml.safe_load(path.read_bytes())["config_version"] == 3, manager.migration_status()
            await manager.restore_active()
            for name in names:
                child = supervisor.process(name)
                assert child and child.state == "healthy", name
                assert manager.router.plugin_route(name).client is child.client
            assert supervisor.process("download").config["save_directories"] == menu
            assert supervisor.process("rename").config["unorganized_path"] == "/incoming"
            tokens = {"access_token": "fresh-access", "refresh_token": "fresh-refresh"}
            for name in ("sync", "caption", "rename", "search", "download"):
                peers = {p: supervisor.process(p) for p in names if p != name}
                if name == "sync":
                    supervisor.on_start = lambda _: manager._write_live_config(
                        SimpleNamespace(plugin_id="download", active=True), tokens, {k: "" for k in tokens})
                await manager.update(release(tmp_path, name))
                supervisor.on_start = None
                for p, child in peers.items():
                    assert supervisor.process(p) is child
                    assert manager.router.plugin_route(p).client is child.client
                assert supervisor.process("download").config["save_directories"] == menu
            await manager.rollback("download")
            assert manager.store.active("download").version == "2.3.0"
            for key, value in tokens.items():
                assert supervisor.process("download").config[key] == value
                assert yaml.safe_load(path.read_bytes())["features"]["download"][key] == value
            assert supervisor.process("download").config["save_directories"] == menu
        finally:
            await manager.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("name,shared,key,expected", [
    ("download", {"directories": {"sp": {"name": "Menu", "path": "/ leading"}},
                  "directory_roles": {"manual_downloads": [{"directory_id": "sp", "label": "Queue"}]}},
     "save_directories", [{"name": "Queue", "path": " leading"}]),
    ("rename", {"directory_roles": {"unorganized_inventory": None}}, "unorganized_path", "/"),
])
def test_compatible_target_recovers_quarantined_directory_consumer(tmp_path, name, shared, key, expected):
    from tests.mixed_config_releases import release
    async def exercise():
        manager = await _start_with_frozen_releases(tmp_path, {"config_version": 3, "shared": shared},
                                                    ("download", "sync") if name == "download" else
                                                    ("download", "search", "rename", "sync"))
        try:
            status = {r.plugin_id: r for r in await manager.restore_active()}
            assert status[name].state == "quarantined"
            assert "Upgrade " + name.title() in status[name].message
            assert manager.supervisor.process(name) is None
            peer = manager.supervisor.process("sync")
            await manager.update(release(tmp_path, name))
            recovered = manager.supervisor.process(name)
            assert recovered.config[key] == expected
            assert manager.router.plugin_route(name).client is recovered.client
            assert manager.supervisor.process("sync") is peer and peer.state == "healthy"
        finally:
            await manager.close()
    asyncio.run(exercise())

