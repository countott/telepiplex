import os

import pytest

from tests.network_guard import ExternalNetworkGuard


LIVE_NETWORK_ENV = "TELEPIPLEX_SEARCH_TEST_LIVE_NETWORK"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_network: test intentionally calls configured external providers",
    )


@pytest.fixture(autouse=True)
def isolate_default_tests_from_external_network(request):
    live_test = request.node.get_closest_marker("live_network") is not None
    live_opt_in = os.environ.get(LIVE_NETWORK_ENV, "").strip() == "1"
    if live_test and not live_opt_in:
        pytest.skip(
            f"set {LIVE_NETWORK_ENV}=1 in addition to the live test's "
            "provider configuration"
        )
    if live_test:
        yield
        return

    guard = ExternalNetworkGuard()
    with guard.active():
        yield
    guard.assert_clean()
