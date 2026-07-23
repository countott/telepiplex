import unittest


class PluginManifestTest(unittest.TestCase):
    def _value(self):
        return {
            "plugin_id": "echo",
            "name": "Echo",
            "version": "1.2.3",
            "host_api": ">=1.0,<2.0",
            "entry_point": "telepiplex_echo.runtime:main",
            "provides": [{"name": "demo.echo", "exclusive": True}],
            "requires": ["runtime.clock"],
            "subscribes": ["demo.requested"],
            "publishes": ["demo.completed"],
            "commands": [{"name": "echo", "description": "Echo text"}],
            "callbacks": ["echo"],
            "config_schema_version": 1,
            "state_schema_version": 1,
            "source": {
                "repository": "origin",
                "branch": "feature/echo",
                "commit": "a" * 40,
            },
        }

    def test_valid_manifest_is_immutable_and_supports_host_api(self):
        from app.runtime.plugin_manifest import PluginManifest

        manifest = PluginManifest.from_mapping(self._value())

        self.assertEqual(manifest.plugin_id, "echo")
        self.assertEqual(manifest.version, "1.2.3")
        self.assertEqual(manifest.provides[0].name, "demo.echo")
        self.assertTrue(manifest.provides[0].exclusive)
        self.assertEqual(manifest.commands[0].name, "echo")
        self.assertTrue(manifest.supports_host("1.0"))
        self.assertTrue(manifest.supports_host("1.9"))
        self.assertFalse(manifest.supports_host("2.0"))
        with self.assertRaises(AttributeError):
            manifest.plugin_id = "changed"

    def test_command_menu_visibility_is_optional_and_boolean(self):
        from app.runtime.plugin_contract import ContractError
        from app.runtime.plugin_manifest import PluginManifest

        value = self._value()
        value["commands"] = [
            {
                "name": "visible",
                "description": "Visible task",
                "menu_visible": True,
            },
            {
                "name": "hidden",
                "description": "Hidden helper",
                "menu_visible": False,
            },
            {"name": "legacy", "description": "Legacy command"},
        ]

        manifest = PluginManifest.from_mapping(value)

        self.assertEqual(
            [command.menu_visible for command in manifest.commands],
            [True, False, None],
        )

        value["commands"][0]["menu_visible"] = "yes"
        with self.assertRaises(ContractError) as raised:
            PluginManifest.from_mapping(value)
        self.assertEqual(raised.exception.code, "invalid_manifest")

    def test_rejects_missing_identity_and_invalid_versions(self):
        from app.runtime.plugin_contract import ContractError
        from app.runtime.plugin_manifest import PluginManifest

        cases = []
        missing_id = self._value()
        missing_id.pop("plugin_id")
        cases.append(missing_id)
        bad_plugin_version = self._value()
        bad_plugin_version["version"] = "v1"
        cases.append(bad_plugin_version)
        bad_host_range = self._value()
        bad_host_range["host_api"] = "^1.0"
        cases.append(bad_host_range)
        bad_commit = self._value()
        bad_commit["source"]["commit"] = "latest"
        cases.append(bad_commit)

        for value in cases:
            with self.subTest(value=value), self.assertRaises(ContractError) as raised:
                PluginManifest.from_mapping(value)
            self.assertEqual(raised.exception.code, "invalid_manifest")

    def test_rejects_unsafe_entry_point_and_invalid_names(self):
        from app.runtime.plugin_contract import ContractError
        from app.runtime.plugin_manifest import PluginManifest

        for field, bad_value in (
            ("entry_point", "../../bad:main"),
            ("entry_point", "module;touch /tmp/x:main"),
            ("plugin_id", "Bad Plugin"),
        ):
            value = self._value()
            value[field] = bad_value
            with self.subTest(field=field, bad_value=bad_value):
                with self.assertRaises(ContractError):
                    PluginManifest.from_mapping(value)

        for collection, bad_value in (
            ("provides", [{"name": "Bad Capability", "exclusive": True}]),
            ("requires", ["../storage"]),
            ("subscribes", ["bad/event"]),
            ("publishes", [""]),
            ("commands", [{"name": "Bad Command", "description": "x"}]),
            ("callbacks", ["bad/callback"]),
        ):
            value = self._value()
            value[collection] = bad_value
            with self.subTest(collection=collection):
                with self.assertRaises(ContractError):
                    PluginManifest.from_mapping(value)

    def test_rejects_duplicate_declarations(self):
        from app.runtime.plugin_contract import ContractError
        from app.runtime.plugin_manifest import PluginManifest

        duplicate_cases = {
            "provides": [
                {"name": "demo.echo", "exclusive": True},
                {"name": "demo.echo", "exclusive": False},
            ],
            "requires": ["runtime.clock", "runtime.clock"],
            "subscribes": ["demo.requested", "demo.requested"],
            "publishes": ["demo.completed", "demo.completed"],
            "commands": [
                {"name": "echo", "description": "one"},
                {"name": "echo", "description": "two"},
            ],
            "callbacks": ["echo", "echo"],
        }
        for field, duplicate in duplicate_cases.items():
            value = self._value()
            value[field] = duplicate
            with self.subTest(field=field), self.assertRaises(ContractError):
                PluginManifest.from_mapping(value)

    def test_rejects_unknown_keys_and_non_positive_schema_versions(self):
        from app.runtime.plugin_contract import ContractError
        from app.runtime.plugin_manifest import PluginManifest

        unknown = self._value()
        unknown["magic"] = True
        with self.assertRaises(ContractError):
            PluginManifest.from_mapping(unknown)

        for field in ("config_schema_version", "state_schema_version"):
            value = self._value()
            value[field] = 0
            with self.subTest(field=field), self.assertRaises(ContractError):
                PluginManifest.from_mapping(value)


if __name__ == "__main__":
    unittest.main()
