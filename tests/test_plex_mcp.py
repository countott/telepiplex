import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class PlexMcpTest(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.server_status.return_value = {"online": True}
        self.service.list_libraries.return_value = [{"id": "12"}]
        self.service.list_audio_candidates.return_value = [{
            "part_id": 11,
            "file": "/Movies/Movie.mkv",
            "candidates": [{"id": 21, "language_code": "jpn"}],
        }]
        self.service.list_subtitle_candidates.return_value = [{
            "part_id": 11,
            "file": "/Movies/Movie.mkv",
            "candidates": [{"id": 31, "language_code": "chi"}],
        }]
        self.service.prepare_operation.return_value = {
            "status": "confirmation_required",
            "confirmation_token": "once",
        }
        self.service.apply_operation.return_value = {"status": "applied"}

    def test_non_loopback_requires_token(self):
        from telepiplex_plex.mcp_server import PlexMcpConfigError, create_plex_mcp_app

        with self.assertRaises(PlexMcpConfigError):
            create_plex_mcp_app(self.service, {"host": "0.0.0.0", "auth_token": ""})

    def test_registers_exact_approved_tool_surface(self):
        from telepiplex_plex.mcp_server import create_plex_mcp

        mcp = create_plex_mcp(self.service, {})
        names = {tool.name for tool in asyncio.run(mcp.list_tools())}

        self.assertEqual(names, {
            "plex_server_status", "plex_list_libraries", "plex_inspect_item",
            "plex_list_artwork_candidates", "plex_list_audio_candidates",
            "plex_list_subtitle_candidates", "plex_get_job", "plex_list_jobs",
            "plex_scan_library",
            "plex_set_textless_poster", "plex_select_original_audio",
            "plex_select_chi_subtitle", "plex_retry_job",
        })

    def test_read_tool_calls_shared_service(self):
        from telepiplex_plex.mcp_server import create_plex_mcp

        mcp = create_plex_mcp(self.service, {})
        result = asyncio.run(
            mcp._tool_manager.call_tool("plex_server_status", {}, convert_result=False)
        )

        self.assertEqual(result, {"online": True})
        self.service.server_status.assert_called_once_with()

    def test_audio_and_subtitle_candidate_tools_call_shared_service(self):
        from telepiplex_plex.mcp_server import create_plex_mcp

        mcp = create_plex_mcp(self.service, {})
        audio = asyncio.run(
            mcp._tool_manager.call_tool(
                "plex_list_audio_candidates",
                {"rating_key": "42"},
                convert_result=False,
            )
        )
        subtitle = asyncio.run(
            mcp._tool_manager.call_tool(
                "plex_list_subtitle_candidates",
                {"rating_key": "42"},
                convert_result=False,
            )
        )

        self.assertEqual(audio[0]["candidates"][0]["id"], 21)
        self.assertEqual(subtitle[0]["candidates"][0]["id"], 31)
        self.service.list_audio_candidates.assert_called_once_with("42")
        self.service.list_subtitle_candidates.assert_called_once_with("42")

    def test_every_write_tool_prepares_then_applies_with_confirmation_token(self):
        from telepiplex_plex.mcp_server import create_plex_mcp

        cases = {
            "plex_scan_library": {"library_id": "12"},
            "plex_set_textless_poster": {
                "rating_key": "42",
                "url": "https://image/poster.jpg",
            },
            "plex_select_original_audio": {
                "rating_key": "42",
                "part_id": 11,
                "stream_id": 21,
            },
            "plex_select_chi_subtitle": {
                "rating_key": "42",
                "part_id": 11,
                "stream_id": 31,
            },
            "plex_retry_job": {"job_id": 7},
        }

        for name, arguments in cases.items():
            with self.subTest(name=name):
                self.service.prepare_operation.reset_mock()
                self.service.apply_operation.reset_mock()
                mcp = create_plex_mcp(self.service, {})
                preview = asyncio.run(
                    mcp._tool_manager.call_tool(
                        name,
                        arguments,
                        convert_result=False,
                    )
                )
                applied = asyncio.run(
                    mcp._tool_manager.call_tool(
                        name,
                        {**arguments, "confirmation_token": "once"},
                        convert_result=False,
                    )
                )

                self.assertEqual(preview["confirmation_token"], "once")
                self.assertEqual(applied["status"], "applied")
                self.service.prepare_operation.assert_called_once_with(
                    name,
                    arguments,
                )
                self.service.apply_operation.assert_called_once_with(
                    name,
                    arguments,
                    "once",
                )

    def test_obsolete_match_and_metadata_tools_are_not_registered(self):
        from telepiplex_plex.mcp_server import create_plex_mcp

        mcp = create_plex_mcp(self.service, {})
        names = {tool.name for tool in asyncio.run(mcp.list_tools())}

        self.assertTrue({
            "plex_list_match_candidates",
            "plex_fix_match",
            "plex_refresh_chinese_metadata",
            "plex_run_management_pipeline",
            "plex_apply_metadata_batch",
        }.isdisjoint(names))

    def test_read_and_write_annotations_match_tool_effects(self):
        from telepiplex_plex.mcp_server import create_plex_mcp

        tools = {
            tool.name: tool
            for tool in asyncio.run(create_plex_mcp(self.service, {}).list_tools())
        }
        read_tools = {
            "plex_server_status",
            "plex_list_libraries",
            "plex_inspect_item",
            "plex_list_artwork_candidates",
            "plex_list_audio_candidates",
            "plex_list_subtitle_candidates",
            "plex_get_job",
            "plex_list_jobs",
        }
        write_tools = set(tools) - read_tools

        for name in read_tools:
            with self.subTest(name=name):
                self.assertTrue(tools[name].annotations.readOnlyHint)
                self.assertFalse(tools[name].annotations.destructiveHint)
        for name in write_tools:
            with self.subTest(name=name):
                self.assertFalse(tools[name].annotations.readOnlyHint)
                self.assertTrue(tools[name].annotations.destructiveHint)

    def test_bearer_auth_rejects_missing_token_before_mcp_dispatch(self):
        from telepiplex_plex.mcp_server import create_plex_mcp_app

        app = create_plex_mcp_app(
            self.service,
            {"host": "0.0.0.0", "path": "/mcp", "auth_token": "secret"},
        )

        async def make_requests():
            transport = httpx.ASGITransport(app=app)
            async with app.app.router.lifespan_context(app.app):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    unauthorized = await client.post("/mcp")
                    authorized = await client.post(
                        "/mcp",
                        headers={"Authorization": "Bearer secret"},
                    )
            return unauthorized, authorized

        unauthorized, authorized = asyncio.run(make_requests())

        self.assertEqual(unauthorized.status_code, 401)
        self.assertNotEqual(authorized.status_code, 401)


if __name__ == "__main__":
    unittest.main()
