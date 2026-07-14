import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

from app.core.plugin_artifact import build_tpx
from tools.update_feature_catalog import (
    CatalogUpdateError,
    merge_feature_release,
    parse_feature_tag,
    write_feature_catalog,
)


class FeatureCatalogUpdaterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _artifact(
        self,
        plugin_id="media-search",
        version="1.2.3",
        branch="feature/media-search",
        commit="a" * 40,
        payload=b"plugin",
    ):
        source = self.root / f"source-{len(list(self.root.glob('source-*')))}"
        (source / "wheelhouse").mkdir(parents=True)
        manifest = {
            "plugin_id": plugin_id,
            "name": plugin_id,
            "version": version,
            "core_api": ">=1.0,<2.0",
            "entry_point": "telepiplex_media_search.runtime:main",
            "provides": [{"name": "media.search", "exclusive": True}],
            "requires": ["download.provider"],
            "subscribes": [],
            "publishes": [],
            "commands": [],
            "callbacks": [],
            "config_schema_version": 1,
            "state_schema_version": 1,
            "source": {
                "repository": "origin",
                "branch": branch,
                "commit": commit,
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
        return build_tpx(source, self.root / f"{plugin_id}-{version}.tpx")

    def _with_invalid_manifest(self, artifact):
        with zipfile.ZipFile(artifact) as bundle:
            infos = bundle.infolist()
            contents = {info.filename: bundle.read(info.filename) for info in infos}
        manifest = yaml.safe_load(contents["manifest.yaml"])
        manifest["version"] = "not-semver"
        contents["manifest.yaml"] = yaml.safe_dump(
            manifest,
            sort_keys=True,
        ).encode("utf-8")
        contents["checksums.sha256"] = (
            "\n".join(
                f"{hashlib.sha256(contents[name]).hexdigest()}  {name}"
                for name in sorted(contents)
                if name != "checksums.sha256"
            )
            + "\n"
        ).encode("utf-8")

        rewritten = artifact.with_name(f".{artifact.name}.invalid")
        with zipfile.ZipFile(rewritten, "w") as bundle:
            for info in infos:
                bundle.writestr(info, contents[info.filename])
        rewritten.replace(artifact)
        return artifact

    def _previous_catalog(self):
        return {
            "schema_version": 1,
            "release": "open115-v1.0.1",
            "plugins": {
                "open115": {
                    "versions": {
                        "1.0.1": {
                            "url": "https://example.test/open115-1.0.1.tpx",
                            "sha256": "1" * 64,
                        }
                    }
                },
                "media-search": {
                    "versions": {
                        "1.2.2": {
                            "url": "https://example.test/media-search-1.2.2.tpx",
                            "sha256": "2" * 64,
                        }
                    }
                },
            },
        }

    def test_parses_supported_feature_tag(self):
        self.assertEqual(
            parse_feature_tag("media-search-v1.2.3"),
            ("media-search", "1.2.3"),
        )

    def test_rejects_unsupported_or_noncanonical_feature_tags(self):
        for tag in (
            "echo-v1.2.3",
            "media-search-v01.2.3",
            "media-search-v1.2",
            "media-search-v1.2.3-rc1",
            " media-search-v1.2.3",
        ):
            with self.subTest(tag=tag), self.assertRaises(CatalogUpdateError):
                parse_feature_tag(tag)

    def test_merge_preserves_other_plugins_and_versions(self):
        previous = self._previous_catalog()
        original = copy.deepcopy(previous)
        artifact = self._artifact()

        merged = merge_feature_release(
            previous,
            artifact,
            "countott/telepiplex",
            "media-search-v1.2.3",
        )

        self.assertEqual(merged["plugins"]["open115"], previous["plugins"]["open115"])
        self.assertIn("1.2.2", merged["plugins"]["media-search"]["versions"])
        self.assertEqual(previous, original)
        self.assertEqual(merged["release"], "media-search-v1.2.3")
        entry = merged["plugins"]["media-search"]["versions"]["1.2.3"]
        self.assertEqual(
            entry["url"],
            "https://github.com/countott/telepiplex/releases/download/"
            "media-search-v1.2.3/media-search-1.2.3.tpx",
        )
        self.assertEqual(entry["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertEqual(entry["core_api"], ">=1.0,<2.0")
        self.assertEqual(entry["provides"], ["media.search"])
        self.assertEqual(entry["requires"], ["download.provider"])
        self.assertEqual(
            entry["source"],
            {"branch": "feature/media-search", "commit": "a" * 40},
        )

    def test_rejects_tag_manifest_and_branch_identity_mismatches(self):
        cases = (
            ("plugin", self._artifact(plugin_id="open115", branch="feature/115")),
            ("version", self._artifact(version="1.2.4")),
            ("branch", self._artifact(branch="feature/renaming")),
        )
        for label, artifact in cases:
            with self.subTest(label=label), self.assertRaises(CatalogUpdateError):
                merge_feature_release(
                    None,
                    artifact,
                    "countott/telepiplex",
                    "media-search-v1.2.3",
                )

    def test_rejects_reused_version_when_immutable_identity_changes(self):
        artifact = self._artifact()
        merged = merge_feature_release(
            None,
            artifact,
            "countott/telepiplex",
            "media-search-v1.2.3",
        )
        cases = {
            "digest": ("sha256", "0" * 64),
            "branch": ("source", {"branch": "feature/renaming", "commit": "a" * 40}),
            "commit": (
                "source",
                {"branch": "feature/media-search", "commit": "b" * 40},
            ),
        }
        for label, (field, value) in cases.items():
            with self.subTest(label=label):
                previous = copy.deepcopy(merged)
                previous["plugins"]["media-search"]["versions"]["1.2.3"][field] = value
                with self.assertRaises(CatalogUpdateError):
                    merge_feature_release(
                        previous,
                        artifact,
                        "countott/telepiplex",
                        "media-search-v1.2.3",
                    )

    def test_rejects_present_null_previous_version_entry(self):
        artifact = self._artifact()
        previous = merge_feature_release(
            None,
            artifact,
            "countott/telepiplex",
            "media-search-v1.2.3",
        )
        previous["plugins"]["media-search"]["versions"]["1.2.3"] = None

        with self.assertRaises(CatalogUpdateError):
            merge_feature_release(
                previous,
                artifact,
                "countott/telepiplex",
                "media-search-v1.2.3",
            )

    def test_rejects_invalid_repository_and_artifact_filename(self):
        artifact = self._artifact()
        with self.assertRaises(CatalogUpdateError):
            merge_feature_release(None, artifact, "not a repo", "media-search-v1.2.3")

        renamed = artifact.with_name("unexpected.tpx")
        artifact.replace(renamed)
        with self.assertRaises(CatalogUpdateError):
            merge_feature_release(
                None,
                renamed,
                "countott/telepiplex",
                "media-search-v1.2.3",
            )

    def test_normalizes_invalid_manifest_contract_error(self):
        artifact = self._with_invalid_manifest(self._artifact())

        with self.assertRaises(CatalogUpdateError):
            merge_feature_release(
                None,
                artifact,
                "countott/telepiplex",
                "media-search-v1.2.3",
            )

    def test_writes_deterministic_yaml_and_catalog_checksum(self):
        artifact = self._artifact()
        previous = self._previous_catalog()
        first = self.root / "first" / "catalog.yaml"
        second = self.root / "second" / "catalog.yaml"

        result = write_feature_catalog(
            previous,
            artifact,
            "countott/telepiplex",
            "media-search-v1.2.3",
            first,
        )
        write_feature_catalog(
            previous,
            artifact,
            "countott/telepiplex",
            "media-search-v1.2.3",
            second,
        )

        self.assertEqual(result, first)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(
            yaml.safe_load(first.read_text(encoding="utf-8")),
            merge_feature_release(
                previous,
                artifact,
                "countott/telepiplex",
                "media-search-v1.2.3",
            ),
        )
        digest = hashlib.sha256(first.read_bytes()).hexdigest()
        self.assertEqual(
            first.with_name("catalog.yaml.sha256").read_text(encoding="utf-8"),
            f"{digest}  catalog.yaml\n",
        )
        self.assertFalse(list(first.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
