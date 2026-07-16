import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from starlette.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))


class PlexMcpTest(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.server_status.return_value = {"online": True}
        self.service.list_libraries.return_value = [{"id": "12"}]
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
            "plex_list_artwork_candidates", "plex_get_job", "plex_list_jobs",
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

    def test_write_prepare_and_apply_use_confirmation_token(self):
        from telepiplex_plex.mcp_server import create_plex_mcp

        mcp = create_plex_mcp(self.service, {})
        arguments = {
            "rating_key": "42",
            "url": "https://image/poster.jpg",
        }
        preview = asyncio.run(
            mcp._tool_manager.call_tool(
                "plex_set_textless_poster",
                arguments,
                convert_result=False,
            )
        )
        applied = asyncio.run(
            mcp._tool_manager.call_tool(
                "plex_set_textless_poster",
                {**arguments, "confirmation_token": "once"},
                convert_result=False,
            )
        )

        self.assertEqual(preview["confirmation_token"], "once")
        self.assertEqual(applied["status"], "applied")
        self.service.prepare_operation.assert_called_once_with(
            "plex_set_textless_poster",
            arguments,
        )
        self.service.apply_operation.assert_called_once()

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

    def test_bearer_auth_rejects_missing_token_before_mcp_dispatch(self):
        from telepiplex_plex.mcp_server import create_plex_mcp_app

        app = create_plex_mcp_app(
            self.service,
            {"host": "0.0.0.0", "path": "/mcp", "auth_token": "secret"},
        )
        with TestClient(app) as client:
            unauthorized = client.post("/mcp")
            authorized = client.post("/mcp", headers={"Authorization": "Bearer secret"})

        self.assertEqual(unauthorized.status_code, 401)
        self.assertNotEqual(authorized.status_code, 401)


if __name__ == "__main__":
    unittest.main()
