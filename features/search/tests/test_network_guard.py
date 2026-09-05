import socket
import tempfile
from pathlib import Path
import unittest

import requests

from tests.network_guard import ExternalNetworkGuard, NetworkAccessBlocked


class ExternalNetworkGuardTest(unittest.TestCase):
    def test_caught_request_is_still_reported_when_test_finishes(self):
        guard = ExternalNetworkGuard()

        with guard.active():
            try:
                requests.get("https://example.invalid/hidden", timeout=0.01)
            except NetworkAccessBlocked:
                pass

        with self.assertRaisesRegex(
            AssertionError,
            "example.invalid/hidden",
        ):
            guard.assert_clean()

    def test_loopback_and_unix_socket_connections_remain_available(self):
        guard = ExternalNetworkGuard()
        tcp_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        directory = tempfile.TemporaryDirectory(prefix="tpx-unix-", dir="/tmp")
        unix_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        unix_left = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        unix_right = None
        try:
            tcp_listener.bind(("127.0.0.1", 0))
            tcp_listener.listen(1)
            unix_path = str(Path(directory.name) / "local.sock")
            unix_listener.bind(unix_path)
            unix_listener.listen(1)
            tcp_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with guard.active():
                    tcp_client.connect(tcp_listener.getsockname())
                    unix_left.connect(unix_path)
                    unix_right, _ = unix_listener.accept()
                    unix_left.sendall(b"ok")
                    self.assertEqual(unix_right.recv(2), b"ok")
            finally:
                tcp_client.close()
        finally:
            unix_left.close()
            if unix_right is not None:
                unix_right.close()
            unix_listener.close()
            directory.cleanup()
            tcp_listener.close()

        guard.assert_clean()


if __name__ == "__main__":
    unittest.main()
