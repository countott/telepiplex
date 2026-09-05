from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import ipaddress
import socket
import traceback
from typing import Iterator
from urllib.parse import urlsplit

import requests
from unittest.mock import patch


class NetworkAccessBlocked(requests.ConnectionError):
    """Raised immediately when a default test attempts external I/O."""


@dataclass(frozen=True)
class NetworkViolation:
    transport: str
    destination: str
    callsite: str


def _production_callsite() -> str:
    frames = [
        frame
        for frame in traceback.extract_stack()
        if "/features/search/src/" in frame.filename
    ]
    if not frames:
        return "unknown"
    return " <- ".join(
        f"{frame.filename.rsplit('/telepiplex/', 1)[-1]}:{frame.lineno}"
        for frame in reversed(frames[-5:])
    )


def _is_loopback_host(host: object) -> bool:
    value = str(host or "").strip().strip("[]")
    if value.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


class ExternalNetworkGuard:
    """Block external requests while retaining violations for final checks."""

    def __init__(self) -> None:
        self.violations: list[NetworkViolation] = []

    @contextmanager
    def active(self) -> Iterator[None]:
        original_request = requests.sessions.Session.request
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex

        def guarded_request(
            session: requests.Session,
            method: str,
            url: str,
            *args: object,
            **kwargs: object,
        ) -> requests.Response:
            host = urlsplit(str(url)).hostname
            if _is_loopback_host(host):
                return original_request(
                    session,
                    method,
                    url,
                    *args,
                    **kwargs,
                )
            destination = str(url)
            self.violations.append(NetworkViolation(
                "http",
                destination,
                _production_callsite(),
            ))
            raise NetworkAccessBlocked(
                f"default test attempted external HTTP request: {destination}"
            )

        def guarded_connect(sock: socket.socket, address: object) -> None:
            if self._socket_destination_is_local(sock, address):
                return original_connect(sock, address)
            destination = repr(address)
            self.violations.append(NetworkViolation(
                "socket",
                destination,
                _production_callsite(),
            ))
            raise NetworkAccessBlocked(
                f"default test attempted external socket connection: {destination}"
            )

        def guarded_connect_ex(sock: socket.socket, address: object) -> int:
            if self._socket_destination_is_local(sock, address):
                return original_connect_ex(sock, address)
            destination = repr(address)
            self.violations.append(NetworkViolation(
                "socket",
                destination,
                _production_callsite(),
            ))
            raise NetworkAccessBlocked(
                f"default test attempted external socket connection: {destination}"
            )

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    requests.sessions.Session,
                    "request",
                    guarded_request,
                )
            )
            stack.enter_context(
                patch.object(socket.socket, "connect", guarded_connect)
            )
            stack.enter_context(
                patch.object(socket.socket, "connect_ex", guarded_connect_ex)
            )
            yield

    @staticmethod
    def _socket_destination_is_local(
        sock: socket.socket,
        address: object,
    ) -> bool:
        if sock.family == socket.AF_UNIX:
            return True
        if not isinstance(address, tuple) or not address:
            return False
        return _is_loopback_host(address[0])

    def assert_clean(self) -> None:
        if not self.violations:
            return
        details = "\n".join(
            f"- {violation.transport}: {violation.destination} "
            f"({violation.callsite})"
            for violation in self.violations
        )
        raise AssertionError(
            "default test attempted external network access; mock the provider "
            "boundary or explicitly opt in to a live-network test:\n"
            f"{details}"
        )
