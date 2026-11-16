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
