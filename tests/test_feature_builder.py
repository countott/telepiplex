import tempfile
import unittest
import zipfile
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _write_wheel(path: Path, metadata: str):
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("example-1.0.0.dist-info/METADATA", metadata)


class FeatureBuilderTest(unittest.TestCase):
    def test_coordinated_feature_1_1_artifact_set_when_supplied(self):
        from app.core.plugin_artifact import verify_tpx

        raw = os.environ.get("TELEPIPLEX_OPERATION_ARTIFACTS", "")
        if not raw:
            self.skipTest(
                "set TELEPIPLEX_OPERATION_ARTIFACTS for the release matrix"
            )
        artifacts = [Path(value) for value in raw.split(os.pathsep) if value]
        self.assertEqual(len(artifacts), 4)
        verified = [verify_tpx(path) for path in artifacts]
        self.assertEqual(
            {item.manifest.plugin_id for item in verified},
            {"media-search", "open115", "renaming", "plex-management"},
        )
        for artifact in verified:
            with self.subTest(plugin_id=artifact.manifest.plugin_id):
                self.assertEqual(artifact.manifest.version, "1.1.0")
                self.assertTrue(artifact.manifest.supports_core("1.1"))

    def test_builds_installable_echo_tpx_from_source_branch(self):
        from app.core.plugin_artifact import verify_tpx
        from tools.build_feature import build_feature_artifact

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "echo.tpx"
            build_feature_artifact(
                ROOT / "examples/echo_feature",
                output,
                sdk_source=ROOT / "sdk",
                repository="git@example.test:telepiplex.git",
                branch="feature/echo",
                commit="b" * 40,
            )

            verified = verify_tpx(output)
            self.assertEqual(verified.manifest.plugin_id, "echo")
            self.assertEqual(verified.manifest.source.commit, "b" * 40)
            self.assertTrue(any(name.startswith("wheelhouse/telepiplex_plugin_sdk-") for name in verified.members))

    def test_rejects_feature_source_importing_core_or_telegram(self):
        from tools.build_feature import FeatureBuildError, validate_feature_imports

        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "src/plugin"
            source.mkdir(parents=True)
            for name, content in (
                ("core.py", "from app.core import plugin_manager\n"),
                ("init.py", "import init\n"),
                ("telegram.py", "from telegram import Update\n"),
            ):
                path = source / name
                path.write_text(content, encoding="utf-8")
                with self.subTest(name=name), self.assertRaises(FeatureBuildError):
                    validate_feature_imports(Path(tmpdir))
                path.unlink()

    def test_rejects_importing_a_sibling_feature_package(self):
        from tools.build_feature import FeatureBuildError, validate_feature_imports

        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "src/telepiplex_example"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text(
                "import telepiplex_open115\n", encoding="utf-8"
            )
            with self.assertRaises(FeatureBuildError):
                validate_feature_imports(Path(tmpdir))

    def test_rejects_sibling_feature_distribution_in_requirements(self):
        from tools.build_feature import FeatureBuildError, validate_feature_requirements

        with self.assertRaises(FeatureBuildError):
            validate_feature_requirements("requests\ntelepiplex-open115==1.0.0\n")

    def test_accepts_named_third_party_and_sdk_requirements(self):
        from tools.build_feature import validate_feature_requirements

        validate_feature_requirements(
            "requests>=2\n"
            "telepiplex-plugin-sdk==1.0.0\n"
            "telepiplex..plugin__sdk==1.0.0\n"
            'requests; platform_release == "foo@bar"\n'
        )

    def test_rejects_direct_reference_without_whitespace(self):
        from tools.build_feature import FeatureBuildError, validate_feature_requirements

        with self.assertRaises(FeatureBuildError):
            validate_feature_requirements(
                "requests@git+ssh:git@example.com:repo\n"
            )

    def test_rejects_bare_vcs_urls(self):
        from tools.build_feature import FeatureBuildError, validate_feature_requirements

        for requirement in (
            "git+ssh:example.com:repo\n",
            "hg+ssh:example.com:repo\n",
            "git+file:../repo\n",
        ):
            with self.subTest(requirement=requirement), self.assertRaises(
                FeatureBuildError
            ):
                validate_feature_requirements(requirement)

    def test_rejects_unsafe_requirement_sources(self):
        from tools.build_feature import FeatureBuildError, validate_feature_requirements

        for requirement in (
            "-r sibling.txt\n",
            "https://example.test/telepiplex_open115.whl\n",
            "./telepiplex_open115.whl\n",
        ):
            with self.subTest(requirement=requirement), self.assertRaises(
                FeatureBuildError
            ):
                validate_feature_requirements(requirement)

    def test_rejects_sibling_feature_distribution_in_plugin_wheel_metadata(self):
        from tools.build_feature import FeatureBuildError, validate_plugin_wheel

        with tempfile.TemporaryDirectory() as tmpdir:
            wheel = Path(tmpdir) / "plugin.whl"
            _write_wheel(
                wheel,
                "Metadata-Version: 2.1\n"
                "Name: example\n"
                "Version: 1.0.0\n"
                "Requires-Dist: telepiplex-open115\n",
            )

            with self.assertRaises(FeatureBuildError):
                validate_plugin_wheel(wheel)

    def test_rejects_direct_reference_in_plugin_wheel_metadata(self):
        from tools.build_feature import FeatureBuildError, validate_plugin_wheel

        with tempfile.TemporaryDirectory() as tmpdir:
            wheel = Path(tmpdir) / "plugin.whl"
            _write_wheel(
                wheel,
                "Metadata-Version: 2.1\n"
                "Name: example\n"
                "Version: 1.0.0\n"
                "Requires-Dist: requests @ https://example.invalid/pkg.whl\n",
            )

            with self.assertRaises(FeatureBuildError):
                validate_plugin_wheel(wheel)

    def test_rejects_empty_plugin_wheel_metadata(self):
        from tools.build_feature import FeatureBuildError, validate_plugin_wheel

        with tempfile.TemporaryDirectory() as tmpdir:
            wheel = Path(tmpdir) / "plugin.whl"
            _write_wheel(wheel, "")

            with self.assertRaises(FeatureBuildError):
                validate_plugin_wheel(wheel)

    def test_rejects_invalid_requires_dist_in_plugin_wheel_metadata(self):
        from tools.build_feature import FeatureBuildError, validate_plugin_wheel

        with tempfile.TemporaryDirectory() as tmpdir:
            wheel = Path(tmpdir) / "plugin.whl"
            _write_wheel(
                wheel,
                "Metadata-Version: 2.1\n"
                "Name: example\n"
                "Version: 1.0.0\n"
                "Requires-Dist: requests, telepiplex-open115\n",
            )

            with self.assertRaises(FeatureBuildError):
                validate_plugin_wheel(wheel)

    def test_rejects_invalid_plugin_wheel_core_metadata_fields(self):
        from tools.build_feature import FeatureBuildError, validate_plugin_wheel

        for field, value in (
            ("Metadata-Version", "2.1 trailing"),
            ("Version", "not valid!"),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                metadata = {
                    "Metadata-Version": "2.1",
                    "Name": "example",
                    "Version": "1.0.0",
                }
                metadata[field] = value
                wheel = Path(tmpdir) / "plugin.whl"
                _write_wheel(
                    wheel,
                    "".join(f"{name}: {item}\n" for name, item in metadata.items()),
                )

                with self.subTest(field=field), self.assertRaises(FeatureBuildError):
                    validate_plugin_wheel(wheel)

    def test_accepts_legacy_metadata_with_license_file(self):
        from tools.build_feature import validate_plugin_wheel

        with tempfile.TemporaryDirectory() as tmpdir:
            wheel = Path(tmpdir) / "plugin.whl"
            _write_wheel(
                wheel,
                "Metadata-Version: 2.3\n"
                "Name: annotated-types\n"
                "Version: 0.7.0\n"
                "License-File: LICENSE\n",
            )

            validate_plugin_wheel(wheel)

    def test_rejects_unsupported_plugin_metadata_version(self):
        from tools.build_feature import FeatureBuildError, validate_plugin_wheel

        with tempfile.TemporaryDirectory() as tmpdir:
            wheel = Path(tmpdir) / "plugin.whl"
            _write_wheel(
                wheel,
                "Metadata-Version: 999.999\n"
                "Name: example\n"
                "Version: 1.0.0\n",
            )

            with self.assertRaises(FeatureBuildError):
                validate_plugin_wheel(wheel)

    def test_rejects_duplicate_single_use_plugin_metadata_fields(self):
        from tools.build_feature import FeatureBuildError, validate_plugin_wheel

        for field, value in (
            ("Metadata-Version", "2.1"),
            ("Name", "example"),
            ("Version", "1.0.0"),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                wheel = Path(tmpdir) / "plugin.whl"
                _write_wheel(
                    wheel,
                    "Metadata-Version: 2.1\n"
                    "Name: example\n"
                    "Version: 1.0.0\n"
                    f"{field}: {value}\n",
                )

                with self.subTest(field=field), self.assertRaises(FeatureBuildError):
                    validate_plugin_wheel(wheel)

    def test_rejects_malformed_plugin_wheel_metadata(self):
        from tools.build_feature import FeatureBuildError, validate_plugin_wheel

        with tempfile.TemporaryDirectory() as tmpdir:
            wheel = Path(tmpdir) / "plugin.whl"
            wheel.write_bytes(b"not a wheel")

            with self.assertRaises(FeatureBuildError):
                validate_plugin_wheel(wheel)

    def test_rejects_sibling_feature_distribution_in_wheelhouse_metadata(self):
        from tools.build_feature import FeatureBuildError, validate_wheelhouse

        with tempfile.TemporaryDirectory() as tmpdir:
            wheelhouse = Path(tmpdir)
            _write_wheel(
                wheelhouse / "sibling.whl",
                "Metadata-Version: 2.1\n"
                "Name: telepiplex-open115\n"
                "Version: 1.0.0\n",
            )

            with self.assertRaises(FeatureBuildError):
                validate_wheelhouse(wheelhouse)

    def test_rejects_invalid_distribution_name_in_wheelhouse_metadata(self):
        from tools.build_feature import FeatureBuildError, validate_wheelhouse

        with tempfile.TemporaryDirectory() as tmpdir:
            wheelhouse = Path(tmpdir)
            _write_wheel(
                wheelhouse / "invalid.whl",
                "Metadata-Version: 2.1\n"
                "Name: requests invalid\n"
                "Version: 1.0.0\n",
            )

            with self.assertRaises(FeatureBuildError):
                validate_wheelhouse(wheelhouse)

    def test_rejects_unsafe_requires_dist_in_wheelhouse_metadata(self):
        from tools.build_feature import FeatureBuildError, validate_wheelhouse

        for requirement in (
            "requests @ https://example.invalid/pkg.whl",
            "telepiplex-open115",
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                wheelhouse = Path(tmpdir)
                _write_wheel(
                    wheelhouse / "dependency.whl",
                    "Metadata-Version: 2.1\n"
                    "Name: requests\n"
                    "Version: 1.0.0\n"
                    f"Requires-Dist: {requirement}\n",
                )

                with self.subTest(requirement=requirement), self.assertRaises(
                    FeatureBuildError
                ):
                    validate_wheelhouse(wheelhouse)


if __name__ == "__main__":
    unittest.main()
