import hashlib
import io
import tempfile
import unittest
from pathlib import Path

import yaml


class PluginCatalogTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog_path = self.root / "catalog.yaml"
        self.cache = self.root / "cache"

    def tearDown(self):
        self.temp.cleanup()

    def _write_catalog(self, entry):
        self.catalog_path.write_text(
            yaml.safe_dump({"plugins": {"echo": {"versions": {"1.0.0": entry}}}}),
            encoding="utf-8",
        )

    async def test_resolves_existing_direct_tpx_path(self):
        from app.core.plugin_catalog import PluginCatalog

        artifact = self.root / "echo.tpx"
        artifact.write_bytes(b"artifact")
        resolved = await PluginCatalog(self.catalog_path, self.cache).resolve(str(artifact))
        self.assertEqual(resolved.path, artifact.resolve())
        self.assertEqual(resolved.expected_sha256, "")

    async def test_resolves_relative_catalog_artifact_with_pinned_digest(self):
        from app.core.plugin_catalog import PluginCatalog

        artifact = self.root / "dist" / "echo.tpx"
        artifact.parent.mkdir()
        artifact.write_bytes(b"artifact")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self._write_catalog({"path": "dist/echo.tpx", "sha256": digest})

        resolved = await PluginCatalog(self.catalog_path, self.cache).resolve("echo@1.0.0")
        self.assertEqual(resolved.path, artifact.resolve())
        self.assertEqual(resolved.expected_sha256, digest)

    async def test_downloads_https_artifact_and_verifies_digest(self):
        from app.core.plugin_catalog import PluginCatalog

        payload = b"downloaded artifact"
        digest = hashlib.sha256(payload).hexdigest()
        self._write_catalog({"url": "https://example.test/echo.tpx", "sha256": digest})
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            return io.BytesIO(payload)

        resolved = await PluginCatalog(
            self.catalog_path,
            self.cache,
            opener=opener,
        ).resolve("echo@1.0.0")

        self.assertEqual(resolved.path.read_bytes(), payload)
        self.assertEqual(resolved.expected_sha256, digest)
        self.assertEqual(calls[0][0], "https://example.test/echo.tpx")

    async def test_rejects_unpinned_or_non_https_catalog_entry(self):
        from app.core.plugin_catalog import CatalogError, PluginCatalog

        catalog = PluginCatalog(self.catalog_path, self.cache)
        for entry in (
            {"url": "http://example.test/echo.tpx", "sha256": "a" * 64},
            {"url": "https://example.test/echo.tpx"},
            {"path": "dist/echo.tpx", "sha256": "bad"},
        ):
            with self.subTest(entry=entry):
                self._write_catalog(entry)
                with self.assertRaises(CatalogError):
                    await catalog.resolve("echo@1.0.0")

    async def test_rejects_unknown_reference_and_oversized_download(self):
        from app.core.plugin_catalog import CatalogError, PluginCatalog

        self.catalog_path.write_text("plugins: {}\n", encoding="utf-8")
        with self.assertRaises(CatalogError):
            await PluginCatalog(self.catalog_path, self.cache).resolve("missing@1.0.0")

        payload = b"12345"
        self._write_catalog({
            "url": "https://example.test/echo.tpx",
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
        with self.assertRaises(CatalogError):
            await PluginCatalog(
                self.catalog_path,
                self.cache,
                opener=lambda *_args, **_kwargs: io.BytesIO(payload),
                max_download_bytes=4,
            ).resolve("echo@1.0.0")

    async def test_rejects_https_download_redirected_to_plain_http(self):
        from app.core.plugin_catalog import CatalogError, PluginCatalog

        payload = b"artifact"
        self._write_catalog({
            "url": "https://example.test/echo.tpx",
            "sha256": hashlib.sha256(payload).hexdigest(),
        })

        class DowngradedResponse(io.BytesIO):
            def geturl(self):
                return "http://mirror.test/echo.tpx"

        with self.assertRaises(CatalogError) as raised:
            await PluginCatalog(
                self.catalog_path,
                self.cache,
                opener=lambda *_args, **_kwargs: DowngradedResponse(payload),
            ).resolve("echo@1.0.0")
        self.assertEqual(raised.exception.code, "insecure_redirect")


if __name__ == "__main__":
    unittest.main()
