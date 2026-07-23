from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .host_client import HostClient


class FeatureError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class RuntimeContext:
    manifest: dict
    token: str
    socket_path: Path
    host_socket_path: Path
    config_path: Path
    state_path: Path
    host: "HostClient"


@dataclass(frozen=True)
class ResponseAction:
    kind: str
    text: str = ""
    data: dict = field(default_factory=dict)

    def to_mapping(self) -> dict:
        return {"kind": self.kind, "text": self.text, "data": dict(self.data)}
