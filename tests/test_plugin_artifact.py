import hashlib
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml


class PluginArtifactTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _source(self, name="source") -> Path:
        source = self.root / name
        (source / "wheelhouse").mkdir(parents=True)
        (source / "migrations").mkdir()
        manifest = {
            "plugin_id": "echo",
            "name": "Echo",
            "version": "1.0.0",
            "core_api": ">=1.0,<2.0",
            "entry_point": "telepiplex_echo.runtime:main",
            "provides": [{"name": "demo.echo", "exclusive": True}],
            "requires": [],
            "subscribes": [],
            "publishes": [],
            "commands": [{"name": "echo", "description": "Echo text"}],
            "callbacks": ["echo"],
            "source": {
                "repository": "origin",
                "branch": "feature/echo",
                "commit": "a" * 40,
            },
        }
        (source / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
        )
        (source / "plugin.whl").write_bytes(b"plugin-wheel")
        (source / "wheelhouse" / "sdk.whl").write_bytes(b"sdk-wheel")
        (source / "config.schema.json").write_text(
            json.dumps({"type": "object", "additionalProperties": False}),
            encoding="utf-8",
        )
        (source / "config.default.yaml").write_text("{}\n", encoding="utf-8")
        (source / "migrations" / "001.json").write_text("{}\n", encoding="utf-8")
        return source

    def test_build_is_deterministic_and_verified_manifest_is_typed(self):
        from app.core.plugin_artifact import build_tpx, verify_tpx

        source = self._source()
        first = build_tpx(source, self.root / "first.tpx")
        second = build_tpx(source, self.root / "second.tpx")

        self.assertEqual(first.read_bytes(), second.read_bytes())
        verified = verify_tpx(first)
        self.assertEqual(verified.manifest.plugin_id, "echo")
        self.assertEqual(verified.manifest.version, "1.0.0")
        self.assertEqual(verified.sha256, hashlib.sha256(first.read_bytes()).hexdigest())
        self.assertIn("checksums.sha256", verified.members)
        self.assertEqual(list(verified.members), sorted(verified.members))

    def test_expected_archive_digest_must_match(self):
        from app.core.plugin_artifact import ArtifactError, build_tpx, verify_tpx

        artifact = build_tpx(self._source(), self.root / "echo.tpx")

        with self.assertRaises(ArtifactError) as raised:
            verify_tpx(artifact, expected_sha256="0" * 64)

        self.assertEqual(raised.exception.code, "archive_checksum_mismatch")

    def test_build_rejects_missing_required_unexpected_and_symlink_members(self):
        from app.core.plugin_artifact import ArtifactError, build_tpx

        missing = self._source("missing-source")
        (missing / "plugin.whl").unlink()
        with self.assertRaises(ArtifactError):
            build_tpx(missing, self.root / "missing.tpx")

        unexpected = self._source("unexpected-source")
        (unexpected / "script.sh").write_text("bad", encoding="utf-8")
        with self.assertRaises(ArtifactError):
            build_tpx(unexpected, self.root / "unexpected.tpx")

        symlink_source = self._source("symlink-source")
        (symlink_source / "wheelhouse" / "link.whl").symlink_to("sdk.whl")
        with self.assertRaises(ArtifactError):
            build_tpx(symlink_source, self.root / "symlink.tpx")

    def test_verify_rejects_tampered_member(self):
        from app.core.plugin_artifact import ArtifactError, build_tpx, verify_tpx

        artifact = build_tpx(self._source(), self.root / "echo.tpx")
        tampered = self.root / "tampered.tpx"
        with zipfile.ZipFile(artifact, "r") as original, zipfile.ZipFile(tampered, "w") as target:
            for info in original.infolist():
                data = original.read(info.filename)
                if info.filename == "config.default.yaml":
                    data = b"changed: true\n"
                target.writestr(info, data)

        with self.assertRaises(ArtifactError) as raised:
            verify_tpx(tampered)

        self.assertEqual(raised.exception.code, "member_checksum_mismatch")

    def test_verify_rejects_unsafe_duplicate_and_symlink_entries(self):
        from app.core.plugin_artifact import ArtifactError, verify_tpx

        unsafe_names = ("../escape", "/absolute", "wheelhouse/../../escape")
        for index, name in enumerate(unsafe_names):
            archive = self.root / f"unsafe-{index}.tpx"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(name, b"bad")
            with self.subTest(name=name), self.assertRaises(ArtifactError):
                verify_tpx(archive)

        duplicate = self.root / "duplicate.tpx"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as bundle:
                bundle.writestr("manifest.yaml", b"one")
                bundle.writestr("manifest.yaml", b"two")
        with self.assertRaises(ArtifactError) as raised:
            verify_tpx(duplicate)
        self.assertEqual(raised.exception.code, "duplicate_member")

        symlink = self.root / "symlink.tpx"
        info = zipfile.ZipInfo("wheelhouse/link.whl")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(symlink, "w") as bundle:
            bundle.writestr(info, b"target.whl")
        with self.assertRaises(ArtifactError) as raised:
            verify_tpx(symlink)
        self.assertEqual(raised.exception.code, "unsafe_member")

    def test_verify_enforces_package_and_member_size_limits(self):
        from app.core.plugin_artifact import ArtifactError, build_tpx, verify_tpx

        artifact = build_tpx(self._source(), self.root / "echo.tpx")
        with patch("app.core.plugin_artifact.MAX_PACKAGE_BYTES", 1):
            with self.assertRaises(ArtifactError) as raised:
                verify_tpx(artifact)
            self.assertEqual(raised.exception.code, "package_too_large")

        with patch("app.core.plugin_artifact.MAX_MEMBER_BYTES", 1):
            with self.assertRaises(ArtifactError) as raised:
                verify_tpx(artifact)
            self.assertEqual(raised.exception.code, "member_too_large")

        with patch("app.core.plugin_artifact.MAX_UNCOMPRESSED_BYTES", 1):
            with self.assertRaises(ArtifactError) as raised:
                verify_tpx(artifact)
            self.assertEqual(raised.exception.code, "package_uncompressed_too_large")


if __name__ == "__main__":
    unittest.main()
