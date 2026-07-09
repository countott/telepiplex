import sys
import importlib.util
import json
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

def _module_available(name):
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


if not _module_available("yaml"):
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = Mock(return_value={})
    sys.modules["yaml"] = yaml_stub

if not _module_available("telethon"):
    telethon_stub = types.ModuleType("telethon")
    telethon_stub.TelegramClient = Mock()
    sys.modules["telethon"] = telethon_stub

if not _module_available("requests"):
    requests_stub = types.ModuleType("requests")
    requests_stub.get = Mock()
    requests_stub.post = Mock()
    sys.modules["requests"] = requests_stub

if not _module_available("qrcode"):
    sys.modules["qrcode"] = types.ModuleType("qrcode")

if not _module_available("telegram"):
    telegram_stub = types.ModuleType("telegram")
    telegram_stub.Bot = Mock()
    sys.modules["telegram"] = telegram_stub

if not _module_available("telegram.helpers"):
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

        self.assertIsNone(init.openapi_115)
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

    def test_initialize_115open_without_direct_tokens_does_not_log_missing_token_file_path(self):
        init.bot_config = {
            "115_app_id": None,
            "access_token": "",
            "refresh_token": "",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            init.TOKEN_FILE = str(Path(tmpdir) / "missing_115_tokens.json")

            self.assertFalse(init.initialize_115open())

        self.assertIsNone(init.openapi_115)
        errors = [call.args[0] for call in init.logger.error.call_args_list]
        self.assertIn("115 OpenAPI客户端初始化失败: 无法获取有效的token", errors)
        self.assertFalse(any("No such file or directory" in message for message in errors))

    def test_offline_download_specify_path_returns_false_when_save_path_info_unavailable(self):
        api = object.__new__(OpenAPI_115)
        api.base_url = "https://proapi.115.com"
        api.get_file_info = Mock(return_value=None)
        api.create_dir_recursive = Mock(return_value=None)
        api._make_api_request = Mock()
        api._get_headers = Mock(return_value={})
        api.refresh_access_token = Mock()

        result = api.offline_download_specify_path(
            "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
            "/动画剧集",
        )

        self.assertFalse(result)
        api._make_api_request.assert_not_called()
        init.logger.warn.assert_any_call("离线下载目录不可用，无法创建任务: /动画剧集")

    @patch("app.core.open_115.time.sleep", return_value=None)
    def test_check_offline_download_waits_for_task_to_appear_after_empty_list(self, sleep_mock):
        api = object.__new__(OpenAPI_115)
        target = "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567"
        api.get_offline_tasks = Mock(
            side_effect=[
                [],
                [
                    {
                        "url": target,
                        "name": "Movie.Release",
                        "info_hash": "HASH",
                        "status": 2,
                        "percentDone": 100,
                    }
                ],
            ]
        )

        self.assertEqual(
            api.check_offline_download_success(target, offline_timeout=20),
            (True, "Movie.Release", "HASH", 100),
        )
        sleep_mock.assert_called_once_with(10)

    @patch("app.core.open_115.time.sleep", return_value=None)
    def test_check_offline_download_times_out_when_target_url_never_matches(self, sleep_mock):
        api = object.__new__(OpenAPI_115)
        target = "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567"
        api.get_offline_tasks = Mock(
            side_effect=[
                [
                    {
                        "url": "magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                        "name": "Other.Release",
                        "info_hash": "OTHER",
                        "status": 1,
                        "percentDone": 10,
                    }
                ],
                [
                    {
                        "url": "magnet:?xt=urn:btih:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
                        "name": "Another.Release",
                        "info_hash": "ANOTHER",
                        "status": 1,
                        "percentDone": 30,
                    }
                ],
                AssertionError("polling did not stop at timeout"),
            ]
        )

        self.assertEqual(
            api.check_offline_download_success(target, offline_timeout=20),
            (False, "", "", 0),
        )
        self.assertEqual(sleep_mock.call_count, 2)

    @patch("app.core.open_115.requests.get")
    def test_make_api_request_uses_timeout_and_redacts_failure_response(self, get_mock):
        init.bot_config = {"open115": {"timeout": 9}}
        api = object.__new__(OpenAPI_115)
        api.lock = threading.Lock()
        api.last_req_time = 0
        api.lifetime_vip = False
        api.request_count = 0
        api._get_headers = Mock(return_value={"Authorization": "Bearer secret-access"})
        response = Mock()
        response.status_code = 500
        response.text = (
            '{"access_token":"secret-access",'
            '"url":"https://download.example/private.mkv"}'
        )
        get_mock.return_value = response

        result = api._make_api_request("GET", "https://proapi.115.com/open/test")

        self.assertEqual(result["code"], 500)
        self.assertEqual(get_mock.call_args.kwargs["timeout"], 9)
        log_message = init.logger.warn.call_args.args[0]
        self.assertIn("***redacted***", log_message)
        self.assertNotIn("secret-access", log_message)
        self.assertNotIn("https://download.example/private.mkv", log_message)

    def test_get_upload_token_redacts_sensitive_response_log(self):
        api = object.__new__(OpenAPI_115)
        api.base_url = "https://proapi.115.com"
        api._make_api_request = Mock(
            return_value={
                "code": 0,
                "data": {
                    "AccessKeyId": "upload-access-key",
                    "AccessKeySecret": "upload-secret",
                    "SecurityToken": "upload-token",
                    "endpoint": "https://oss.example/private",
                },
            }
        )

        self.assertEqual(api.get_upload_token()["AccessKeyId"], "upload-access-key")

        log_message = init.logger.info.call_args.args[0]
        self.assertIn("***redacted***", log_message)
        self.assertNotIn("upload-secret", log_message)
        self.assertNotIn("upload-token", log_message)
        self.assertNotIn("https://oss.example/private", log_message)


if __name__ == "__main__":
    unittest.main()
