import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FeatureBuilderTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
