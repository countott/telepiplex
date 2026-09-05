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
    file_tree: list[dict] | None = None
    release: dict | None = None
    download_root: str | None = None
    provider: str = "download"
    snapshot_id: str = ""
    snapshot_complete: bool | None = True
    file_tree_transport: str = ""
    snapshot_verified: bool = False
    storage: Any = None


@dataclass
class PostDownloadResult:
    handled: bool
    final_path: str | None = None
    message: str | None = None
    should_stop: bool = False
    metadata: dict | None = None
    file_results: dict | None = None
