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

    async def test_remote_catalog_refreshes_atomically_and_discovers_update(self):
        from app.core.plugin_catalog import PluginCatalog

        payload = yaml.safe_dump({
            "plugins": {
                "echo": {
                    "versions": {
                        "1.0.0": {
                            "url": "https://example.test/echo-1.0.0.tpx",
                            "sha256": "a" * 64,
                            "core_api": ">=1.0,<2.0",
                            "source": {"branch": "feature/echo", "commit": "a" * 40},
                        },
                        "1.2.0": {
                            "url": "https://example.test/echo-1.2.0.tpx",
                            "sha256": "b" * 64,
                            "core_api": ">=1.0,<2.0",
                            "source": {"branch": "feature/echo", "commit": "b" * 40},
                        },
                        "2.0.0": {
                            "url": "https://example.test/echo-2.0.0.tpx",
                            "sha256": "c" * 64,
                            "core_api": ">=2.0,<3.0",
                            "source": {"branch": "feature/echo", "commit": "c" * 40},
                        },
                    }
                }
            }
        }).encode()

        class Response(io.BytesIO):
            def geturl(self):
                return "https://cdn.example.test/catalog.yaml"

        catalog = PluginCatalog(
            "https://example.test/catalog.yaml",
            self.cache,
            opener=lambda *_args, **_kwargs: Response(payload),
        )

        await catalog.refresh()
        updates = await catalog.available_updates({"echo": "1.0.0"}, "1.0")

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].plugin_id, "echo")
        self.assertEqual(updates[0].current_version, "1.0.0")
        self.assertEqual(updates[0].target_version, "1.2.0")
        self.assertEqual(updates[0].source_commit, "b" * 40)
        self.assertTrue((self.cache / "catalog.yaml").is_file())

    async def test_failed_remote_refresh_preserves_last_valid_cache(self):
        from app.core.plugin_catalog import CatalogError, PluginCatalog

        valid = yaml.safe_dump({"plugins": {}}).encode()

        class Response(io.BytesIO):
            def __init__(self, payload, final_url="https://example.test/catalog.yaml"):
                super().__init__(payload)
                self.final_url = final_url

            def geturl(self):
                return self.final_url

        responses = iter([
            Response(valid),
            Response(b"plugins: [broken"),
        ])
        catalog = PluginCatalog(
            "https://example.test/catalog.yaml",
            self.cache,
            opener=lambda *_args, **_kwargs: next(responses),
        )
        await catalog.refresh()
        cached = (self.cache / "catalog.yaml").read_bytes()

        with self.assertRaises(CatalogError):
            await catalog.refresh()

        self.assertEqual((self.cache / "catalog.yaml").read_bytes(), cached)

        downgraded = PluginCatalog(
            "https://example.test/catalog.yaml",
            self.cache,
            opener=lambda *_args, **_kwargs: Response(
                valid, "http://example.test/catalog.yaml"
            ),
        )
        with self.assertRaises(CatalogError) as raised:
            await downgraded.refresh()
        self.assertEqual(raised.exception.code, "insecure_redirect")

    async def test_resolve_uses_valid_cache_when_remote_refresh_fails(self):
        from app.core.plugin_catalog import PluginCatalog

        artifact = b"cached release"
        digest = hashlib.sha256(artifact).hexdigest()
        catalog_payload = yaml.safe_dump({
            "plugins": {
                "echo": {
                    "versions": {
                        "1.1.0": {
                            "url": "https://example.test/echo-1.1.0.tpx",
                            "sha256": digest,
                        }
                    }
                }
            }
        }).encode()
        calls = 0

        def opener(request, timeout=None):
            del timeout
            nonlocal calls
            calls += 1
            if calls == 1:
                return io.BytesIO(catalog_payload)
            if request.full_url.endswith("catalog.yaml"):
                raise OSError("network down")
            return io.BytesIO(artifact)

        catalog = PluginCatalog(
            "https://example.test/catalog.yaml",
            self.cache,
            opener=opener,
        )
        await catalog.refresh()

        resolved = await catalog.resolve("echo@1.1.0")

        self.assertEqual(resolved.path.read_bytes(), artifact)
        self.assertEqual(resolved.expected_sha256, digest)

    async def test_remote_catalog_size_limit_is_enforced(self):
        from app.core.plugin_catalog import CatalogError, PluginCatalog

        catalog = PluginCatalog(
            "https://example.test/catalog.yaml",
            self.cache,
            opener=lambda *_args, **_kwargs: io.BytesIO(b"plugins: {}\n"),
            max_catalog_bytes=4,
        )

        with self.assertRaises(CatalogError) as raised:
            await catalog.refresh()
        self.assertEqual(raised.exception.code, "catalog_too_large")

    async def test_available_plugins_selects_latest_and_explains_dependencies(self):
        from app.core.plugin_catalog import PluginCatalog

        def release(version, *, core_api=">=1.0,<2.0", provides=(), requires=()):
            return {
                "url": f"https://example.test/{version}.tpx",
                "sha256": version[0] * 64,
                "core_api": core_api,
                "provides": list(provides),
                "requires": list(requires),
                "source": {
                    "branch": "feature/test",
                    "commit": version[0] * 40,
                },
            }

        self.catalog_path.write_text(yaml.safe_dump({
            "plugins": {
                "installed": {"versions": {"1.0.0": release("1.0.0")}},
                "provider": {"versions": {
                    "1.0.0": release("1.0.0", provides=("storage.provider",)),
                    "1.2.0": release("2.0.0", provides=("storage.provider",)),
                    "1.3.0-rc1": release("3.0.0", provides=("storage.provider",)),
                    "2.0.0": release(
                        "4.0.0",
                        core_api=">=2.0,<3.0",
                        provides=("storage.provider",),
                    ),
                }},
                "consumer": {"versions": {"1.0.0": release(
                    "5.0.0",
                    requires=("storage.provider",),
                )}},
                "orphan": {"versions": {"1.0.0": release(
                    "6.0.0",
                    requires=("missing.provider",),
                )}},
            }
        }), encoding="utf-8")

        catalog = PluginCatalog(self.catalog_path, self.cache)
        candidates = await catalog.available_plugins(
            {"installed"},
            "1.0",
            available_capabilities=(),
        )

        by_id = {item.plugin_id: item for item in candidates}
        self.assertEqual(set(by_id), {"provider", "consumer", "orphan"})
        self.assertEqual(by_id["provider"].target_version, "1.2.0")
        self.assertTrue(by_id["provider"].ready)
        self.assertEqual(
            by_id["consumer"].missing_capabilities,
            ("storage.provider",),
        )
        self.assertEqual(by_id["consumer"].dependency_plugins, ("provider",))
        self.assertFalse(by_id["consumer"].ready)
        self.assertEqual(by_id["orphan"].dependency_plugins, ())

        ready = await catalog.available_plugins(
            {"installed"},
            "1.0",
            available_capabilities={"storage.provider"},
        )
        self.assertTrue(
            next(item for item in ready if item.plugin_id == "consumer").ready
        )


if __name__ == "__main__":
    unittest.main()
