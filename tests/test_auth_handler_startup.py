import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from app.handlers.auth_handler import create_115_client_for_auth_only


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


if __name__ == "__main__":
    unittest.main()
