from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
import threading
import time
from unittest.mock import patch

import pytest
import requests

from telepiplex_download.client import Open115Client, Open115Error
from telepiplex_download.pacing import EndpointPacer


class FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = float(value)
        self.sleeps: list[float] = []
        self.before_sleep = None

    def monotonic(self) -> float:
        return self.value

    def sleep(self, delay: float) -> None:
        delay = float(delay)
        self.sleeps.append(delay)
        if self.before_sleep is not None:
            callback, self.before_sleep = self.before_sleep, None
            callback()
        self.value += delay


def test_endpoint_pacer_spaces_each_class_independently_and_enforces_write_floor():
    clock = FakeClock()
    pacer = EndpointPacer(
        {
            "offline_poll": 0.4,
            "offline_mutation": 0,
            "storage_read": 0.25,
            "storage_mutation": 0.1,
            "token_refresh": 0.2,
        },
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )

    assert pacer.acquire("storage.read") == pytest.approx(0)
    assert pacer.acquire("offline.poll") == pytest.approx(0)
    assert pacer.acquire("storage.read") == pytest.approx(0.25)
    assert pacer.acquire("offline.poll") == pytest.approx(0.15)

    for endpoint_class in (
        "offline.mutation",
        "storage.mutation",
        "token.refresh",
    ):
        assert pacer.acquire(endpoint_class) == pytest.approx(0)
        assert pacer.acquire(endpoint_class) == pytest.approx(1.0)


def test_endpoint_pacer_rechecks_after_unlocked_sleep_when_throttle_moves_target():
    clock = FakeClock()
    pacer = EndpointPacer(
        {"storage_read": 0.25},
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    pacer.acquire("storage.read")
    clock.before_sleep = lambda: pacer.observe_throttle("storage.read", "2")

    waited = pacer.acquire("storage.read")

    assert waited == pytest.approx(2.0)
    assert clock.sleeps == pytest.approx([0.25, 1.75])


def test_endpoint_pacer_retry_after_is_bounded_parsed_and_class_local():
    clock = FakeClock()
    wall_now = 1_700_000_000.0
    pacer = EndpointPacer(
        {
            "offline_poll": 0,
            "storage_read": 0,
            "storage_mutation": 1,
        },
        clock=clock.monotonic,
        sleeper=clock.sleep,
        wall_clock=lambda: wall_now,
    )

    pacer.observe_throttle("offline.poll", "9999")
    assert pacer.acquire("storage.read") == pytest.approx(0)
    assert pacer.acquire("storage.mutation") == pytest.approx(0)
    assert pacer.acquire("offline.poll") == pytest.approx(300)

    retry_date = format_datetime(
        datetime.fromtimestamp(wall_now + 12, tz=timezone.utc),
        usegmt=True,
    )
    pacer.observe_throttle("storage.read", retry_date)
    assert pacer.acquire("storage.read") == pytest.approx(12)

    pacer.observe_throttle("storage.read", "not-a-delay")
    assert pacer.acquire("storage.read") == pytest.approx(0)


def test_endpoint_pacer_rejects_unknown_classes_instead_of_bypassing_pacing():
    pacer = EndpointPacer({}, clock=lambda: 0.0, sleeper=lambda _delay: None)

    with pytest.raises(ValueError, match="endpoint class"):
        pacer.acquire("future.endpoint")
    with pytest.raises(ValueError, match="endpoint class"):
        pacer.observe_throttle("future.endpoint", "1")


class FakeResponse:
    def __init__(self, payload, *, status_code=200, headers=None):
        self.payload = payload
        self.status_code = int(status_code)
        self.headers = dict(headers or {})

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self.payload


class RecordingPacer:
    def __init__(self):
        self.acquired = []
        self.throttles = []

    def acquire(self, endpoint_class):
        self.acquired.append(endpoint_class)
        return 0.0

    def observe_throttle(self, endpoint_class, retry_after):
        self.throttles.append((endpoint_class, retry_after))
        return 0.0


class QueueSession:
    def __init__(self, *, requests_=(), posts=(), gets=()):
        self.request_responses = list(requests_)
        self.post_responses = list(posts)
        self.get_responses = list(gets)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append(("request", method, url))
        if self.request_responses:
            return self.request_responses.pop(0)
        return FakeResponse({"state": True, "code": 0, "data": {}})

    def post(self, url, **kwargs):
        self.calls.append(("post", "POST", url))
        if self.post_responses:
            return self.post_responses.pop(0)
        return FakeResponse({"state": True, "code": 0, "data": {}})

    def get(self, url, **kwargs):
        self.calls.append(("get", "GET", url))
        if self.get_responses:
            return self.get_responses.pop(0)
        return FakeResponse({"state": True, "code": 0, "data": {}})


def test_open115_request_classifies_every_current_provider_route():
    pacer = RecordingPacer()
    client = Open115Client(
        {"access_token": "access", "refresh_token": "refresh"},
        session=QueueSession(),
        pacer=pacer,
    )
    routes = (
        ("GET", "/open/offline/get_task_list", "offline.poll"),
        ("POST", "/open/offline/add_task_urls", "offline.mutation"),
        ("POST", "/open/offline/del_task", "offline.mutation"),
        ("GET", "/open/folder/get_info", "storage.read"),
        ("GET", "/open/ufile/files", "storage.read"),
        ("POST", "/open/folder/add", "storage.mutation"),
        ("POST", "/open/ufile/update", "storage.mutation"),
        ("POST", "/open/ufile/copy", "storage.mutation"),
        ("POST", "/open/ufile/delete", "storage.mutation"),
        ("POST", "/open/ufile/move", "storage.mutation"),
    )

    for method, path, _endpoint_class in routes:
        client._request(method, path)

    assert pacer.acquired == [item[2] for item in routes]


def test_open115_unknown_provider_route_fails_instead_of_bypassing_pacing():
    client = Open115Client(
        {"access_token": "access"},
        session=QueueSession(),
        pacer=RecordingPacer(),
    )

    with pytest.raises(Open115Error) as raised:
        client._request("GET", "/open/future/endpoint")

    assert raised.value.code == "unclassified_endpoint"
    assert raised.value.operation == "/open/future/endpoint"


def test_open115_429_records_cooldown_for_current_class_without_replaying_call():
    session = QueueSession(requests_=[
        FakeResponse(
            {"state": False, "code": 429},
            status_code=429,
            headers={"Retry-After": "17"},
        ),
    ])
    pacer = RecordingPacer()
    client = Open115Client(
        {"access_token": "access"},
        session=session,
        pacer=pacer,
    )

    with pytest.raises(Open115Error, match="HTTPError"):
        client._request("GET", "/open/offline/get_task_list")

    assert len(session.calls) == 1
    assert pacer.acquired == ["offline.poll"]
    assert pacer.throttles == [("offline.poll", "17")]


def test_token_expiry_releases_mutation_guard_then_paces_refresh_and_one_retry():
    session = QueueSession(
        requests_=[
            FakeResponse({"state": False, "code": 40140125}),
            FakeResponse({"state": True, "code": 0, "data": {}}),
        ],
        posts=[FakeResponse({
            "state": True,
            "code": 0,
            "data": {
                "access_token": "renewed-access",
                "refresh_token": "renewed-refresh",
            },
        })],
    )
    pacer = RecordingPacer()
    client = Open115Client(
        {"access_token": "access", "refresh_token": "refresh"},
        session=session,
        pacer=pacer,
    )
    finished = threading.Event()
    outcome = {}

    def run():
        try:
            outcome["result"] = client._request(
                "POST",
                "/open/ufile/delete",
                data={"file_ids": "file-1"},
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            outcome["error"] = exc
        finally:
            finished.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()

    assert finished.wait(1), "token refresh recursively deadlocked the mutation guard"
    assert "error" not in outcome
    assert outcome["result"]["state"] is True
    assert pacer.acquired == [
        "storage.mutation",
        "token.refresh",
        "storage.mutation",
    ]
    assert [call[0] for call in session.calls] == ["request", "post", "request"]
    assert client.access_token == "renewed-access"


def test_token_expiry_releases_cache_writer_barrier_during_refresh():
    class RefreshBlockingSession(QueueSession):
        def __init__(self):
            super().__init__(requests_=[
                FakeResponse({"state": False, "code": 40140125}),
                FakeResponse({"state": True, "code": 0, "data": {}}),
            ])
            self.refresh_started = threading.Event()
            self.release_refresh = threading.Event()

        def post(self, url, **kwargs):
            self.calls.append(("post", "POST", url))
            self.refresh_started.set()
            assert self.release_refresh.wait(1)
            return FakeResponse({
                "state": True,
                "code": 0,
                "data": {
                    "access_token": "renewed-access",
                    "refresh_token": "renewed-refresh",
                },
            })

    session = RefreshBlockingSession()
    client = Open115Client(
        {"access_token": "access", "refresh_token": "refresh"},
        session=session,
        pacer=RecordingPacer(),
    )
    cached = {"file_id": "still-current-before-retry"}
    client._file_cache["/episode.mkv"] = cached
    outcome = {}
    worker = threading.Thread(
        target=lambda: outcome.setdefault(
            "value",
            client._request("POST", "/open/ufile/delete"),
        )
    )
    worker.start()
    assert session.refresh_started.wait(1)

    assert client.get_file_info("/episode.mkv") is cached
    session.release_refresh.set()
    worker.join(1)

    assert not worker.is_alive()
    assert outcome["value"]["state"] is True
    assert client._pacer.acquired == [
        "storage.mutation",
        "token.refresh",
        "storage.mutation",
    ]


def test_device_token_issue_and_exchange_are_paced_but_qr_status_poll_is_not():
    session = QueueSession(
        posts=[
            FakeResponse({
                "state": True,
                "code": 0,
                "data": {
                    "uid": "device-1",
                    "time": 123,
                    "sign": "signed",
                    "qrcode": "https://115.com/scan/device-1",
                },
            }),
            FakeResponse({
                "state": True,
                "code": 0,
                "data": {
                    "access_token": "device-access",
                    "refresh_token": "device-refresh",
                },
            }),
        ],
        gets=[FakeResponse({
            "state": True,
            "code": 0,
            "data": {"status": 2},
        })],
    )
    pacer = RecordingPacer()
    client = Open115Client({}, session=session, pacer=pacer)

    authorization = client.create_device_authorization("app-id")
    tokens = client.complete_device_authorization(authorization)

    assert pacer.acquired == ["token.refresh", "token.refresh"]
    assert [call[0] for call in session.calls] == ["post", "get", "post"]
    assert tokens == {
        "access_token": "device-access",
        "refresh_token": "device-refresh",
    }


def test_provider_mutation_guard_serializes_offline_storage_and_token_requests():
    class ActiveSession(QueueSession):
        def __init__(self):
            super().__init__()
            self.state_lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def _respond(self, payload):
            with self.state_lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)
            with self.state_lock:
                self.active -= 1
            return FakeResponse(payload)

        def request(self, method, url, **kwargs):
            self.calls.append(("request", method, url))
            return self._respond({"state": True, "code": 0, "data": {}})

        def post(self, url, **kwargs):
            self.calls.append(("post", "POST", url))
            return self._respond({
                "state": True,
                "code": 0,
                "data": {
                    "access_token": "renewed-access",
                    "refresh_token": "renewed-refresh",
                },
            })

    session = ActiveSession()
    client = Open115Client(
        {"access_token": "access", "refresh_token": "refresh"},
        session=session,
        pacer=RecordingPacer(),
    )
    barrier = threading.Barrier(4)
    errors = []

    def invoke(call):
        barrier.wait()
        try:
            call()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    calls = (
        lambda: client._request("POST", "/open/offline/del_task"),
        lambda: client._request("POST", "/open/ufile/update"),
        client.refresh_access_token,
    )
    workers = [threading.Thread(target=invoke, args=(call,)) for call in calls]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(2)

    assert not errors
    assert all(not worker.is_alive() for worker in workers)
    assert session.max_active == 1


def test_storage_read_batch_uses_one_client_wide_bound_of_four_workers():
    class ReadSession(QueueSession):
        def __init__(self):
            super().__init__()
            self.state_lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def request(self, method, url, **kwargs):
            assert method == "GET"
            with self.state_lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.03)
            with self.state_lock:
                self.active -= 1
            path = kwargs["params"]["path"]
            return FakeResponse({
                "state": True,
                "code": 0,
                "data": {"file_id": f"id:{path}"},
            })

    session = ReadSession()
    client = Open115Client(
        {
            "access_token": "access",
            "storage_read_workers": 99,
        },
        session=session,
        pacer=RecordingPacer(),
    )

    result = client.get_file_info_batch([f"/file-{index}" for index in range(8)])

    assert session.max_active == 4
    assert list(result) == [f"/file-{index}" for index in range(8)]
    assert all(value["file_id"].startswith("id:/file-") for value in result.values())


def test_two_simultaneous_batches_still_share_the_same_four_read_slots():
    class ReadSession(QueueSession):
        def __init__(self):
            super().__init__()
            self.state_lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def request(self, method, _url, **kwargs):
            assert method == "GET"
            with self.state_lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.04)
            with self.state_lock:
                self.active -= 1
            path = kwargs["params"]["path"]
            return FakeResponse({
                "state": True,
                "code": 0,
                "data": {"file_id": f"id:{path}"},
            })

    session = ReadSession()
    client = Open115Client(
        {"access_token": "access", "storage_read_workers": 4},
        session=session,
        pacer=RecordingPacer(),
    )
    start = threading.Barrier(3)
    results = []

    def batch(prefix):
        start.wait()
        results.append(client.get_file_info_batch([
            f"/{prefix}-{index}" for index in range(4)
        ]))

    workers = [
        threading.Thread(target=batch, args=(prefix,))
        for prefix in ("a", "b")
    ]
    for worker in workers:
        worker.start()
    start.wait()
    for worker in workers:
        worker.join(2)

    assert all(not worker.is_alive() for worker in workers)
    assert len(results) == 2
    assert session.max_active == 4


def test_legacy_request_interval_fills_all_classes_but_cannot_lower_write_floor():
    clock = FakeClock()
    session = QueueSession()
    with patch(
        "telepiplex_download.pacing.time.monotonic",
        side_effect=clock.monotonic,
    ), patch(
        "telepiplex_download.pacing.time.sleep",
        side_effect=clock.sleep,
    ):
        client = Open115Client(
            {
                "access_token": "access",
                "request_interval": 0.4,
            },
            session=session,
        )
        client._request("GET", "/open/folder/get_info")
        client._request("GET", "/open/folder/get_info")
        client._request("POST", "/open/ufile/update")
        client._request("POST", "/open/ufile/update")

    assert clock.sleeps == pytest.approx([0.4, 1.0])


def test_endpoint_intervals_override_legacy_request_interval_when_present():
    clock = FakeClock()
    with patch(
        "telepiplex_download.pacing.time.monotonic",
        side_effect=clock.monotonic,
    ), patch(
        "telepiplex_download.pacing.time.sleep",
        side_effect=clock.sleep,
    ):
        client = Open115Client(
            {
                "access_token": "access",
                "request_interval": 9,
                "endpoint_intervals": {
                    "offline_poll": 0,
                    "offline_mutation": 1,
                    "storage_read": 0.25,
                    "storage_mutation": 1,
                    "token_refresh": 1,
                },
            },
            session=QueueSession(),
        )
        client._request("GET", "/open/folder/get_info")
        client._request("GET", "/open/folder/get_info")

    assert clock.sleeps == pytest.approx([0.25])
