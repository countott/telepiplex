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
    def _run_script(self, *, changed_path, remote_tags, host_version="-"):
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

        for module in MODULES:
            target = repository / "features" / module
            target.mkdir(parents=True)
            shutil.copy(ROOT / "features" / module / "manifest.yaml", target)
            shutil.copy(ROOT / "features" / module / "pyproject.toml", target)

        ssh_key = root / "telepiplex_github"
        ssh_key.write_text("test key\n", encoding="utf-8")
        git_log = root / "git.log"
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
            [
                "bash",
                str(SCRIPT),
                f"PUBLISH {host_version} test publish",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        return result, git_log

    def test_host_and_multiple_features_are_pushed_as_individual_tag_events(self):
        result, git_log = self._run_script(
            changed_path="app/115bot.py",
            host_version="3.4.9",
            remote_tags="\n".join(
                (
                    "a refs/tags/telepiplex-v3.4.8",
                    "b refs/tags/search-v1.1.0",
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
                "push origin refs/tags/telepiplex-v3.4.9",
                "push origin refs/tags/download-v1.0.5",
                "push origin refs/tags/rename-v1.0.4",
                "push origin refs/tags/sync-v1.0.2",
                "push origin refs/tags/caption-v0.1.2",
            ],
        )
        self.assertTrue(
            all(line.count("refs/tags/") == 1 for line in tag_pushes)
        )
        self.assertFalse(
            any(line.startswith("push --atomic ") for line in tag_pushes)
        )

    def test_published_feature_changes_can_enter_main_without_a_new_tag(self):
        result, git_log = self._run_script(
            changed_path="features/download/README.md",
            remote_tags="\n".join(
                (
                    "a refs/tags/download-v1.0.5",
                    "b refs/tags/search-v1.1.0",
                    "c refs/tags/rename-v1.0.4",
                    "d refs/tags/sync-v1.0.2",
                    "e refs/tags/caption-v0.1.2",
                )
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("仅进入 main、不创建标签", result.stdout)
        self.assertIn("download 1.0.5", result.stdout)
        self.assertNotIn("refs/tags/download-v1.0.5", git_log.read_text())

    def test_unpublished_feature_version_is_tagged(self):
        result, git_log = self._run_script(
            changed_path="features/search/manifest.yaml",
            remote_tags="\n".join(
                (
                    "a refs/tags/download-v1.0.5",
                    "b refs/tags/search-v1.0.0",
                    "c refs/tags/rename-v1.0.4",
                    "d refs/tags/sync-v1.0.2",
                    "e refs/tags/caption-v0.1.2",
                )
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("search-v1.1.0", result.stdout)
        self.assertIn(
            "push origin refs/tags/search-v1.1.0",
            git_log.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
