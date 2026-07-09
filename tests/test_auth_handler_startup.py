import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import init

from app.handlers.auth_handler import check_115_app_id, create_115_client_for_auth_only


class AuthHandlerStartupTest(unittest.TestCase):
    def test_create_115_client_for_auth_only_has_runtime_fields_without_refreshing_token(self):
        api = create_115_client_for_auth_only()

        self.assertEqual(api.access_token, "")
        self.assertEqual(api.refresh_token, "")
        self.assertEqual(api.base_url, "https://proapi.115.com")
        self.assertEqual(api.request_count, 0)
        self.assertEqual(api.cache_hit, 0)
        self.assertEqual(api.file_info_cache, {})
        self.assertTrue(hasattr(api, "lock"))

    def test_check_115_app_id_rejects_direct_token_mode_values(self):
        init.logger = Mock()
        for value in (None, "", "null", "none", "your_115_app_id"):
            with self.subTest(value=value):
                init.bot_config = {"115_app_id": value}
                self.assertFalse(check_115_app_id())

    def test_check_115_app_id_accepts_real_app_id(self):
        init.logger = Mock()
        init.bot_config = {"115_app_id": "real-app-id"}

        self.assertTrue(check_115_app_id())


if __name__ == "__main__":
    unittest.main()
