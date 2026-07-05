import sys
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = Mock(return_value={})
    sys.modules["yaml"] = yaml_stub

if "telethon" not in sys.modules:
    telethon_stub = types.ModuleType("telethon")
    telethon_stub.TelegramClient = Mock()
    sys.modules["telethon"] = telethon_stub

if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_stub.get = Mock()
    requests_stub.post = Mock()
    sys.modules["requests"] = requests_stub

if "qrcode" not in sys.modules:
    sys.modules["qrcode"] = types.ModuleType("qrcode")

if "telegram" not in sys.modules:
    telegram_stub = types.ModuleType("telegram")
    telegram_stub.Bot = Mock()
    sys.modules["telegram"] = telegram_stub

if "telegram.helpers" not in sys.modules:
    telegram_helpers_stub = types.ModuleType("telegram.helpers")
    telegram_helpers_stub.escape_markdown = lambda text, version=2: text
    sys.modules["telegram.helpers"] = telegram_helpers_stub

if "app.utils.message_queue" not in sys.modules:
    message_queue_stub = types.ModuleType("app.utils.message_queue")
    message_queue_stub.add_task_to_queue = Mock()
    sys.modules["app.utils.message_queue"] = message_queue_stub

if "app.utils.alioss" not in sys.modules:
    alioss_stub = types.ModuleType("app.utils.alioss")
    alioss_stub.upload_file_to_oss = Mock(return_value=False)
    sys.modules["app.utils.alioss"] = alioss_stub

import init

from app.core.open_115 import OpenAPI_115


TOKEN_ERROR_RESPONSE = {
    "state": False,
    "message": "access_token 无效",
    "code": 40140125,
    "data": [],
}

TOKEN_VALIDATION_ERROR_RESPONSE = {
    "state": False,
    "message": "access_token 校验失败",
    "code": 40140126,
    "data": [],
}


class Open115StartupTest(unittest.TestCase):
    def setUp(self):
        init.logger = Mock()

    def test_welcome_message_skips_invalid_user_info_without_crashing(self):
        api = object.__new__(OpenAPI_115)
        api.get_user_info = Mock(return_value=TOKEN_ERROR_RESPONSE)
        api.get_quota_info = Mock(return_value={"used": 0, "count": 0})

        self.assertEqual(api.welcome_message(), ("", "", "", ""))

    def test_initialize_115open_rejects_api_error_response(self):
        def set_expired_tokens(api):
            api.access_token = "expired-access-token"
            api.refresh_token = "expired-refresh-token"

        with patch.object(OpenAPI_115, "get_token", set_expired_tokens), patch.object(
            OpenAPI_115,
            "get_user_info",
            return_value=TOKEN_ERROR_RESPONSE,
        ):
            self.assertFalse(init.initialize_115open())

        init.logger.error.assert_called_with("115 OpenAPI客户端初始化失败: OpenAPI测试失败！")

    def test_get_user_info_refreshes_on_access_token_validation_failure(self):
        api = object.__new__(OpenAPI_115)
        api.base_url = "https://proapi.115.com"
        api.refresh_access_token = Mock()
        api._make_api_request = Mock(
            side_effect=[
                TOKEN_VALIDATION_ERROR_RESPONSE,
                {"code": 0, "data": {"rt_space_info": {}}},
            ]
        )

        self.assertEqual(api.get_user_info(), {"rt_space_info": {}})
        api.refresh_access_token.assert_called_once()

    def test_get_token_prefers_config_tokens_over_stale_token_file_in_direct_token_mode(self):
        init.bot_config = {
            "115_app_id": None,
            "access_token": "config-access-token",
            "refresh_token": "config-refresh-token",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            token_file = Path(tmpdir) / "115_tokens.json"
            token_file.write_text(
                json.dumps(
                    {
                        "access_token": "stale-access-token",
                        "refresh_token": "stale-refresh-token",
                    }
                ),
                encoding="utf-8",
            )
            init.TOKEN_FILE = str(token_file)

            api = OpenAPI_115()

            self.assertEqual(api.access_token, "config-access-token")
            self.assertEqual(api.refresh_token, "config-refresh-token")
            self.assertEqual(
                json.loads(token_file.read_text(encoding="utf-8")),
                {
                    "access_token": "config-access-token",
                    "refresh_token": "config-refresh-token",
                },
            )


if __name__ == "__main__":
    unittest.main()
