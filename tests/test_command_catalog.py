import unittest


def manifest(plugin_id, *, name=None, commands=(), requires=(), provides=()):
    from app.core.plugin_manifest import PluginManifest

    return PluginManifest.from_mapping({
        "plugin_id": plugin_id,
        "name": name or plugin_id,
        "version": "1.1.0",
        "core_api": ">=1.1,<2.0",
        "entry_point": f"telepiplex_{plugin_id.replace('-', '_')}.runtime:main",
        "provides": [
            {"name": capability, "exclusive": True}
            for capability in provides
        ],
        "requires": list(requires),
        "subscribes": [],
        "publishes": [],
        "commands": [
            {"name": command, "description": description}
            for command, description in commands
        ],
        "callbacks": [],
        "source": {
            "repository": "origin",
            "branch": f"feature/{plugin_id}",
            "commit": "a" * 40,
        },
    })


class CommandCatalogTest(unittest.TestCase):
    def setUp(self):
        from app.core.capability_router import CapabilityRouter

        self.router = CapabilityRouter()

    def test_combines_core_and_active_feature_commands(self):
        from app.core.command_catalog import build_bot_commands

        self.router.activate(
            "open115",
            manifest("open115", commands=(
                ("magnet", "提交磁力链接"),
                ("config", "旧配置入口"),
                ("auth", "授权 115"),
            )),
            object(),
        )
        self.router.activate(
            "media-search",
            manifest("media-search", commands=(
                ("search", "搜索媒体"),
                ("s", "搜索媒体（简写）"),
            )),
            object(),
        )

        commands = build_bot_commands(self.router)
        names = [item.command for item in commands]

        self.assertEqual(names[:4], ["start", "reload", "plugin", "config"])
        self.assertEqual(
            names[4:],
            ["search", "s", "magnet", "auth"],
        )
        self.assertEqual(names.count("config"), 1)

    def test_blocked_and_deactivated_features_are_not_advertised(self):
        from app.core.command_catalog import build_bot_commands

        self.router.activate(
            "provider",
            manifest(
                "provider",
                commands=(("provide", "Provider"),),
                provides=("demo.provider",),
            ),
            object(),
        )
        self.router.activate(
            "consumer",
            manifest(
                "consumer",
                commands=(("consume", "Consumer"),),
                requires=("demo.provider",),
            ),
            object(),
        )
        self.router.deactivate("provider")

        names = [item.command for item in build_bot_commands(self.router)]

        self.assertNotIn("provide", names)
        self.assertNotIn("consume", names)

    def test_start_help_is_html_safe_and_preserves_manifest_order(self):
        from app.core.command_catalog import build_start_help

        self.router.activate(
            "echo",
            manifest(
                "echo",
                name="Echo <unsafe>",
                commands=(
                    ("second", "Second & safer"),
                    ("first", "First <tag>"),
                    ("start", "Override"),
                ),
            ),
            object(),
        )

        help_text = build_start_help(self.router, "v<1.1>")

        self.assertIn("v&lt;1.1&gt;", help_text)
        self.assertIn("Echo &lt;unsafe&gt;", help_text)
        self.assertIn("Second &amp; safer", help_text)
        self.assertIn("First &lt;tag&gt;", help_text)
        self.assertLess(help_text.index("/second"), help_text.index("/first"))
        self.assertEqual(help_text.count("<code>/start</code>"), 1)


if __name__ == "__main__":
    unittest.main()
