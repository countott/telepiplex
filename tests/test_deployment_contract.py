import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTest(unittest.TestCase):
    def _advanced_section(self, source, heading):
        match = re.search(
            rf"(?ms)^{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^#{{2,3}}\s|\Z)",
            source,
        )
        self.assertIsNotNone(match, heading)
        return match.group("body"), match.start()

    def test_image_contains_only_core_runtime_and_plugin_toolchain(self):
        source = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY ./app /app", source)
        self.assertIn("COPY ./sdk /opt/telepiplex/sdk", source)
        self.assertIn("COPY ./tools /opt/telepiplex/tools", source)
        self.assertIn("RUN mkdir -p /config/plugins /tmp/telepiplex", source)
        self.assertIn('VOLUME ["/config"]', source)
        self.assertNotIn("ADD ./app .", source)
        self.assertNotIn("COPY ./examples", source)

    def test_compose_runs_one_core_service_with_persistent_config_only(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        self.assertEqual(list(compose["services"]), ["telepiplex-core"])
        service = compose["services"]["telepiplex-core"]
        self.assertEqual(service["image"], "telepiplex-core:latest")
        self.assertEqual(service["volumes"], ["/to/your/path/config:/config"])
        self.assertNotIn("ports", service)

    def test_core_documentation_describes_runtime_feature_contract(self):
        for name in ("README.md", "README_EN.md"):
            source = (ROOT / name).read_text(encoding="utf-8")
            for term in (
                "/plugin install",
                "name@version",
                ".tpx",
                "/config/plugins",
                "Feature",
            ):
                self.assertIn(term, source, f"{name}: {term}")

    def test_build_script_only_references_existing_dockerfiles(self):
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        dockerfiles = re.findall(r"docker\s+build\s+-f\s+([^\s]+)", source)

        self.assertTrue(dockerfiles)
        for dockerfile in dockerfiles:
            self.assertTrue((ROOT / dockerfile).is_file(), dockerfile)

    def test_build_script_outputs_the_compose_image(self):
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        service = next(iter(compose["services"].values()))
        image = service["image"]

        self.assertIn(f"-t {image}", source)
        self.assertIn(f"docker image inspect {image}", source)

    def test_documentation_describes_independent_release_contract(self):
        chinese_required = (
            "ghcr.io/<owner>/telepiplex-core",
            "core-v1.0.7",
            "同名 GitHub Release",
            "强制设为 **Latest**",
            "open115-v1.0.2",
            "media-search-v1.0.2",
            "renaming-v1.0.2",
            "plex-management-v1.0.2",
            "`catalog` 分支",
            "catalog.yaml",
            "Feature version",
            "1.0.1",
            "不会产生 Telegram 更新通知",
            "不会静默更新",
        )
        chinese = (ROOT / "README.md").read_text(encoding="utf-8")
        for term in chinese_required:
            self.assertIn(term, chinese, term)

        english = (ROOT / "README_EN.md").read_text(encoding="utf-8")
        for term in (
            "ghcr.io/<owner>/telepiplex-core",
            "core-v1.0.7",
            "same-tag GitHub Release",
            "explicitly marked **Latest**",
            "open115-v1.0.2",
            "media-search-v1.0.2",
            "renaming-v1.0.2",
            "plex-management-v1.0.2",
            "`catalog` branch",
            "catalog.yaml",
            "Feature version",
            "1.0.1",
            "does not produce a Telegram update notification",
            "never updates silently",
        ):
            self.assertIn(term, english, term)

        decisions = (
            ROOT / "docs/todos/2026-07-12-business-module-decisions.md"
        ).read_text(encoding="utf-8")
        self.assertIn("OPS-TODO-01A GitHub 聚合发布（已实现）", decisions)
        self.assertIn("OPS-TODO-01B 远程更新发现（已实现）", decisions)
        self.assertIn("GitHub 聚合发布流水线已经落地", decisions)
        self.assertNotIn("GitHub 自动发布 Core 镜像、Feature `.tpx` 和远程 catalog 尚未落地", decisions)

    def test_documentation_describes_remote_update_discovery(self):
        preferred_catalog = (
            "https://raw.githubusercontent.com/countott/telepiplex/"
            "catalog/catalog.yaml"
        )
        chinese = (ROOT / "README.md").read_text(encoding="utf-8")
        for term in (
            preferred_catalog,
            "catalog_refresh_interval: 21600",
            "确认更新",
            "/config/plugins/catalog.yaml",
            "不会静默更新",
        ):
            self.assertIn(term, chinese, term)
        self.assertNotIn("releases/latest/download/catalog.yaml", chinese)

        english = (ROOT / "README_EN.md").read_text(encoding="utf-8")
        for term in (
            preferred_catalog,
            "catalog_refresh_interval: 21600",
            "Confirm update",
            "/config/plugins/catalog.yaml",
            "never updates silently",
        ):
            self.assertIn(term, english, term)
        self.assertNotIn("releases/latest/download/catalog.yaml", english)

    def test_documentation_describes_click_only_feature_catalog_flow(self):
        chinese = (ROOT / "README.md").read_text(encoding="utf-8")
        for term in (
            "发送 `/plugin`",
            "安装按钮和更新按钮都绑定该 Feature 的最新稳定兼容版本",
            "只有依赖满足的 ready 候选才显示安装按钮",
            "旧版默认 catalog 是 `<plugins.root>/catalog.yaml`",
            "仅当这个 legacy 文件缺失时，Core 才回退到官方 URL",
            "已存在的 legacy 文件继续使用本地目录",
            "其他显式本地路径即使当前文件缺失，也保持本地配置意图",
            "不会自动安装",
        ):
            with self.subTest(readme="README.md", term=term):
                self.assertIn(term, chinese)

        chinese_advanced, chinese_advanced_start = self._advanced_section(
            chinese,
            "### 高级/离线操作",
        )
        for command in (
            "/plugin install <name@version|artifact.tpx>",
            "/plugin update <name@version|artifact.tpx>",
            "/plugin install media-search@1.2.0",
            "/plugin update media-search@1.3.0",
        ):
            with self.subTest(readme="README.md", advanced_command=command):
                self.assertIn(command, chinese_advanced)
                self.assertNotIn(command, chinese[:chinese_advanced_start])
                self.assertEqual(
                    chinese.count(command),
                    chinese_advanced.count(command),
                )

        english = (ROOT / "README_EN.md").read_text(encoding="utf-8")
        for term in (
            "Send `/plugin`",
            "Install and Update buttons target that Feature's newest stable, Core-compatible release",
            "Only dependency-satisfied, ready candidates receive an Install button",
            "The legacy default catalog is `<plugins.root>/catalog.yaml`",
            "Core falls back to the official URL only when that legacy file is missing",
            "An existing legacy file remains local",
            "every other explicit local path preserves its local configuration intent even when its file is missing",
            "never installs automatically",
        ):
            with self.subTest(readme="README_EN.md", term=term):
                self.assertIn(term, english)

        english_advanced, english_advanced_start = self._advanced_section(
            english,
            "### Advanced/offline operations",
        )
        for command in (
            "/plugin install <name@version|artifact.tpx>",
            "/plugin update <name@version|artifact.tpx>",
            "/plugin install media-search@1.2.0",
            "/plugin update media-search@1.3.0",
        ):
            with self.subTest(readme="README_EN.md", advanced_command=command):
                self.assertIn(command, english_advanced)
                self.assertNotIn(command, english[:english_advanced_start])
                self.assertEqual(
                    english.count(command),
                    english_advanced.count(command),
                )

        decisions = (
            ROOT / "docs/todos/2026-07-12-business-module-decisions.md"
        ).read_text(encoding="utf-8")
        self.assertIn("OPS-TODO-02 首次安装体验（已实现）", decisions)
        self.assertNotIn("OPS-TODO-02 首次安装体验\n", decisions)
        self.assertIn("安装按钮和更新按钮", decisions)
        self.assertIn(
            "旧版默认 catalog `<plugins.root>/catalog.yaml` 缺失时回退到官方远程 catalog",
            decisions,
        )
        self.assertIn(
            "其他显式本地路径即使当前文件缺失也保持本地配置意图",
            decisions,
        )


if __name__ == "__main__":
    unittest.main()
