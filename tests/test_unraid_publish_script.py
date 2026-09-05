import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "unraid" / "telepiplex-publish.sh"
MODULES = ("download", "search", "rename", "sync", "caption")


class UnraidPublishScriptTest(unittest.TestCase):
    def _run_script(
        self,
        *,
        changed_path,
        remote_tags,
        script_args=(),
        host_source=textwrap.dedent(
            '''\
            def get_version(md_format=False):
                version = "v3.6.9-host"
                return version
            '''
        ),
    ):
        self.assertTrue(SCRIPT.is_file(), f"missing script: {SCRIPT}")

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repository = root / "telepiplex"
        fakebin = root / "bin"
        repository.mkdir()
        fakebin.mkdir()
        (repository / ".git").mkdir()
        (repository / ".stfolder").mkdir()
        app = repository / "app"
        app.mkdir()
        (app / "115bot.py").write_text(host_source, encoding="utf-8")

        for module in MODULES:
            target = repository / "features" / module
            target.mkdir(parents=True)
            shutil.copy(ROOT / "features" / module / "manifest.yaml", target)
            shutil.copy(ROOT / "features" / module / "pyproject.toml", target)

        ssh_key = root / "telepiplex_github"
        ssh_key.write_text("test key\n", encoding="utf-8")
        git_log = root / "git.log"
        git_log.touch()
        fake_git = fakebin / "git"
        fake_git.write_text(
            textwrap.dedent(
                r"""\
                #!/usr/bin/env bash
                set -u

                if [[ "$1" != "-c" \
                  || "$2" != "safe.directory=$TELEPIPLEX_PUBLISH_REPO" \
                  || "$3" != "-C" \
                  || "$4" != "$TELEPIPLEX_PUBLISH_REPO" ]]; then
                  printf "fatal: detected dubious ownership in repository at '%s'\n" \
                    "$TELEPIPLEX_PUBLISH_REPO" >&2
                  exit 128
                fi

                shift 4
                printf '%s\n' "$*" >>"$FAKE_GIT_LOG"

                case "$*" in
                  "branch --show-current")
                    printf '%s\n' main
                    ;;
                  "remote get-url origin")
                    printf '%s\n' \
                      ssh://git@ssh.github.com:443/countott/telepiplex.git
                    ;;
                  "config user.name")
                    printf '%s\n' "telepiplex publisher"
                    ;;
                  "config user.email")
                    printf '%s\n' publisher@example.test
                    ;;
                  "rev-parse --git-dir")
                    printf '%s\n' .git
                    ;;
                  "diff --name-only --diff-filter=U")
                    ;;
                  "config --local core.sshCommand "*)
                    ;;
                  "fetch "*)
                    ;;
                  "merge-base --is-ancestor origin/main HEAD")
                    ;;
                  "ls-remote --tags --refs origin refs/tags/*-v*")
                    printf '%s\n' "$FAKE_REMOTE_TAGS"
                    ;;
                  "diff --name-only")
                    [[ -z "$FAKE_CHANGED_PATH" ]] ||
                      printf '%s\n' "$FAKE_CHANGED_PATH"
                    ;;
                  "diff --cached --name-only")
                    ;;
                  "diff --name-only origin/main..HEAD")
                    ;;
                  "ls-files --others --exclude-standard")
                    ;;
                  "add -A")
                    ;;
                  "rev-list --count origin/main..HEAD")
                    printf '%s\n' 0
                    ;;
                  "status --short")
                    [[ -z "$FAKE_CHANGED_PATH" ]] ||
                      printf 'M  %s\n' "$FAKE_CHANGED_PATH"
                    ;;
                  "diff --cached --quiet")
                    [[ -z "$FAKE_CHANGED_PATH" ]]
                    ;;
                  "commit -m "*)
                    ;;
                  "push origin main")
                    ;;
                  "rev-parse HEAD"|"rev-parse origin/main")
                    printf '%s\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
                    ;;
                  "show-ref --verify --quiet refs/tags/"*)
                    exit 1
                    ;;
                  "tag -a "*)
                    ;;
                  "push origin refs/tags/"*)
                    ;;
                  "ls-remote --exit-code --tags --refs origin refs/tags/"*)
                    printf '%s\n' \
                      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/tags/${*: -1}"
                    ;;
                  "status -sb")
                    printf '%s\n' "## main...origin/main"
                    ;;
                  *)
                    printf 'unexpected git command: %s\n' "$*" >&2
                    exit 90
                    ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fakebin}:{env['PATH']}",
                "TELEPIPLEX_PUBLISH_REPO": str(repository),
                "TELEPIPLEX_PUBLISH_SSH_KEY": str(ssh_key),
                "TELEPIPLEX_PUBLISH_LOCK_FILE": str(root / "publish.lock"),
                "FAKE_CHANGED_PATH": changed_path,
                "FAKE_REMOTE_TAGS": remote_tags,
                "FAKE_GIT_LOG": str(git_log),
            }
        )
        result = subprocess.run(
            ["bash", str(SCRIPT), *script_args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        return result, git_log

    def test_host_and_multiple_features_are_pushed_as_individual_tag_events(self):
        result, git_log = self._run_script(
            changed_path="app/115bot.py",
            remote_tags="\n".join(
                (
                    "host refs/tags/telepiplex-v3.6.1",
                    "a refs/tags/download-v1.0.18",
                    "b refs/tags/search-v1.11.5",
                    "c refs/tags/rename-v1.5.9",
                    "d refs/tags/sync-v1.1.4",
                    "e refs/tags/caption-v0.1.4",
                )
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tag_pushes = [
            line
            for line in git_log.read_text(encoding="utf-8").splitlines()
            if line.startswith("push ") and "refs/tags/" in line
        ]
        self.assertEqual(
            tag_pushes,
            [
                "push origin refs/tags/telepiplex-v3.6.9",
                "push origin refs/tags/download-v2.1.0",
                "push origin refs/tags/search-v2.1.1",
                "push origin refs/tags/rename-v2.1.0",
                "push origin refs/tags/sync-v2.0.1",
            ],
        )
        self.assertTrue(
            all(line.count("refs/tags/") == 1 for line in tag_pushes)
        )
        self.assertFalse(
            any(line.startswith("push --atomic ") for line in tag_pushes)
        )
        self.assertIn(
            "commit -m update telepiplex",
            git_log.read_text(encoding="utf-8"),
        )

    def test_published_feature_changes_can_enter_main_without_a_new_tag(self):
        result, git_log = self._run_script(
            changed_path="features/download/README.md",
            remote_tags="\n".join(
                (
                    "host refs/tags/telepiplex-v3.6.9",
                    "a refs/tags/download-v2.1.0",
                    "b refs/tags/search-v2.1.1",
                    "c refs/tags/rename-v2.1.0",
                    "d refs/tags/sync-v2.0.1",
                    "e refs/tags/caption-v0.1.4",
                )
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("仅进入 main、不创建标签", result.stdout)
        self.assertIn("download 2.1.0", result.stdout)
        self.assertNotIn("refs/tags/download-v2.1.0", git_log.read_text())

    def test_unpublished_feature_version_is_tagged(self):
        result, git_log = self._run_script(
            changed_path="features/search/manifest.yaml",
            remote_tags="\n".join(
                (
                    "host refs/tags/telepiplex-v3.6.9",
                    "a refs/tags/download-v2.1.0",
                    "b refs/tags/search-v1.12.3",
                    "c refs/tags/rename-v2.1.0",
                    "d refs/tags/sync-v2.0.1",
                    "e refs/tags/caption-v0.1.4",
                )
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("search-v2.1.1", result.stdout)
        self.assertIn(
            "push origin refs/tags/search-v2.1.1",
            git_log.read_text(encoding="utf-8"),
        )

    def test_user_script_is_zero_argument_and_ignores_legacy_arguments(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("#argumentDescription=", source)
        self.assertNotIn("#argumentDefault=", source)

        result, git_log = self._run_script(
            changed_path="",
            remote_tags="\n".join(
                (
                    "host refs/tags/telepiplex-v3.6.9",
                    "a refs/tags/download-v2.1.0",
                    "b refs/tags/search-v2.1.1",
                    "c refs/tags/rename-v2.1.0",
                    "d refs/tags/sync-v2.0.1",
                    "e refs/tags/caption-v0.1.4",
                )
            ),
            script_args=("PUBLISH 3.4.9 release telepiplex 3.4.9",),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("已忽略 User Scripts 保留的旧参数", result.stdout)
        self.assertFalse(
            any(
                line.startswith(("commit ", "push "))
                for line in git_log.read_text(encoding="utf-8").splitlines()
            )
        )

    def test_invalid_host_version_source_fails_before_git_mutation(self):
        cases = {
            "missing": "def get_version():\n    return 'unknown'\n",
            "malformed": (
                "def get_version():\n"
                '    version = "v3.4-host"\n'
                "    return version\n"
            ),
            "duplicate": (
                "def get_version():\n"
                '    version = "v3.4.10-host"\n'
                '    version = "v3.4.11-host"\n'
                "    return version\n"
            ),
        }
        for name, host_source in cases.items():
            with self.subTest(name=name):
                result, git_log = self._run_script(
                    changed_path="",
                    remote_tags="",
                    host_source=host_source,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Host 版本", result.stderr)
                self.assertFalse(
                    any(
                        line.startswith(("add ", "commit ", "push "))
                        for line in git_log.read_text(
                            encoding="utf-8"
                        ).splitlines()
                    )
                )


if __name__ == "__main__":
    unittest.main()
