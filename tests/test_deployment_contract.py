import os
import re
import shlex
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTest(unittest.TestCase):
    def _docker_instructions(self):
        instructions = []
        pending = ""
        for raw_line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            pending += line.rstrip("\\").rstrip() + " "
            if line.endswith("\\"):
                continue
            instruction, value = pending.strip().split(None, 1)
            instructions.append((instruction.upper(), value))
            pending = ""
        self.assertEqual(pending, "")
        return instructions

    def _advanced_section(self, source, heading):
        match = re.search(
            rf"(?ms)^{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^#{{2,3}}\s|\Z)",
            source,
        )
        self.assertIsNotNone(match, heading)
        return match.group("body"), match.start()

    def test_image_contains_only_host_runtime_and_plugin_toolchain(self):
        source = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY ./app /app", source)
        self.assertIn("COPY ./sdk /opt/telepiplex/sdk", source)
        self.assertIn("COPY ./tools /opt/telepiplex/tools", source)
        self.assertIn("RUN mkdir -p /config/plugins /tmp/telepiplex", source)
        self.assertIn('VOLUME ["/config"]', source)
        self.assertNotIn("ADD ./app .", source)
        self.assertNotIn("COPY ./examples", source)

    def test_release_image_receives_and_exports_the_release_commit(self):
        instructions = self._docker_instructions()
        arguments = {}
        environment = {}
        for instruction, value in instructions:
            if instruction == "ARG":
                key, default = value.split("=", 1)
                arguments[key] = default
            elif instruction == "ENV":
                environment.update(
                    token.split("=", 1) for token in shlex.split(value) if "=" in token
                )

        self.assertEqual(arguments["TELEPIPLEX_COMMIT"], "unknown")
        self.assertEqual(environment["TELEPIPLEX_COMMIT"], "${TELEPIPLEX_COMMIT}")

        workflow = yaml.load(
            (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        steps = workflow["jobs"]["build-telepiplex-image"]["steps"]
        build = next(
            step for step in steps
            if step.get("uses") == "docker/build-push-action@v7"
        )
        build_args = dict(
            line.strip().split("=", 1)
            for line in build["with"]["build-args"].splitlines()
            if line.strip()
        )
        self.assertEqual(build_args["TELEPIPLEX_COMMIT"], "${{ github.sha }}")

    def test_compose_runs_one_host_service_with_persistent_config_only(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        self.assertEqual(list(compose["services"]), ["telepiplex"])
        service = compose["services"]["telepiplex"]
        self.assertEqual(service["volumes"], ["/to/your/path/config:/config"])
        self.assertNotIn("ports", service)

    def test_compose_defaults_to_the_official_latest_release(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        service = compose["services"]["telepiplex"]

        self.assertEqual(
            service["image"],
            "${TELEPIPLEX_IMAGE:-ghcr.io/countott/telepiplex:latest}",
        )
        self.assertEqual(
            service["pull_policy"],
            "${TELEPIPLEX_PULL_POLICY:-always}",
        )

    def test_host_documentation_describes_runtime_feature_contract(self):
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

    def test_operation_milestones_are_declared_as_host_api_1_6(self):
        from app.runtime.plugin_contract import HOST_API_VERSION

        self.assertEqual(HOST_API_VERSION, "1.6")
        for name in ("README.md", "README_EN.md"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("Host API 1.5", source)
            self.assertIn("Host API 1.6", source)

    def test_plugin_sdk_release_identity_is_1_3_1(self):
        project = tomllib.loads(
            (ROOT / "sdk" / "pyproject.toml").read_text(encoding="utf-8")
        )

        self.assertEqual(project["project"]["version"], "1.3.1")

    def test_build_script_only_references_existing_dockerfiles(self):
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        dockerfiles = re.findall(r"docker\s+build\s+-f\s+([^\s]+)", source)

        self.assertTrue(dockerfiles)
        for dockerfile in dockerfiles:
            self.assertTrue((ROOT / dockerfile).is_file(), dockerfile)

    def test_build_script_preserves_the_local_unraid_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fakebin = root / "bin"
            fakebin.mkdir()
            docker = fakebin / "docker"
            docker.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            log = root / "docker.log"
            env = os.environ.copy()
            env["PATH"] = f"{fakebin}:{env['PATH']}"
            env["DOCKER_LOG"] = str(log)

            result = subprocess.run(
                ["bash", str(ROOT / "build.sh")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                [
                    "build -f Dockerfile -t telepiplex:latest .",
                    (
                        "image inspect telepiplex:latest --format "
                        "Image: {{.RepoTags}} Size: {{.Size}} bytes"
                    ),
                ],
            )

    def test_documentation_describes_independent_release_contract(self):
        chinese_required = (
            "ghcr.io/<owner>/telepiplex",
            "telepiplex-v<semver>",
            "远端 `main`",
            "普通 `main` push",
            "同名 GitHub Release",
            "强制设为 **Latest**",
            "download-v1.0.0",
            "search-v1.0.0",
            "rename-v1.0.0",
            "sync-v1.0.0",
            "caption-v0.1.0",
            "`catalog` 分支",
            "catalog.yaml",
            "Feature version",
            "全新技术身份",
            "不会静默更新",
        )
        chinese = (ROOT / "README.md").read_text(encoding="utf-8")
        for term in chinese_required:
            self.assertIn(term, chinese, term)

        english = (ROOT / "README_EN.md").read_text(encoding="utf-8")
        for term in (
            "ghcr.io/<owner>/telepiplex",
            "telepiplex-v<semver>",
            "remote `main`",
            "ordinary `main` push",
            "same-tag GitHub Release",
            "explicitly marked **Latest**",
            "download-v1.0.0",
            "search-v1.0.0",
            "rename-v1.0.0",
            "sync-v1.0.0",
            "caption-v0.1.0",
            "`catalog` branch",
            "catalog.yaml",
            "Feature version",
            "new technical identities",
            "never updates silently",
        ):
            self.assertIn(term, english, term)

        decisions = (
            ROOT / "docs/todos/2026-07-12-business-module-decisions.md"
        ).read_text(encoding="utf-8")
        self.assertIn("OPS-TODO-01A GitHub 聚合发布（已实现）", decisions)
        self.assertIn("OPS-TODO-01B 远程更新发现（已实现）", decisions)
        self.assertIn("GitHub 聚合发布流水线已经落地", decisions)
        self.assertNotIn("GitHub 自动发布 Host 镜像、Feature `.tpx` 和远程 catalog 尚未落地", decisions)

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
            "仅当这个 legacy 文件缺失时，telepiplex 才回退到官方 URL",
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
            "/plugin install search@1.0.0",
            "/plugin update search@1.0.0",
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
            "Install and Update buttons target that Feature's newest stable, Host-compatible release",
            "Only dependency-satisfied, ready candidates receive an Install button",
            "The legacy default catalog is `<plugins.root>/catalog.yaml`",
            "telepiplex falls back to the official URL only when that legacy file is missing",
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
            "/plugin install search@1.0.0",
            "/plugin update search@1.0.0",
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
