import json
import tempfile
import unittest
from pathlib import Path

import yaml


class PluginStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plugins_root = self.root / "plugins"

    def tearDown(self):
        self.temp.cleanup()

    def _artifact(self, version="1.0.0", commit="a" * 40):
        from app.core.plugin_artifact import build_tpx, verify_tpx

        source = self.root / f"source-{version}"
        (source / "wheelhouse").mkdir(parents=True)
        manifest = {
            "plugin_id": "echo",
            "name": "Echo",
            "version": version,
            "core_api": ">=1.0,<2.0",
            "entry_point": "telepiplex_echo.runtime:main",
            "provides": [{"name": "demo.echo", "exclusive": True}],
            "requires": [],
            "subscribes": [],
            "publishes": [],
            "commands": [{"name": "echo", "description": "Echo text"}],
            "callbacks": [],
            "source": {
                "repository": "origin",
                "branch": "feature/echo",
                "commit": commit,
            },
        }
        (source / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
        )
        (source / "plugin.whl").write_bytes(f"plugin-{version}".encode())
        (source / "wheelhouse" / "sdk.whl").write_bytes(b"sdk")
        (source / "config.schema.json").write_text(json.dumps({
            "type": "object",
            "properties": {"prefix": {"type": "string"}},
            "required": ["prefix"],
            "additionalProperties": False,
        }), encoding="utf-8")
        (source / "config.default.yaml").write_text("prefix: echo\n", encoding="utf-8")
        output = build_tpx(source, self.root / f"echo-{version}.tpx")
        return verify_tpx(output)

    def test_stage_and_activate_create_exact_persistent_layout(self):
        from app.core.plugin_store import PluginStore

        store = PluginStore(self.plugins_root)
        staged = store.stage(self._artifact())

        self.assertEqual(staged.plugin_id, "echo")
        self.assertEqual(staged.version, "1.0.0")
        self.assertEqual(staged.path.parent, (self.plugins_root / ".staging").resolve())
        self.assertTrue((staged.path / "manifest.yaml").is_file())

        active = store.activate(staged)

        expected_release = (self.plugins_root / "echo/releases/1.0.0").resolve()
        self.assertEqual(active.path, expected_release)
        self.assertTrue((expected_release / "plugin.whl").is_file())
        self.assertEqual(
            yaml.safe_load((self.plugins_root / "echo/config.yaml").read_text()),
            {"prefix": "echo"},
        )
        self.assertTrue((self.plugins_root / "echo/state").is_dir())
        record = json.loads((self.plugins_root / "echo/active.json").read_text())
        self.assertEqual(record["active_version"], "1.0.0")
        self.assertIsNone(record["previous_version"])
        self.assertEqual(store.active("echo").version, "1.0.0")

    def test_second_activation_preserves_previous_release_for_rollback(self):
        from app.core.plugin_store import PluginStore

        store = PluginStore(self.plugins_root)
        first = store.activate(store.stage(self._artifact("1.0.0", "a" * 40)))
        second = store.activate(store.stage(self._artifact("1.1.0", "b" * 40)))

        self.assertEqual(first.version, "1.0.0")
        self.assertEqual(second.version, "1.1.0")
        self.assertEqual(second.previous_version, "1.0.0")
        self.assertTrue((self.plugins_root / "echo/releases/1.0.0").is_dir())
        self.assertEqual(
            [item.version for item in store.list_installed()],
            ["1.0.0", "1.1.0"],
        )

    def test_existing_invalid_config_rejects_stage_and_cleans_staging(self):
        from app.core.plugin_store import PluginStore, StoreError

        plugin_root = self.plugins_root / "echo"
        plugin_root.mkdir(parents=True)
        (plugin_root / "config.yaml").write_text("unknown: true\n", encoding="utf-8")
        store = PluginStore(self.plugins_root)

        with self.assertRaises(StoreError) as raised:
            store.stage(self._artifact())

        self.assertEqual(raised.exception.code, "invalid_config")
        self.assertEqual(list((self.plugins_root / ".staging").glob("*")), [])
        self.assertFalse((plugin_root / "releases").exists())

    def test_malformed_yaml_config_is_reported_as_stable_store_error(self):
        from app.core.plugin_store import PluginStore, StoreError

        plugin_root = self.plugins_root / "echo"
        plugin_root.mkdir(parents=True)
        (plugin_root / "config.yaml").write_text("prefix: [", encoding="utf-8")
        store = PluginStore(self.plugins_root)

        with self.assertRaises(StoreError) as raised:
            store.stage(self._artifact())

        self.assertEqual(raised.exception.code, "invalid_config")
        self.assertEqual(list((self.plugins_root / ".staging").glob("*")), [])

    def test_validate_config_returns_copy_and_rejects_schema_violation(self):
        from app.core.plugin_store import PluginStore, StoreError

        store = PluginStore(self.plugins_root)
        active = store.activate(store.stage(self._artifact()))
        source = {"prefix": "ok"}

        validated = store.validate_config(active, source)
        validated["prefix"] = "changed"
        self.assertEqual(source["prefix"], "ok")
        with self.assertRaises(StoreError) as raised:
            store.validate_config(active, {"prefix": 123})
        self.assertEqual(raised.exception.code, "invalid_config")

    def test_corrupt_active_record_is_quarantined(self):
        from app.core.plugin_store import PluginStore

        store = PluginStore(self.plugins_root)
        store.activate(store.stage(self._artifact()))
        active_record = self.plugins_root / "echo/active.json"
        active_record.write_text("{bad", encoding="utf-8")

        self.assertIsNone(store.active("echo"))
        self.assertFalse(active_record.exists())
        quarantined = list((self.plugins_root / "echo").glob("active.corrupt.*.json"))
        self.assertEqual(len(quarantined), 1)


if __name__ == "__main__":
    unittest.main()
