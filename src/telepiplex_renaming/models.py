from dataclasses import dataclass
from typing import Any


@dataclass
class DownloadCompletedEvent:
    link: str
    selected_path: str
    user_id: int
    final_path: str
    resource_name: str
    naming_metadata: dict | None = None
    metadata: dict | None = None
    provider: str = "open115"
    storage: Any = None


@dataclass
class PostDownloadResult:
    handled: bool
    final_path: str | None = None
    message: str | None = None
    should_stop: bool = False
    metadata: dict | None = None
