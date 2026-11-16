"""Central YAML transactions with real storage and fake child processes."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

import yaml

from tests import test_plugin_manager as helpers


def category_routes(path):
    return [{"kind": kind, "name": kind, "path": path, "plex_library_id": ""}
            for kind in ("live_action_movie", "animated_movie", "live_action_series", "animated_series")]


class SnapshotSupervisor(helpers.FakeSupervisor):
    fail_plugin = ""
    on_start = None

    def promote(self, process):
        if process.state != "healthy":
            raise RuntimeError("only healthy processes can be promoted")
        return super().promote(process)

    async def start(self, release, *, shadow=False, config=None):
        if self.on_start:
            self.on_start(release)
        if release.plugin_id == self.fail_plugin:
            raise RuntimeError("child failed")
        process = await super().start(release, shadow=shadow)
        process.config = deepcopy(config)
        process.launch_config = deepcopy(config)
        return process


class CentralLifecycleTest(unittest.IsolatedAsyncioTestCase):
    _artifact = helpers.PluginManagerTest._artifact
    _install_venv = helpers.PluginManagerTest._install_venv

    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        from app.runtime.central_config import CentralConfigStore
        from app.runtime.capability_router import CapabilityRouter
        from app.runtime.event_journal import EventJournal
        from app.runtime.plugin_store import PluginStore
        from app.runtime.plugin_manager import PluginManager
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "config.yaml"
        self.path.write_text("config_version: 2\nfeatures: {}\nshared: {}\n")
        self.central = CentralConfigStore(self.path)
        self.store = PluginStore(self.root / "plugins", central_config=self.central)
        self.supervisor = SnapshotSupervisor()
        self.router = CapabilityRouter()
        self.journal = EventJournal(self.root / "host.db")
        self.manager = PluginManager(store=self.store, supervisor=self.supervisor,
            router=self.router, journal=self.journal, stabilize_seconds=0,
            venv_installer=self._install_venv)

    async def asyncTearDown(self):
        self.journal.close()
        self.temp.cleanup()

    async def install_feature(self, name, version="1.0.0"):
        return await self.manager.install(self._artifact(name, version,
            provides=((f"{name}.test", True),), commands=(name,),
            config_schema={"type": "object"},
            config_default={"category_folder": category_routes("/old"),
                            "access_token": "old", "prefix": "old"}))

    async def install_download(self):
        from app.runtime.plugin_artifact import build_tpx
        self._artifact("download", config_schema={"type": "object"},
                       config_default={"access_token": "old", "refresh_token": "refresh"})
        source = self.root / "source-download-1.0.0"
        path = source / "manifest.yaml"
        manifest = yaml.safe_load(path.read_text())
        manifest["config_write_mode"] = "host_rpc_v1"
        path.write_text(yaml.safe_dump(manifest))
        await self.manager.install(build_tpx(source, self.root / "modern-download.tpx"))

    async def test_quarantined_old_search_can_upgrade_to_marked_target(self):
        from app.runtime.plugin_manager import PluginOperationError
        from tests.test_config_semantics_migration import contracts
        defaults, old_schema = contracts()['search']
        old_schema.pop('x-telepiplex-metadata-timeout-contract',None)
        self.path.write_text('config_version: 3\n')
        await self.manager.install(self._artifact('search', '1.0.0', config_schema=old_schema, config_default=defaults))
        await self.supervisor.close_all()
        doc = self.central.read()
        doc['features'] = {'search': {'metadata': {'tmdb': {'timeout': 300}}}}
        self.path.write_text(yaml.safe_dump(doc))
        from tests.test_bot_runtime_startup import load_bot_module
        host_config = {'plugins': {'root': str(self.store.root), 'runtime_root': str(self.root/'runtime')}}
        cold = load_bot_module().build_plugin_manager(host_config, self.root/'cold.db', config_path=self.path)
        cold.supervisor = SnapshotSupervisor()
        cold._venv_installer = self._install_venv
        cold.stabilize_seconds = 0
        try:
            self.assertEqual((await cold.restore_active())[0].state, 'quarantined')
            marked = {**old_schema, 'x-telepiplex-metadata-timeout-contract': 'seconds_1_300_v1'}
            await cold.update(self._artifact('search', '1.1.0', config_schema=marked, config_default=defaults))
            self.assertEqual(cold.supervisor.process('search').config['metadata']['tmdb']['timeout'], 300)
            with self.assertRaises(PluginOperationError):
                await cold.rollback('search')
            self.assertEqual(cold.store.active('search').version, '1.1.0')
        finally:
            await cold.close()

    async def test_host_connection_editor_validates_disabled_release_without_starting(self):
        from app.runtime.config_settings import setting_rows
        from tests.test_config_semantics_migration import contracts
        self.path.write_text('config_version: 3\n')
        defaults,schema=contracts()['sync']
        await self.manager.install(self._artifact('sync',config_schema=schema,config_default=defaults))
        await self.manager.disable('sync')
        row=next(r for r in setting_rows(self.manager,'sync_plex') if r.field=='plex.token')
        result=await self.manager.save_setting(row,'new')
        self.assertEqual(result.details['affected_plugins'],[])
        self.assertFalse(self.store.active('sync').enabled)
        self.assertIsNone(self.supervisor.process('sync'))
        self.assertEqual(self.central.read()['features']['sync']['plex']['token'],'new')

    async def test_host_connection_candidate_failure_keeps_prior_value_and_live_process(self):
        from app.runtime.config_settings import setting_rows
        from tests.test_config_semantics_migration import contracts
        self.path.write_text('config_version: 3\n')
        defaults,schema=contracts()['sync']
        await self.manager.install(self._artifact('sync',config_schema=schema,config_default=defaults))
        row=setting_rows(self.manager,'sync_plex')[1]
        old=self.supervisor.process('sync')
        original=self.central.read()
        self.supervisor.fail_plugin='sync'
        with self.assertRaises(Exception):
            await self.manager.save_setting(row,'discarded')
        self.assertEqual(self.central.read(),original)
        self.assertIs(self.supervisor.process('sync'),old)

    async def test_repair_batch_candidate_failure_keeps_original_v2_and_live_caption(self):
        from app.runtime.config_settings import setting_rows
        from tests.test_config_semantics_migration import contracts
        defaults, schema = contracts()['caption']
        await self.manager.install(self._artifact('caption',config_schema=schema,config_default=defaults))
        process = self.supervisor.process('caption')
        document = self.central.read()
        document['features'] = {'caption': {'output_path': '../bad','providers': {'local_archive': {'roots':15}}},
                                'download': {'access_token':'latest'}}
        self.path.write_text(yaml.safe_dump(document))
        original = self.path.read_bytes()
        rows = setting_rows(self.manager,'repair_caption')
        batch = [(rows[0],'/subs',False),(rows[1],['/archive'],False)]
        self.supervisor.fail_plugin = 'caption'
        with self.assertRaises(Exception):
            await self.manager.save_repairs(batch)
        self.assertEqual(self.path.read_bytes(),original)
        self.assertIs(self.supervisor.process('caption'),process)
        self.supervisor.fail_plugin = ''
        result = await self.manager.save_repairs(batch)
        self.assertEqual(result.details['affected_plugins'],['caption'])
        self.assertEqual(self.supervisor.process('caption').config['output_path'],'/subs')
        self.assertEqual(self.central.read()['features']['download']['access_token'],'latest')

    async def _settings_dispatch_fixture(self):
        from unittest.mock import AsyncMock
        from tests.test_config_semantics_migration import contracts
        from tests.test_shared_config_handler import SharedConfigHandlerTest
        from app.handlers.plugin_handler import MANAGER_KEY, ROUTER_KEY
        defaults,schema=contracts()['sync']
        self.path.write_text(yaml.safe_dump({'config_version':3,'features':{'sync':{'plex':{'token':'current'}}}}))
        await self.manager.install(self._artifact('sync',commands=('sync_config','scan'),
                                                 config_schema=schema,config_default=defaults))
        route=self.router.plugin_route('sync')
        route.client.process=self.supervisor.process('sync')
        route.client.request=AsyncMock(return_value={'actions':[],'session':{'state':'close'}})
        update,ctx,_=SharedConfigHandlerTest().request()
        ctx.application.bot_data.update({MANAGER_KEY:self.manager, ROUTER_KEY:self.router})
        return update,ctx,route

    async def test_scan_cannot_reauthorize_retired_patch_from_current_process(self):
        from unittest.mock import patch
        from app.handlers.plugin_handler import (
            deliver_feature_result, dynamic_command_gateway, retire_feature_settings, settings_epoch,
        )
        update,ctx,route=await self._settings_dispatch_fixture()
        update.callback_query=None
        original_process=self.supervisor.process('sync')
        old_epoch=settings_epoch(ctx,update)
        retire_feature_settings(update,ctx,'sync')
        stale={'config_patch':{'plex':{'token':'obsolete'}},'actions':[]}
        with patch('app.handlers.plugin_handler.init.check_user',return_value=True):
            update.effective_message.text='/scan'
            await dynamic_command_gateway(update,ctx)
            for epoch in (old_epoch,settings_epoch(ctx,update)):
                await deliver_feature_result(update,ctx,route,stale,epoch)
                self.assertEqual(self.central.read()['features']['sync']['plex']['token'],'current')
                self.assertIs(self.supervisor.process('sync'),original_process)
            business = {'actions': [{'kind': 'send_message', 'text': '扫描完成'}]}
            await deliver_feature_result(update,ctx,route,business,old_epoch)
            update.effective_message.reply_text.assert_awaited_with('扫描完成')
            route.client.request.return_value={'actions':[],'session':{'state':'open'}}
            update.effective_message.text='/sync_config'
            await dynamic_command_gateway(update,ctx)
            await deliver_feature_result(update,ctx,route,stale,old_epoch)
            self.assertEqual(self.central.read()['features']['sync']['plex']['token'],'current')
            # Different PTB owners can have the same generation.
            other = SimpleNamespace(effective_chat=SimpleNamespace(id=20), effective_user=update.effective_user)
            ctx.user_data['settings_epochs'][(20,1)] = ctx.user_data['settings_epochs'][(10,1)]
            await deliver_feature_result(update,ctx,route,stale,settings_epoch(ctx,other))
            self.assertEqual(self.central.read()['features']['sync']['plex']['token'],'current')
            await deliver_feature_result(update,ctx,route,{'config_patch':{'plex':{'token':'fresh'}}},settings_epoch(ctx,update))
            self.assertEqual(self.central.read()['features']['sync']['plex']['token'],'fresh')

    async def test_direct_config_delayed_patch_cannot_write_after_scan(self):
        import asyncio
        from unittest.mock import patch
        from app.handlers.config_handler import direct_config_callback
        from app.handlers.plugin_handler import dynamic_command_gateway
        update,ctx,route=await self._settings_dispatch_fixture()
        update.callback_query.data='host-config-direct:sync'
        started,release=asyncio.Event(),asyncio.Event()
        async def respond(method,params,**kwargs):
            if params['command']=='scan':
                return {'actions':[],'session':{'state':'close'}}
            started.set()
            await release.wait()
            return {'config_patch':{'plex':{'token':'obsolete'}},'actions':[]}
        route.client.request.side_effect=respond
        with patch('app.handlers.config_handler.init.check_user',return_value=True):
            pending=asyncio.create_task(direct_config_callback(update,ctx))
            await asyncio.wait_for(started.wait(),2)
            scan=SimpleNamespace(effective_chat=update.effective_chat,effective_user=update.effective_user,
                                 effective_message=SimpleNamespace(text='/scan',reply_text=update.effective_message.reply_text),
                                 callback_query=None)
            await dynamic_command_gateway(scan,ctx)
            release.set()
            await pending
        self.assertEqual(self.central.read()['features']['sync']['plex']['token'],'current')

    async def test_recovery_reload_reconciles_running_shared_peers(self):
        for key in ('search', 'rename', 'sync', 'caption'):
            await self.install_feature(key)
        original_caption = self.supervisor.process('caption')
        doc = self.central.read()
        doc['features']['caption'] = {'output_path': '/unsafe//caption'}
        doc['shared'] = {'tmdb': {'api_key': 'new'}, 'category_folder': category_routes('/edited')}
        self.path.write_text(yaml.safe_dump(doc))
        self.manager.bootstrap_semantics()
        self.assertTrue(self.manager.migration_status()['blockers'])
        await self.manager.reload_config('search')
        for key in ('search', 'rename', 'sync'):
            self.assertEqual(self.supervisor.process(key).config['category_folder'][0]['path'], '/edited')
        self.assertEqual(self.supervisor.process('sync').config['tmdb']['api_key'], 'new')
        self.assertIs(self.supervisor.process('caption'), original_caption)

    async def test_live_patch_does_not_acquire_lifecycle_lock_and_compares_fields(self):
        import asyncio
        await self.install_download()
        identity = SimpleNamespace(plugin_id="download", active=True)
        async with self.manager._lifecycle_lock:
            result = self.manager._write_live_config(identity,
                {"access_token": "fresh"}, {"access_token": "old"})
        self.assertEqual(result["access_token"], "fresh")
        self.assertEqual(result["refresh_token"], "refresh")
        self.assertEqual(self.manager._active_configs["download"]["access_token"], "fresh")
        self.assertEqual(self.supervisor.process("download").config["access_token"], "fresh")
        with self.assertRaisesRegex(Exception, "changed"):
            self.manager._write_live_config(identity,
                {"access_token": "stale"}, {"access_token": "old"})
        identity.active = False
        with self.assertRaisesRegex(Exception, "authorized"):
            self.manager._write_live_config(identity,
                {"access_token": "retired"}, {"access_token": "fresh"})

    async def test_legacy_download_rejected_without_disrupting_existing_routes(self):
        await self.install_feature("echo")
        before = self.supervisor.process("echo")
        with self.assertRaisesRegex(Exception, "Upgrade Download"):
            await self.install_feature("download")
        self.assertIs(self.supervisor.process("echo"), before)
        self.assertIsNone(self.store.active("download"))

    async def test_old_host_rejects_new_bundle_before_install_or_update(self):
        from app.runtime.plugin_manager import PluginOperationError
        from tests.mixed_config_releases import release
        current = release(self.root, "sync")
        self.manager.host_api_version = "1.10"
        for operation in ("install", "update"):
            if operation == "update":
                await self.manager.install(release(self.root, "sync", old=True))
            before = {p.relative_to(self.store.root): p.read_bytes() for p in self.store.root.rglob("*") if p.is_file()}
            original, process, launches = self.path.read_bytes(), self.supervisor.process("sync"), len(self.supervisor.instances)
            with self.assertRaises(PluginOperationError) as error:
                await getattr(self.manager, operation)(current)
            self.assertEqual(error.exception.code, "incompatible_host")
            self.assertIn("1.11", str(error.exception))
            self.assertIn("1.10", str(error.exception))
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(before, {p.relative_to(self.store.root): p.read_bytes() for p in self.store.root.rglob("*") if p.is_file()})
            self.assertEqual(len(self.supervisor.instances), launches)
            self.assertIs(self.supervisor.process("sync"), process)
            if process:
                self.assertIs(self.router.plugin_route("sync").client, process.client)
        self.manager.host_api_version = "1.11"
        await self.manager.update(current)

    async def test_enable_stabilizes_candidate_once_before_promotion(self):
        await self.install_feature("echo")
        await self.manager.disable("echo")
        states = []
        health = self.supervisor.health
        async def check(process):
            states.append(process.shadow)
            return await health(process)
        self.supervisor.health = check
        await self.manager.enable("echo")
        self.assertEqual(states, [True])

    async def test_disabled_consumer_validates_but_does_not_start(self):
        for name in ("search", "rename"):
            await self.install_feature(name)
        await self.manager.disable("rename")
        release = self.store.active("rename")
        schema = release.path / "config.schema.json"
        schema.write_text('{"type":"object","properties":{"category_folder":{"maxItems":0}}}')
        before = self.path.read_text()
        with self.assertRaises(Exception):
            await self.manager.configure_shared({"category_folder": category_routes("/new")})
        self.assertEqual(self.path.read_text(), before)
        self.assertIsNone(self.supervisor.process("rename"))
        schema.write_text('{"type":"object"}')
        await self.manager.configure_shared({"category_folder": category_routes("/new")})
        self.assertIsNone(self.supervisor.process("rename"))

    async def test_invalid_manual_yaml_keeps_old_route_and_returns_config_state(self):
        await self.install_feature("echo")
        old = self.supervisor.process("echo")
        self.path.write_text("features: []\n")
        self.assertEqual(self.manager.config_state("echo")["state"], "invalid_config")
        with self.assertRaises(Exception):
            await self.manager.reload_all_configs()
        self.assertIs(self.supervisor.process("echo"), old)
        self.assertIs(self.router.plugin_route("echo").client, old.client)

    async def test_raw_patch_retains_token_refreshed_during_candidate_start(self):
        await self.install_feature("echo")
        old = self.supervisor.process("echo")
        def refresh(_release):
            self.central.commit(self.central.prepare_patch("echo", {"access_token": "fresh"}))
        self.supervisor.on_start = refresh
        from app.runtime.plugin_manager import PluginOperationError
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.configure_patch("echo", {"prefix": "new"})
        self.assertEqual(raised.exception.code, "config_conflict")
        self.assertEqual(self.store.read_config(self.store.active("echo"))["access_token"], "fresh")
        self.assertNotIn("prefix", self.central.read()["features"]["echo"])
        self.assertIs(self.supervisor.process("echo"), old)
        self.assertEqual(self.supervisor.instances[-1].launch_config["access_token"], "old")

    async def test_install_update_enable_and_rollback_reject_stale_launch_configuration(self):
        from app.runtime.plugin_manager import PluginOperationError
        def edit(_release):
            self.central.commit(self.central.prepare_patch("echo", {"prefix": "external"}))
        self.supervisor.on_start = edit
        with self.assertRaises(PluginOperationError) as raised:
            await self.install_feature("echo")
        self.assertEqual(raised.exception.code, "config_conflict")
        self.assertIsNone(self.store.active("echo"))
        self.supervisor.on_start = None
        self.store.remove_plugin("echo")
        await self.manager.install(self.root / "echo-1.0.0.tpx")
        await self.manager.disable("echo")
        self.central.commit(self.central.prepare_patch("echo", {"prefix": "before"}))
        self.supervisor.on_start = edit
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.enable("echo")
        self.assertEqual(raised.exception.code, "config_conflict")
        self.assertIsNone(self.supervisor.process("echo"))
        self.supervisor.on_start = None
        await self.manager.enable("echo")
        artifact = self._artifact("echo", "1.1.0", provides=(("echo.test", True),),
            commands=("echo",), config_schema={"type": "object"}, config_default={"prefix": "default"})
        self.central.commit(self.central.prepare_patch("echo", {"prefix": "before"}))
        old = self.supervisor.process("echo")
        self.supervisor.on_start = edit
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(artifact)
        self.assertEqual(raised.exception.code, "config_conflict")
        self.assertIs(self.supervisor.process("echo"), old)
        self.supervisor.on_start = None
        await self.manager.update(self._artifact("echo", "1.2.0", provides=(("echo.test", True),),
            commands=("echo",), config_schema={"type": "object"}, config_default={"prefix": "default"}))
        old = self.supervisor.process("echo")
        self.central.commit(self.central.prepare_patch("echo", {"prefix": "before"}))
        self.supervisor.on_start = edit
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.rollback("echo")
        self.assertEqual(raised.exception.code, "config_conflict")
        self.assertIs(self.supervisor.process("echo"), old)
        self.assertEqual(self.central.read()["features"]["echo"]["prefix"], "external")

    async def test_shared_stabilization_edit_rejects_all_candidates(self):
        from app.runtime.plugin_manager import PluginOperationError
        for name in ("search", "rename", "sync"):
            await self.install_feature(name)
        old = dict(self.supervisor.active)
        health = self.supervisor.health
        async def edit(process):
            if process.plugin_id == "sync":
                self.central.commit(self.central.prepare_patch("search", {"prefix": "external"}))
            return await health(process)
        self.supervisor.health = edit
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.configure_shared({"category_folder": category_routes("/new")})
        self.assertEqual(raised.exception.code, "config_conflict")
        self.assertNotIn("category_folder", self.central.read()["shared"])
        self.assertEqual(self.central.read()["features"]["search"]["prefix"], "external")
        for name, process in old.items():
            self.assertIs(self.supervisor.process(name), process)
            self.assertIs(self.router.plugin_route(name).client, process.client)
            self.assertEqual(process.state, "healthy")

    async def test_shared_draft_expected_rejects_external_other_category_change(self):
        from app.runtime.plugin_manager import PluginOperationError
        await self.install_feature("search")
        original = self.central.effective("search", {})["category_folder"]
        draft = deepcopy(original)
        draft[0]["path"] = "/chosen"
        external = deepcopy(original)
        external[1]["path"] = "/other-newer"
        self.central.commit(self.central.prepare_document_patch({"shared": {"category_folder": external}}))
        before = self.path.read_text()
        count = len(self.supervisor.instances)
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.configure_shared({"category_folder": draft},
                expected={"category_folder": original})
        self.assertEqual(raised.exception.code, "config_conflict")
        self.assertEqual(raised.exception.details["config_error_paths"], ["shared.category_folder"])
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(len(self.supervisor.instances), count)
        self.assertEqual(self.central.read()["shared"]["category_folder"][1]["path"], "/other-newer")

    async def test_shared_draft_expected_accepts_missing_canonical_defaults(self):
        original = self.central.effective("search", {})["category_folder"]
        draft = deepcopy(original)
        draft[0]["path"] = "/chosen"
        await self.manager.configure_shared({"category_folder": draft, "tmdb": {"api_key": "new"}},
            expected={"category_folder": original, "tmdb": {"api_key": ""}})
        self.assertEqual(self.central.read()["shared"]["category_folder"], draft)
        self.assertEqual(self.central.read()["shared"]["tmdb"]["api_key"], "new")

    async def test_shared_draft_expected_is_compared_after_acquiring_lifecycle_lock(self):
        import asyncio
        from app.runtime.plugin_manager import PluginOperationError
        async with self.manager._lifecycle_lock:
            task = asyncio.create_task(self.manager.configure_shared({"tmdb": {"api_key": "draft"}},
                expected={"tmdb": {"api_key": ""}}))
            await asyncio.sleep(0)
            self.central.commit(self.central.prepare_document_patch({"shared": {"tmdb": {"api_key": "newer"}}}))
        with self.assertRaises(PluginOperationError) as raised:
            await task
        self.assertEqual(raised.exception.code, "config_conflict")
        self.assertEqual(raised.exception.details["config_error_paths"], ["shared.tmdb.api_key"])
        self.assertEqual(self.central.read()["shared"]["tmdb"]["api_key"], "newer")

    async def strict_upgrade(self):
        schema = {"type": "object", "properties": {"prefix": {"type": "string"}},
                  "additionalProperties": False}
        await self.manager.install(self._artifact("echo", config_schema=schema,
            config_default={"prefix": "old"}))
        schema["properties"]["new_setting"] = {"type": "integer"}
        await self.manager.update(self._artifact("echo", "1.1.0", config_schema=schema,
            config_default={"prefix": "new", "new_setting": 7}))

    async def test_strict_rollback_discards_new_release_ephemeral_defaults(self):
        await self.strict_upgrade()
        await self.manager.rollback("echo")
        self.assertEqual(self.supervisor.process("echo").launch_config, {"prefix": "old"})

    async def test_strict_rollback_reports_explicit_incompatible_config_path(self):
        from app.runtime.plugin_manager import PluginOperationError
        await self.strict_upgrade()
        await self.manager.configure_patch("echo", {"new_setting": 9})
        old = self.supervisor.process("echo")
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.rollback("echo")
        self.assertEqual(raised.exception.details["config_error_paths"], ["new_setting"])
        self.assertIs(self.supervisor.process("echo"), old)

    async def test_candidate_snapshot_contains_credentials_refreshed_during_drain(self):
        await self.install_feature("echo")
        drain = self.supervisor.drain
        async def refresh(process, timeout):
            self.central.commit(self.central.prepare_patch("echo", {"access_token": "fresh"}))
            return await drain(process, timeout)
        self.supervisor.drain = refresh
        await self.manager.configure_patch("echo", {"prefix": "new"})
        self.assertEqual(self.supervisor.process("echo").config["access_token"], "fresh")

    async def test_shared_switches_all_consumers_with_prepared_snapshots(self):
        for name in ("search", "rename", "sync"):
            await self.install_feature(name)
        await self.manager.configure_shared({"category_folder": category_routes("/new")})
        for name in ("search", "rename", "sync"):
            self.assertEqual(self.supervisor.process(name).config["category_folder"][0]["path"], "/new")

    async def test_shared_child_failure_preserves_every_old_process_and_yaml(self):
        for name in ("search", "rename", "sync"):
            await self.install_feature(name)
        old = dict(self.supervisor.active)
        before = self.path.read_text()
        self.supervisor.fail_plugin = "sync"
        with self.assertRaises(Exception):
            await self.manager.configure_shared({"category_folder": category_routes("/new")})
        self.assertEqual(self.path.read_text(), before)
        for name, process in old.items():
            self.assertIs(self.supervisor.process(name), process)
            self.assertEqual(process.state, "healthy")
            self.assertIs(self.router.plugin_route(name).client, process.client)

    async def test_cancel_after_candidate_start_has_no_authoritative_write(self):
        await self.install_feature("echo")
        before = self.path.read_text()
        cancelled = False
        def cancel(_release):
            nonlocal cancelled
            cancelled = True
        self.supervisor.on_start = cancel
        with self.assertRaisesRegex(Exception, "cancelled"):
            await self.manager.configure_patch("echo", {"prefix": "new"}, should_cancel=lambda: cancelled)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(self.supervisor.process("echo").state, "healthy")

    async def test_code_rollback_preserves_current_settings(self):
        await self.install_feature("echo")
        await self.manager.update(self._artifact("echo", "1.1.0", provides=(("echo.test", True),),
            commands=("echo",), config_schema={"type": "object"}, config_default={"prefix": "old"}))
        await self.manager.configure_patch("echo", {"prefix": "latest", "access_token": "fresh"})
        await self.manager.rollback("echo")
        self.assertEqual(self.supervisor.process("echo").config["prefix"], "latest")
        self.assertEqual(self.store.read_config(self.store.active("echo"))["access_token"], "fresh")

    async def test_failed_update_does_not_commit_migration_defaults(self):
        await self.install_feature("echo")
        before = self.path.read_text()
        old = self.supervisor.process("echo")
        artifact = self._artifact("echo", "1.1.0", provides=(("echo.test", True),),
            commands=("echo",), config_schema={"type": "object"},
            config_default={"prefix": "old", "new_setting": 7})
        self.supervisor.unhealthy_versions.add("1.1.0")
        with self.assertRaises(Exception):
            await self.manager.update(artifact)
        self.assertEqual(self.path.read_text(), before)
        self.assertIs(self.supervisor.process("echo"), old)
        self.assertEqual(old.state, "healthy")
        self.assertEqual(self.store.active("echo").version, "1.0.0")

    async def test_update_changing_package_default_keeps_effective_settings_consistent(self):
        await self.install_feature("echo")
        await self.manager.update(self._artifact("echo", "1.1.0", provides=(("echo.test", True),),
            commands=("echo",), config_schema={"type": "object"},
            config_default={"prefix": "new package default"}))
        snapshot = self.supervisor.process("echo").config
        effective = self.store.read_config(self.store.active("echo"))
        self.assertEqual(snapshot["prefix"], "new package default")
        self.assertEqual(effective["prefix"], snapshot["prefix"])

    async def test_rollback_storage_failure_still_restores_old_process_and_route(self):
        await self.install_feature("echo")
        old = self.supervisor.process("echo")
        promote = self.supervisor.promote
        def fail_candidate(process):
            if process is not old:
                raise RuntimeError("promotion failed")
            return promote(process)
        def fail_rollback(_change):
            raise OSError("storage unavailable")
        self.supervisor.promote = fail_candidate
        self.central.rollback = fail_rollback
        with self.assertRaises(Exception):
            await self.manager.configure_patch("echo", {"prefix": "new"})
        self.assertIs(self.supervisor.process("echo"), old)
        self.assertEqual(old.state, "healthy")
        self.assertIs(self.router.plugin_route("echo").client, old.client)
