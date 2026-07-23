import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from app.runtime.plugin_artifact import build_tpx, verify_tpx
from tools.generate_release_catalog import (
    CatalogBuildError,
    build_catalog,
    main,
    reuse_unchanged_artifacts,
    write_catalog,
)


PLUGINS = {
    "download": "main",
    "search": "main",
    "rename": "main",
    "sync": "main",
    "caption": "main",
}


class ReleaseCatalogGeneratorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _artifact(
        self,
        plugin_id,
        branch,
        *,
        version="1.2.3",
        suffix="",
        provides=(),
        requires=(),
        commit=None,
        payload=b"plugin",
        output_dir=None,
    ):
        output_dir = Path(output_dir or self.root)
        source = output_dir / f"source-{plugin_id}{suffix}"
        (source / "wheelhouse").mkdir(parents=True)
        manifest = {
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": version,
            "host_api": ">=1.0,<2.0",
            "entry_point": f"telepiplex_{plugin_id.replace('-', '_')}.runtime:main",
            "provides": [
                {"name": name, "exclusive": exclusive}
                for name, exclusive in provides
            ],
            "requires": list(requires),
            "subscribes": [],
            "publishes": [],
            "commands": [],
            "callbacks": [],
            "config_schema_version": 1,
            "state_schema_version": 1,
            "source": {
                "repository": "origin",
                "branch": branch,
                "commit": commit or (
                    (plugin_id[0] if plugin_id[0] in "abcdef" else "a") * 40
                ),
            },
        }
        (source / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
        )
        (source / "plugin.whl").write_bytes(payload)
        (source / "wheelhouse" / "sdk.whl").write_bytes(b"sdk")
        (source / "config.schema.json").write_text(
            json.dumps({"type": "object"}), encoding="utf-8"
        )
        (source / "config.default.yaml").write_text("{}\n", encoding="utf-8")
        return build_tpx(
            source,
            output_dir / f"{plugin_id}-{version}{suffix}.tpx",
        )

    def _all_artifacts(self, *, output_dir=None, payload=b"plugin"):
        return [
            self._artifact(
                "download",
                PLUGINS["download"],
                provides=(("download.provider", True), ("storage.provider", True)),
                output_dir=output_dir,
                payload=payload,
            ),
            self._artifact(
                "search",
                PLUGINS["search"],
                requires=("download.provider",),
                output_dir=output_dir,
                payload=payload,
            ),
            self._artifact(
                "rename",
                PLUGINS["rename"],
                requires=("storage.provider",),
                output_dir=output_dir,
                payload=payload,
            ),
            self._artifact(
                "sync",
                PLUGINS["sync"],
                output_dir=output_dir,
                payload=payload,
            ),
            self._artifact(
                "caption",
                PLUGINS["caption"],
                output_dir=output_dir,
                payload=payload,
            ),
        ]

    def _previous_catalog(self, artifact):
        verified = verify_tpx(artifact)
        manifest = verified.manifest
        return {
            "plugins": {
                manifest.plugin_id: {
                    "versions": {
                        manifest.version: {
                            "sha256": verified.sha256,
                            "source": {
                                "branch": manifest.source.branch,
                                "commit": manifest.source.commit,
                            },
                        }
                    }
                }
            }
        }

    def test_builds_manifest_derived_digest_pinned_catalog(self):
        artifacts = self._all_artifacts()

        catalog = build_catalog(
            "countott/telepiplex",
            "platform-v1.0.0",
            artifacts,
        )

        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["release"], "platform-v1.0.0")
        entry = catalog["plugins"]["search"]["versions"]["1.2.3"]
        artifact = next(path for path in artifacts if "search" in path.name)
        self.assertEqual(entry["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertEqual(
            entry["url"],
            "https://github.com/countott/telepiplex/releases/download/"
            "platform-v1.0.0/search-1.2.3.tpx",
        )
        self.assertEqual(entry["host_api"], ">=1.0,<2.0")
        self.assertEqual(entry["provides"], [])
        self.assertEqual(entry["requires"], ["download.provider"])
        self.assertEqual(entry["source"]["branch"], "main")
        self.assertEqual(len(entry["source"]["commit"]), 40)

        download = catalog["plugins"]["download"]["versions"]["1.2.3"]
        self.assertEqual(
            download["provides"],
            ["download.provider", "storage.provider"],
        )
        self.assertEqual(download["requires"], [])

    def test_builds_catalog_from_main_source_identity(self):
        artifacts = [
            self._artifact(plugin_id, "main")
            for plugin_id in PLUGINS
        ]

        catalog = build_catalog(
            "countott/telepiplex",
            "platform-v1.0.0",
            artifacts,
        )

        self.assertEqual(
            {
                entry["source"]["branch"]
                for plugin in catalog["plugins"].values()
                for entry in plugin["versions"].values()
            },
            {"main"},
        )

    def test_output_is_deterministic_and_writes_catalog_digest(self):
        artifacts = list(reversed(self._all_artifacts()))
        first = self.root / "first" / "catalog.yaml"
        second = self.root / "second" / "catalog.yaml"

        write_catalog("countott/telepiplex", "platform-v1.0.0", artifacts, first)
        write_catalog("countott/telepiplex", "platform-v1.0.0", artifacts, second)

        self.assertEqual(first.read_bytes(), second.read_bytes())
        digest_file = first.with_name("catalog.yaml.sha256")
        expected = hashlib.sha256(first.read_bytes()).hexdigest()
        self.assertEqual(
            digest_file.read_text(encoding="utf-8"),
            f"{expected}  catalog.yaml\n",
        )

    def test_rejects_missing_duplicate_invalid_and_corrupt_inputs(self):
        artifacts = self._all_artifacts()
        cases = [
            ("missing", artifacts[:-1]),
            (
                "duplicate",
                [*artifacts, self._artifact("download", "main", suffix="-copy")],
            ),
        ]
        for label, paths in cases:
            with self.subTest(label=label), self.assertRaises(CatalogBuildError):
                build_catalog("countott/telepiplex", "platform-v1.0.0", paths)

        with self.assertRaises(CatalogBuildError):
            build_catalog("not-a-repository", "platform-v1.0.0", artifacts)
        with self.assertRaises(CatalogBuildError):
            build_catalog("countott/telepiplex", "v1.0.0", artifacts)

        corrupt = self.root / "corrupt.tpx"
        corrupt.write_bytes(b"not a tpx")
        with self.assertRaises(CatalogBuildError):
            build_catalog(
                "countott/telepiplex",
                "platform-v1.0.0",
                [*artifacts[:-1], corrupt],
            )

    def test_rejects_reused_version_with_changed_digest(self):
        artifacts = self._all_artifacts()
        previous = {
            "plugins": {
                "search": {
                    "versions": {
                        "1.2.3": {"sha256": "0" * 64}
                    }
                }
            }
        }

        with self.assertRaises(CatalogBuildError):
            build_catalog(
                "countott/telepiplex",
                "platform-v1.0.0",
                artifacts,
                previous_catalog=previous,
            )

        media_path = next(path for path in artifacts if "search" in path.name)
        previous["plugins"]["search"]["versions"]["1.2.3"][
            "sha256"
        ] = hashlib.sha256(media_path.read_bytes()).hexdigest()
        catalog = build_catalog(
            "countott/telepiplex",
            "platform-v1.0.0",
            artifacts,
            previous_catalog=previous,
        )
        self.assertIn("search", catalog["plugins"])

    def test_reuses_verified_previous_artifact_for_unchanged_source_commit(self):
        previous_assets = self.root / "previous"
        current_assets = self.root / "current"
        previous = self._artifact(
            "search",
            PLUGINS["search"],
            payload=b"previous payload",
            output_dir=previous_assets,
        )
        current = self._artifact(
            "search",
            PLUGINS["search"],
            payload=b"ephemeral current payload",
            output_dir=current_assets,
        )
        current_before = current.read_bytes()
        self.assertNotEqual(previous.read_bytes(), current_before)

        reused = reuse_unchanged_artifacts(
            [current],
            self._previous_catalog(previous),
            previous_assets,
        )

        self.assertEqual(reused, [current])
        self.assertEqual(current.read_bytes(), previous.read_bytes())
        self.assertNotEqual(current.read_bytes(), current_before)

    def test_changed_source_commit_is_not_reused_and_digest_gate_still_rejects(self):
        previous_assets = self.root / "previous"
        current_assets = self.root / "current"
        previous = self._artifact(
            "search",
            PLUGINS["search"],
            commit="a" * 40,
            payload=b"previous payload",
            output_dir=previous_assets,
        )
        current = self._artifact(
            "search",
            PLUGINS["search"],
            commit="b" * 40,
            payload=b"current payload",
            output_dir=current_assets,
        )
        current_before = current.read_bytes()
        previous_catalog = self._previous_catalog(previous)

        reused = reuse_unchanged_artifacts(
            [current],
            previous_catalog,
            previous_assets,
        )

        self.assertEqual(reused, [])
        self.assertEqual(current.read_bytes(), current_before)
        with self.assertRaisesRegex(
            CatalogBuildError,
            "version digest changed without version bump",
        ):
            build_catalog(
                "countott/telepiplex",
                "platform-v1.0.3",
                [
                    self._artifact("download", PLUGINS["download"]),
                    current,
                    self._artifact("rename", PLUGINS["rename"]),
                    self._artifact("sync", PLUGINS["sync"]),
                ],
                previous_catalog=previous_catalog,
            )

    def test_reuse_requires_previous_asset_for_unchanged_source_commit(self):
        previous_assets = self.root / "previous"
        previous_assets.mkdir()
        current = self._artifact("search", PLUGINS["search"])
        previous_catalog = self._previous_catalog(current)

        with self.assertRaisesRegex(CatalogBuildError, "previous artifact is missing"):
            reuse_unchanged_artifacts(
                [current],
                previous_catalog,
                previous_assets,
            )

    def test_reuse_rejects_corrupt_previous_asset(self):
        previous_assets = self.root / "previous"
        previous = self._artifact(
            "search",
            PLUGINS["search"],
            output_dir=previous_assets,
        )
        previous_catalog = self._previous_catalog(previous)
        previous.write_bytes(b"not a tpx")
        current = self._artifact(
            "search",
            PLUGINS["search"],
            payload=b"current",
            output_dir=self.root / "current",
        )

        with self.assertRaisesRegex(CatalogBuildError, "invalid previous artifact"):
            reuse_unchanged_artifacts(
                [current],
                previous_catalog,
                previous_assets,
            )

    def test_reuse_rejects_previous_catalog_digest_mismatch(self):
        previous_assets = self.root / "previous"
        previous = self._artifact(
            "search",
            PLUGINS["search"],
            output_dir=previous_assets,
        )
        previous_catalog = self._previous_catalog(previous)
        previous_catalog["plugins"]["search"]["versions"]["1.2.3"][
            "sha256"
        ] = "0" * 64
        current = self._artifact(
            "search",
            PLUGINS["search"],
            payload=b"current",
            output_dir=self.root / "current",
        )

        with self.assertRaisesRegex(CatalogBuildError, "invalid previous artifact"):
            reuse_unchanged_artifacts(
                [current],
                previous_catalog,
                previous_assets,
            )

    def test_reuse_rejects_missing_previous_catalog_digest(self):
        previous_assets = self.root / "previous"
        previous = self._artifact(
            "search",
            PLUGINS["search"],
            output_dir=previous_assets,
        )
        previous_catalog = self._previous_catalog(previous)
        del previous_catalog["plugins"]["search"]["versions"]["1.2.3"][
            "sha256"
        ]
        current = self._artifact(
            "search",
            PLUGINS["search"],
            payload=b"current",
            output_dir=self.root / "current",
        )

        with self.assertRaisesRegex(CatalogBuildError, "invalid previous artifact"):
            reuse_unchanged_artifacts(
                [current],
                previous_catalog,
                previous_assets,
            )

    def test_reuse_rejects_previous_asset_identity_mismatch(self):
        current = self._artifact(
            "search",
            PLUGINS["search"],
            payload=b"current",
            output_dir=self.root / "current",
        )
        cases = (
            ("plugin", "download", PLUGINS["download"], "1.2.3", "a" * 40),
            ("version", "search", PLUGINS["search"], "9.9.9", "a" * 40),
            ("branch", "search", "feature/rename", "1.2.3", "a" * 40),
            ("commit", "search", PLUGINS["search"], "1.2.3", "b" * 40),
        )
        for label, plugin_id, branch, version, commit in cases:
            with self.subTest(label=label):
                previous_assets = self.root / f"previous-{label}"
                wrong_asset = self._artifact(
                    plugin_id,
                    branch,
                    version=version,
                    commit=commit,
                    payload=b"previous",
                    output_dir=previous_assets,
                )
                expected_path = previous_assets / "search-1.2.3.tpx"
                if wrong_asset != expected_path:
                    wrong_asset.replace(expected_path)
                previous_catalog = self._previous_catalog(current)
                previous_catalog["plugins"]["search"]["versions"]["1.2.3"][
                    "sha256"
                ] = hashlib.sha256(expected_path.read_bytes()).hexdigest()

                with self.assertRaisesRegex(
                    CatalogBuildError,
                    "previous artifact identity mismatch",
                ):
                    reuse_unchanged_artifacts(
                        [current],
                        previous_catalog,
                        previous_assets,
                    )

    def test_previous_assets_cli_option_requires_previous_catalog(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            main(
                [
                    "--repository",
                    "countott/telepiplex",
                    "--tag",
                    "platform-v1.0.3",
                    "--output",
                    str(self.root / "catalog.yaml"),
                    "--previous-assets",
                    str(self.root / "previous"),
                    str(self.root / "missing.tpx"),
                ]
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn(
            "--previous-assets is valid only with --previous-catalog",
            error.getvalue(),
        )

    def test_cli_reuses_previous_assets_when_both_previous_inputs_are_given(self):
        previous_assets = self.root / "previous"
        current_assets = self.root / "current"
        previous = self._all_artifacts(
            output_dir=previous_assets,
            payload=b"previous payload",
        )
        current = self._all_artifacts(
            output_dir=current_assets,
            payload=b"ephemeral current payload",
        )
        previous_catalog = build_catalog(
            "countott/telepiplex",
            "platform-v1.0.2",
            previous,
        )
        previous_catalog_path = previous_assets / "catalog.yaml"
        previous_catalog_path.write_text(
            yaml.safe_dump(previous_catalog, sort_keys=True),
            encoding="utf-8",
        )

        result = main(
            [
                "--repository",
                "countott/telepiplex",
                "--tag",
                "platform-v1.0.3",
                "--output",
                str(self.root / "release" / "catalog.yaml"),
                "--previous-catalog",
                str(previous_catalog_path),
                "--previous-assets",
                str(previous_assets),
                *(str(path) for path in current),
            ]
        )

        self.assertEqual(result, 0)
        for current_path in current:
            self.assertEqual(
                current_path.read_bytes(),
                (previous_assets / current_path.name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
