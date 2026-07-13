import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from app.core.plugin_artifact import build_tpx
from tools.generate_release_catalog import CatalogBuildError, build_catalog, write_catalog


PLUGINS = {
    "open115": "feature/115",
    "media-search": "feature/media-search",
    "renaming": "feature/renaming",
    "plex-management": "feature/plex-management",
}


class ReleaseCatalogGeneratorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _artifact(self, plugin_id, branch, *, version="1.2.3", suffix=""):
        source = self.root / f"source-{plugin_id}{suffix}"
        (source / "wheelhouse").mkdir(parents=True)
        manifest = {
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": version,
            "core_api": ">=1.0,<2.0",
            "entry_point": f"telepiplex_{plugin_id.replace('-', '_')}.runtime:main",
            "provides": [],
            "requires": [],
            "subscribes": [],
            "publishes": [],
            "commands": [],
            "callbacks": [],
            "config_schema_version": 1,
            "state_schema_version": 1,
            "source": {
                "repository": "origin",
                "branch": branch,
                "commit": (plugin_id[0] if plugin_id[0] in "abcdef" else "a") * 40,
            },
        }
        (source / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
        )
        (source / "plugin.whl").write_bytes(b"plugin")
        (source / "wheelhouse" / "sdk.whl").write_bytes(b"sdk")
        (source / "config.schema.json").write_text(
            json.dumps({"type": "object"}), encoding="utf-8"
        )
        (source / "config.default.yaml").write_text("{}\n", encoding="utf-8")
        return build_tpx(
            source,
            self.root / f"{plugin_id}-{version}{suffix}.tpx",
        )

    def _all_artifacts(self):
        return [
            self._artifact(plugin_id, branch)
            for plugin_id, branch in PLUGINS.items()
        ]

    def test_builds_manifest_derived_digest_pinned_catalog(self):
        artifacts = self._all_artifacts()

        catalog = build_catalog(
            "countott/telepiplex",
            "platform-v1.0.0",
            artifacts,
        )

        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["release"], "platform-v1.0.0")
        entry = catalog["plugins"]["media-search"]["versions"]["1.2.3"]
        artifact = next(path for path in artifacts if "media-search" in path.name)
        self.assertEqual(entry["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertEqual(
            entry["url"],
            "https://github.com/countott/telepiplex/releases/download/"
            "platform-v1.0.0/media-search-1.2.3.tpx",
        )
        self.assertEqual(entry["core_api"], ">=1.0,<2.0")
        self.assertEqual(entry["source"]["branch"], "feature/media-search")
        self.assertEqual(len(entry["source"]["commit"]), 40)

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
                [*artifacts, self._artifact("open115", "feature/115", suffix="-copy")],
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
                "media-search": {
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

        media_path = next(path for path in artifacts if "media-search" in path.name)
        previous["plugins"]["media-search"]["versions"]["1.2.3"][
            "sha256"
        ] = hashlib.sha256(media_path.read_bytes()).hexdigest()
        catalog = build_catalog(
            "countott/telepiplex",
            "platform-v1.0.0",
            artifacts,
            previous_catalog=previous,
        )
        self.assertIn("media-search", catalog["plugins"])


if __name__ == "__main__":
    unittest.main()
