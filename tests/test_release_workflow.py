import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CORE_WORKFLOW = ROOT / ".github/workflows/release.yml"
FEATURE_WORKFLOW = ROOT / ".github/workflows/release-feature.yml"
DESIGN = ROOT / "docs/superpowers/specs/2026-07-14-independent-feature-and-catalog-releases-design.md"
PLAN = ROOT / "docs/superpowers/plans/2026-07-14-independent-feature-and-catalog-releases.md"
OLD_WORKFLOW = ROOT / ".github/workflows/docker-build.yml"


class ReleaseWorkflowTest(unittest.TestCase):
    def _workflow(self, path):
        self.assertTrue(path.is_file(), f"workflow is missing: {path.name}")
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        # PyYAML's YAML 1.1 resolver treats the Actions key `on` as True.
        if True in workflow:
            workflow["on"] = workflow.pop(True)
        return workflow

    def _step(self, workflow, job, name):
        return next(
            step
            for step in workflow["jobs"][job]["steps"]
            if step.get("name") == name
        )

    def _python_blocks(self, source):
        blocks = []
        lines = source.splitlines()
        cursor = 0
        while cursor < len(lines):
            if "<<'PY'" not in lines[cursor]:
                cursor += 1
                continue
            end = cursor + 1
            while end < len(lines) and lines[end].strip() != "PY":
                end += 1
            self.assertLess(end, len(lines), "unterminated Python heredoc")
            blocks.append("\n".join(lines[cursor + 1 : end]) + "\n")
            cursor = end + 1
        return blocks

    def _run_script(self, source, *, env=None, commands=None):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        fakebin = root / "bin"
        fakebin.mkdir()
        for name, body in (commands or {}).items():
            command = fakebin / name
            command.write_text(
                "#!/usr/bin/env bash\n" + textwrap.dedent(body),
                encoding="utf-8",
            )
            command.chmod(0o755)
        run_env = os.environ.copy()
        run_env.update(env or {})
        run_env["PATH"] = f"{fakebin}:{run_env['PATH']}"
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", source],
            cwd=root,
            env=run_env,
            capture_output=True,
            text=True,
        )
        return temporary, root, result

    def test_core_release_accepts_only_core_tags(self):
        workflow = self._workflow(CORE_WORKFLOW)
        triggers = workflow["on"]

        self.assertEqual(set(triggers), {"push"})
        self.assertEqual(triggers["push"]["tags"], ["core-v*"])
        self.assertNotIn("branches", triggers["push"])
        self.assertNotIn("workflow_dispatch", triggers)
        self.assertIn("concurrency", workflow)

    def test_core_release_tests_and_pushes_only_version_and_latest(self):
        workflow = self._workflow(CORE_WORKFLOW)
        jobs = workflow["jobs"]
        source = CORE_WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(set(jobs), {"validate-core", "build-core-image"})
        self.assertNotIn("build-features", jobs)
        self.assertNotIn("publish-release", jobs)
        self.assertEqual(workflow["permissions"]["contents"], "read")
        self.assertEqual(workflow["permissions"]["packages"], "write")

        validate = self._step(
            workflow, "validate-core", "Validate immutable Core tag"
        )["run"]
        self.assertIn("^core-v", validate)
        self.assertNotIn("platform-v", validate)
        self._step(workflow, "validate-core", "Run Core tests")
        self._step(workflow, "validate-core", "Compile tracked Python")

        build = jobs["build-core-image"]
        self.assertEqual(build["needs"], "validate-core")
        publish = self._step(
            workflow, "build-core-image", "Build and push Core image"
        )["with"]
        self.assertEqual(publish["platforms"], "linux/amd64")
        self.assertTrue(publish["push"])
        self.assertEqual(
            set(publish["tags"].splitlines()),
            {
                "${{ env.CORE_IMAGE }}:${{ steps.version.outputs.version }}",
                "${{ env.CORE_IMAGE }}:latest",
            },
        )
        self.assertNotIn("gh release create", source)
        self.assertNotIn(".tpx", source)

    def test_core_manifest_probe_fails_closed(self):
        workflow = self._workflow(CORE_WORKFLOW)
        probe = self._step(
            workflow, "build-core-image", "Refuse an existing immutable image tag"
        )["run"]
        docker = """
            printf '%s\\n' "$FAKE_DOCKER_OUTPUT" >&2
            exit "$FAKE_DOCKER_STATUS"
        """
        base_env = {
            "RELEASE_TAG": "core-v1.2.3",
            "CORE_IMAGE": "ghcr.io/example/telepiplex-core",
        }

        for label, status, output, succeeds in (
            ("missing", "1", "manifest unknown", True),
            ("missing-code", "1", "MANIFEST_UNKNOWN", True),
            ("exists", "0", "exists", False),
            ("auth", "1", "unauthorized: authentication required", False),
            ("network", "1", "dial tcp: network is unreachable", False),
            ("unknown", "42", "unexpected inspect failure", False),
        ):
            with self.subTest(label=label):
                with tempfile.NamedTemporaryFile() as output_file:
                    env = {
                        **base_env,
                        "GITHUB_OUTPUT": output_file.name,
                        "FAKE_DOCKER_STATUS": status,
                        "FAKE_DOCKER_OUTPUT": output,
                    }
                    temporary, _, result = self._run_script(
                        probe, env=env, commands={"docker": docker}
                    )
                    self.addCleanup(temporary.cleanup)
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)

    def test_feature_release_is_tag_only_and_splits_read_from_write(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        triggers = workflow["on"]
        jobs = workflow["jobs"]

        self.assertEqual(set(triggers), {"push"})
        self.assertEqual(
            triggers["push"]["tags"],
            [
                "open115-v*",
                "media-search-v*",
                "renaming-v*",
                "plex-management-v*",
            ],
        )
        self.assertNotIn("workflow_dispatch", triggers)
        self.assertNotIn("concurrency", workflow)
        self.assertEqual(set(jobs), {"build-feature", "publish-feature"})
        self.assertEqual(workflow["permissions"]["contents"], "read")
        self.assertEqual(jobs["build-feature"]["permissions"]["contents"], "read")
        self.assertEqual(jobs["publish-feature"]["permissions"]["contents"], "write")
        self.assertEqual(jobs["publish-feature"]["needs"], "build-feature")

        build_source = "\n".join(
            step.get("run", "") for step in jobs["build-feature"]["steps"]
        )
        publish_source = "\n".join(
            step.get("run", "") for step in jobs["publish-feature"]["steps"]
        )
        self.assertIn("tools/build_feature.py", build_source)
        self.assertNotIn("tools/build_feature.py", publish_source)
        self.assertNotIn("feature-src", publish_source)

        for name in (
            "Checkout Core release infrastructure",
            "Checkout fixed Feature branch",
        ):
            checkout = self._step(workflow, "build-feature", name)
            self.assertIs(checkout["with"]["persist-credentials"], False)
        feature_checkout = self._step(
            workflow, "build-feature", "Checkout fixed Feature branch"
        )
        self.assertEqual(feature_checkout["if"], "steps.release.outputs.exists == 'false'")

    def test_feature_release_probe_distinguishes_404_from_failures(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        probe = self._step(
            workflow, "build-feature", "Probe immutable Feature Release"
        )["run"]
        curl = """
            output=''
            while (($#)); do
              if [[ "$1" == '--output' ]]; then output="$2"; shift 2; else shift; fi
            done
            [[ -z "$output" ]] || printf '{}\\n' > "$output"
            printf '%s' "$FAKE_HTTP_CODE"
            exit "$FAKE_CURL_STATUS"
        """
        base_env = {
            "GITHUB_API_URL": "https://api.github.test",
            "GITHUB_REPOSITORY": "example/repo",
            "RELEASE_TAG": "media-search-v1.2.3",
            "GH_TOKEN": "token",
        }

        for label, code, curl_status, succeeds, expected in (
            ("exists", "200", "0", True, "exists=true"),
            ("missing", "404", "0", True, "exists=false"),
            ("auth", "401", "0", False, ""),
            ("server", "503", "0", False, ""),
            ("network", "000", "7", False, ""),
        ):
            with self.subTest(label=label):
                with tempfile.NamedTemporaryFile() as output_file:
                    env = {
                        **base_env,
                        "GITHUB_OUTPUT": output_file.name,
                        "FAKE_HTTP_CODE": code,
                        "FAKE_CURL_STATUS": curl_status,
                    }
                    temporary, _, result = self._run_script(
                        probe, env=env, commands={"curl": curl}
                    )
                    self.addCleanup(temporary.cleanup)
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                    if expected:
                        self.assertIn(expected, Path(output_file.name).read_text())

    def test_existing_release_reuses_exact_asset_and_embedded_commit(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        download = self._step(
            workflow, "build-feature", "Download existing Feature artifact"
        )
        verify = self._step(
            workflow, "build-feature", "Verify immutable Feature artifact"
        )["run"]
        build = self._step(
            workflow, "build-feature", "Build new Feature artifact"
        )

        self.assertEqual(download["if"], "steps.release.outputs.exists == 'true'")
        self.assertIn("release.json", download["run"])
        self.assertIn("browser_download_url", download["run"])
        self.assertIn("ASSET_NAME", download["run"])
        self.assertIn("steps.release.outputs.exists == 'false'", build["if"])
        self.assertIn("verify_tpx", verify)
        self.assertIn("manifest.source.commit", verify)
        self.assertIn("source_commit=", verify)
        self.assertNotIn("git -C feature-src rev-parse", download["run"] + verify)

    def test_first_release_reuses_matching_previous_catalog_asset(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        reuse = self._step(
            workflow, "build-feature", "Reuse matching catalog artifact"
        )
        build = self._step(
            workflow, "build-feature", "Build new Feature artifact"
        )

        self.assertEqual(reuse["if"], "steps.release.outputs.exists == 'false'")
        self.assertIn("releases/latest/download/catalog.yaml", reuse["run"])
        self.assertIn("refs/heads/catalog", reuse["run"])
        self.assertIn("entry[\"url\"]", reuse["run"])
        self.assertIn("entry[\"sha256\"]", reuse["run"])
        self.assertIn("FEATURE_COMMIT", reuse["run"])
        self.assertIn("verify_tpx", reuse["run"])
        self.assertEqual(
            build["if"],
            "steps.release.outputs.exists == 'false' && steps.prior.outputs.found != 'true'",
        )

    def test_prior_catalog_asset_url_is_exact_and_never_receives_token(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        reuse = self._step(
            workflow, "build-feature", "Reuse matching catalog artifact"
        )["run"]
        validator = next(
            block
            for block in self._python_blocks(reuse)
            if "previous Feature asset URL" in block
        )
        download = reuse.split(
            "if [[ -f prior-catalog/reuse.yaml ]]", 1
        )[1].split('python - "dist/$ASSET_NAME"', 1)[0]

        self.assertIn("urlsplit", validator)
        for rejected_field in ("username", "password", "query", "fragment"):
            self.assertIn(rejected_field, validator)
        self.assertIn("GITHUB_REPOSITORY", validator)
        self.assertIn("ASSET_NAME", validator)
        self.assertNotIn("Authorization:", download)
        self.assertNotIn("GH_TOKEN", download)

        expected = (
            "https://github.com/countott/telepiplex/releases/download/"
            "platform-v1.0.5/media-search-1.2.3.tpx"
        )
        urls = {
            "valid": (expected, True),
            "off-origin": (expected.replace("github.com", "evil.example"), False),
            "userinfo": (expected.replace("github.com", "attacker@github.com"), False),
            "query": (expected + "?token=steal", False),
            "fragment": (expected + "#steal", False),
            "wrong-path": (expected.replace("media-search-1.2.3.tpx", "other.tpx"), False),
        }
        for label, (url, succeeds) in urls.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                (root / "prior-catalog").mkdir()
                catalog = {
                    "schema_version": 1,
                    "plugins": {
                        "media-search": {
                            "versions": {
                                "1.2.3": {
                                    "url": url,
                                    "sha256": "a" * 64,
                                    "source": {
                                        "branch": "feature/media-search",
                                        "commit": "b" * 40,
                                    },
                                }
                            }
                        }
                    },
                }
                (root / "prior-catalog/catalog.yaml").write_text(
                    yaml.safe_dump(catalog), encoding="utf-8"
                )
                env = os.environ.copy()
                env.update(
                    {
                        "PLUGIN_ID": "media-search",
                        "VERSION": "1.2.3",
                        "FEATURE_BRANCH": "feature/media-search",
                        "FEATURE_COMMIT": "b" * 40,
                        "GITHUB_REPOSITORY": "countott/telepiplex",
                        "ASSET_NAME": "media-search-1.2.3.tpx",
                    }
                )
                result = subprocess.run(
                    [sys.executable, "-c", validator],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)

    def test_catalog_discovery_bootstraps_only_on_status_two(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        bootstrap = self._step(
            workflow, "publish-feature", "Load bootstrap catalog snapshot"
        )["run"]
        git = """
            if [[ "$1" == 'ls-remote' ]]; then exit "$FAKE_LS_REMOTE_STATUS"; fi
            exit 99
        """
        curl = """
            printf 'called\\n' >> "$FAKE_CURL_LOG"
            output=''
            while (($#)); do
              if [[ "$1" == '--output' ]]; then output="$2"; shift 2; else shift; fi
            done
            [[ -z "$output" ]] || : > "$output"
            printf '404'
        """

        for status, succeeds, curl_called in (("2", True, True), ("128", False, False)):
            with self.subTest(ls_remote_status=status):
                temporary = tempfile.TemporaryDirectory()
                root = Path(temporary.name)
                curl_log = root / "curl.log"
                result_temp, _, result = self._run_script(
                    bootstrap,
                    env={
                        "FAKE_LS_REMOTE_STATUS": status,
                        "FAKE_CURL_LOG": str(curl_log),
                        "GITHUB_REPOSITORY": "example/repo",
                        "GH_TOKEN": "token",
                    },
                    commands={"git": git, "curl": curl},
                )
                self.addCleanup(temporary.cleanup)
                self.addCleanup(result_temp.cleanup)
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                self.assertEqual(curl_log.exists(), curl_called)

        self.assertIn("LS_REMOTE_STATUS", bootstrap)
        self.assertIn("2)", bootstrap)
        self.assertIn("Catalog branch probe failed", bootstrap)

    def test_publication_uses_optimistic_merge_retry_without_dropped_entries(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        steps = workflow["jobs"]["publish-feature"]["steps"]
        names = [step.get("name") for step in steps]
        ensure_release = self._step(
            workflow, "publish-feature", "Ensure immutable Feature Release"
        )["run"]
        publish = self._step(
            workflow, "publish-feature", "Merge and publish catalog with optimistic retry"
        )["run"]

        self.assertIn("gh release create", ensure_release)
        self.assertIn("catalog.yaml", ensure_release)
        self.assertIn("catalog.yaml.sha256", ensure_release)
        self.assertIn("for ATTEMPT in 1 2 3 4 5", publish)
        self.assertIn("git ls-remote", publish)
        self.assertIn("LS_REMOTE_STATUS", publish)
        self.assertIn("git fetch --force", publish)
        self.assertIn("write_feature_catalog", publish)
        self.assertIn("git push --porcelain origin HEAD:catalog", publish)
        self.assertIn("[rejected]", publish)
        self.assertIn("continue", publish)
        self.assertIn("Catalog push failed operationally", publish)
        self.assertLess(
            names.index("Ensure immutable Feature Release"),
            names.index("Merge and publish catalog with optimistic retry"),
        )
        self.assertFalse(OLD_WORKFLOW.exists(), "unsafe legacy workflow still exists")

    def test_latest_release_compatibility_assets_converge_after_catalog_push(self):
        workflow = self._workflow(FEATURE_WORKFLOW)
        sync = self._step(
            workflow, "publish-feature", "Synchronize Release catalog assets"
        )["run"]

        self.assertIn("gh release upload", sync)
        self.assertIn("--clobber", sync)
        self.assertIn("catalog.yaml", sync)
        self.assertIn("catalog.yaml.sha256", sync)
        self.assertIn("LATEST_TAG", sync)
        self.assertIn("releases/latest", sync)
        self.assertIn("for SYNC_ATTEMPT in 1 2 3 4 5", sync)
        self.assertIn("cmp", sync)

    def test_docs_specify_optimistic_catalog_publication(self):
        for path in (DESIGN, PLAN):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn("optimistic", source.lower())
                self.assertIn("non-fast-forward", source.lower())
                self.assertNotIn("cancel-in-progress: false", source)
                self.assertNotIn("serialized one-Feature", source)

    def test_workflows_install_local_wheel_build_backends_only_in_read_job(self):
        core = self._workflow(CORE_WORKFLOW)
        feature = self._workflow(FEATURE_WORKFLOW)
        core_install = self._step(
            core, "validate-core", "Install Core test dependencies"
        )["run"]
        feature_install = self._step(
            feature, "build-feature", "Install Feature build dependencies"
        )["run"]
        for package in ("setuptools", "wheel"):
            self.assertIn(package, core_install)
            self.assertIn(package, feature_install)
        publish_install = self._step(
            feature, "publish-feature", "Install catalog dependencies"
        )["run"]
        self.assertNotIn(" build ", f" {publish_install} ")


if __name__ == "__main__":
    unittest.main()
