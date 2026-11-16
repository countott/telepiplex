"""Upgrade dependency preflight and immutable failed-candidate recovery."""
import json
import unittest

from app.runtime.plugin_artifact import build_tpx
from app.runtime.plugin_manager import PluginOperationError
from tests import test_central_config_lifecycle as central_helpers


class UpgradeRecoveryTest(unittest.IsolatedAsyncioTestCase):
    # Reuse setup, not the lifecycle test cases themselves.
    asyncSetUp = central_helpers.CentralLifecycleTest.asyncSetUp
    asyncTearDown = central_helpers.CentralLifecycleTest.asyncTearDown
    _artifact = central_helpers.CentralLifecycleTest._artifact
    _install_venv = central_helpers.CentralLifecycleTest._install_venv

    def artifact(self, name, version, *, requires=(), provides=None, **kwargs):
        path = self._artifact(name, version, requires=requires,
            provides=provides or ((f"{name}.test", True),), commands=(name,),
            config_schema={"type": "object"}, config_default={"prefix": "default"}, **kwargs)
        if name == "download":
            import yaml
            source = self.root / f"source-{name}-{version}"
            manifest_path = source / "manifest.yaml"
            manifest = yaml.safe_load(manifest_path.read_text())
            manifest["config_write_mode"] = "host_rpc_v1"
            manifest_path.write_text(yaml.safe_dump(manifest))
            path = build_tpx(source, path)
        return path

    async def commit_orphan(self, artifact):
        verified = self.manager._verify(artifact, "")
        staged = self.store.stage(verified)
        await self._install_venv(staged)
        return self.store.commit(staged)

    def tree(self, path):
        return {str(item.relative_to(path)): item.read_bytes()
                for item in path.rglob("*") if item.is_file()}

    async def test_wrong_order_install_does_not_commit_or_start_candidate(self):
        artifact = self.artifact("search", "2.11.2", requires=("download.provider",))
        before = self.path.read_bytes()
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.install(artifact)
        self.assertEqual(raised.exception.code, "missing_capability")
        self.assertIn("download.provider", str(raised.exception))
        self.assertFalse((self.store.root / "search").exists())
        self.assertEqual(list(self.store.staging_root.iterdir()), [])
        self.assertEqual(self.supervisor.instances, [])
        self.assertEqual(self.router.snapshot.plugin_ids, ())
        self.assertEqual(self.path.read_bytes(), before)

    async def test_wrong_order_update_keeps_active_routes_config_and_disk(self):
        await self.manager.install(self.artifact("search", "2.11.1"))
        await self.manager.configure_patch("search", {"prefix": "operator"})
        old = self.supervisor.process("search")
        before = self.tree(self.store.root)
        config = self.path.read_bytes()
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.update(self.artifact("search", "2.11.2",
                requires=("download.provider",)))
        self.assertEqual(raised.exception.code, "missing_capability")
        self.assertEqual(self.tree(self.store.root), before)
        self.assertEqual(self.path.read_bytes(), config)
        self.assertEqual(len(self.supervisor.instances), 2)
        self.assertIs(self.supervisor.process("search"), old)
        self.assertIs(self.router.plugin_route("search").client, old.client)
        self.assertEqual(old.state, "healthy")

    async def test_preexisting_search_and_rename_orphans_recover_in_dependency_order(self):
        # Model a Host restart with recorded old releases but no live providers.
        download_old = self._artifact("download", "2.2.5", commands=("download",),
            provides=(("download.provider", True),), config_schema={"type": "object"})
        search_old = self.artifact("search", "2.11.1", requires=("download.provider",),
                                   provides=(("media.search", True),))
        rename_old = self.artifact("rename", "2.4.5", requires=("media.search",))
        for artifact in (download_old, search_old, rename_old):
            release = await self.commit_orphan(artifact)
            self.store.set_active(release)
        restored = await self.manager.restore_active()
        self.assertEqual({item.plugin_id: item.details["code"] for item in restored},
            {"download": "config_upgrade_required", "search": "missing_capability",
             "rename": "missing_capability"})
        for name in ("download", "search", "rename"):
            self.assertTrue(self.store.active(name).enabled)
            self.assertIsNone(self.supervisor.process(name))
            self.assertIsNone(self.router.plugin_route(name))
        for name in ("search", "rename"):
            self.central.commit(self.central.prepare_patch(name, {"prefix": "operator"}))
        search = self.artifact("search", "2.11.2", requires=("download.provider",),
                               provides=(("media.search", True),))
        rename = self.artifact("rename", "2.4.6", requires=("media.search",))
        search_release = await self.commit_orphan(search)
        rename_release = await self.commit_orphan(rename)
        original = self.tree(search_release.path), self.tree(rename_release.path)
        state = self.store.root / "rename/state/sentinel"
        state.write_text("preserve")
        await self.manager.update(self.artifact("download", "2.3.0",
            provides=(("download.provider", True),)))
        await self.manager.update(search)
        await self.manager.update(rename)
        self.assertEqual(self.store.active("search").version, "2.11.2")
        self.assertEqual(self.store.active("rename").version, "2.4.6")
        self.assertEqual((self.tree(search_release.path), self.tree(rename_release.path)), original)
        self.assertEqual(state.read_text(), "preserve")
        for name in ("search", "rename"):
            self.assertEqual(self.supervisor.process(name).config["prefix"], "operator")
            self.assertIs(self.router.plugin_route(name).client, self.supervisor.process(name).client)

    async def test_retry_failed_health_preserves_old_activation_then_succeeds(self):
        await self.manager.install(self.artifact("echo", "1.0.0"))
        await self.manager.configure_patch("echo", {"prefix": "operator"})
        old = self.supervisor.process("echo")
        artifact = self.artifact("echo", "1.1.0")
        before = self.path.read_bytes()
        self.supervisor.unhealthy_versions.add("1.1.0")
        for _ in range(2):
            with self.assertRaises(PluginOperationError) as raised:
                await self.manager.update(artifact)
            self.assertEqual(raised.exception.code, "stabilization_failed")
            self.assertEqual(self.path.read_bytes(), before)
            self.assertIs(self.supervisor.process("echo"), old)
            self.assertIs(self.router.plugin_route("echo").client, old.client)
            self.assertEqual(old.state, "healthy")
            self.assertEqual(self.store.active("echo").version, "1.0.0")
        self.supervisor.unhealthy_versions.clear()
        await self.manager.update(artifact)
        self.assertEqual(self.store.active("echo").version, "1.1.0")
        self.assertEqual(self.supervisor.process("echo").config["prefix"], "operator")

    async def test_first_install_failed_activation_can_retry_same_artifact(self):
        artifact = self.artifact("echo", "1.0.0")
        self.supervisor.unhealthy_versions.add("1.0.0")
        with self.assertRaises(PluginOperationError):
            await self.manager.install(artifact)
        self.assertIsNone(self.store.active("echo"))
        self.supervisor.unhealthy_versions.clear()
        await self.manager.install(artifact)
        self.assertEqual(self.store.active("echo").version, "1.0.0")

    async def test_same_version_different_artifact_cannot_replace_orphan(self):
        artifact = self.artifact("echo", "1.0.0")
        release = await self.commit_orphan(artifact)
        original = self.tree(release.path)
        source = self.root / "source-echo-1.0.0"
        (source / "plugin.whl").write_bytes(b"different artifact")
        different = build_tpx(source, self.root / "different.tpx")
        with self.assertRaises(PluginOperationError) as raised:
            await self.manager.install(different)
        self.assertEqual(raised.exception.code, "release_exists")
        self.assertEqual(self.tree(release.path), original)
        self.assertIsNone(self.store.active("echo"))

    async def test_missing_corrupt_metadata_or_payload_cannot_be_reused(self):
        for index, corruption in enumerate(("missing", "invalid", "hash", "manifest", "payload", "venv")):
            with self.subTest(corruption=corruption):
                name = f"echo{index}"
                artifact = self.artifact(name, "1.0.0")
                release = await self.commit_orphan(artifact)
                metadata = release.path / ".artifact.json"
                if corruption == "missing":
                    metadata.unlink()
                elif corruption == "invalid":
                    metadata.write_text("[]")
                elif corruption == "hash":
                    metadata.write_text(json.dumps({"artifact_sha256": "0" * 64}))
                elif corruption == "manifest":
                    (release.path / "manifest.yaml").write_text("not: a manifest")
                elif corruption == "payload":
                    (release.path / "plugin.whl").write_bytes(b"corrupt")
                else:
                    (release.path / "venv/bin/python").unlink()
                before = self.tree(release.path)
                with self.assertRaises(PluginOperationError) as raised:
                    await self.manager.install(artifact)
                self.assertEqual(raised.exception.code, "release_exists")
                self.assertEqual(self.tree(release.path), before)
                self.assertIsNone(self.store.active(name))
