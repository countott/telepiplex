"""Explicit legacy settings must survive package default projection."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from app.runtime.central_config import CentralConfigStore


ROOT = Path(__file__).resolve().parents[1]


def test_download_aliases_reach_client_pacing_before_defaults(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "features/download/src"))
    from telepiplex_download.client import Open115Client

    defaults = yaml.safe_load((ROOT / "features/download/config.default.yaml").read_text())
    raw = {"request_interval": 0, "poll_interval": 10}
    store = CentralConfigStore(tmp_path / "config.yaml", initial={"features": {"download": raw}})
    effective = store.effective("download", defaults)
    now = [0]
    def sleep(delay):
        now[0] += delay
    monkeypatch.setattr("telepiplex_download.pacing.time.monotonic", lambda: now[0])
    monkeypatch.setattr("telepiplex_download.pacing.time.sleep", sleep)
    client = Open115Client(effective)
    assert client.request_interval == 0
    assert client._pacer.acquire("storage.read") == 0
    assert client._pacer.acquire("storage.read") == 0
    assert client._pacer.acquire("storage.mutation") == 0
    # Writes retain their safety floor even when legacy pacing is disabled.
    assert client._pacer.acquire("storage.mutation") == 1
    assert effective["poll_initial_interval"] == 10
    assert raw == {"request_interval": 0, "poll_interval": 10}


@pytest.mark.parametrize("plugin_id,raw,want", [
    ("download", {"request_interval": 0}, {"request_interval": 0, "endpoint_intervals": {
        "offline_poll": 0, "offline_mutation": 0, "storage_read": 0,
        "storage_mutation": 0, "token_refresh": 0}}),
    ("download", {"request_interval": 9, "endpoint_intervals": {}},
     {"request_interval": 9, "endpoint_intervals": {}}),
    ("download", {"poll_interval": 10}, {"poll_interval": 10, "poll_initial_interval": 10}),
    ("download", {"poll_interval": 10, "poll_initial_interval": 0},
     {"poll_interval": 10, "poll_initial_interval": 0}),
    ("rename", {"ai": {"base_url": "https://ai.example/v1"}},
     {"ai": {"api_url": "https://ai.example/v1"}}),
    ("rename", {"ai": {"base_url": "https://old.example", "api_url": ""}},
     {"ai": {"api_url": ""}}),
    ("caption", {}, {}),
])
def test_alias_normalization_copies_without_defaults(plugin_id, raw, want):
    from app.runtime.config_aliases import normalize_explicit_aliases

    before = deepcopy(raw)
    normalized = normalize_explicit_aliases(plugin_id, raw)
    assert normalized == want
    assert raw == before
    assert normalized is not raw
    assert normalize_explicit_aliases(plugin_id, normalized) == want


@pytest.mark.parametrize("api", [{"base_url": "https://ai.example/v1"},
                                 {"base_url": "https://old.example", "api_url": ""}])
def test_ai_alias_is_resolved_before_empty_package_default(tmp_path, api):
    defaults = yaml.safe_load((ROOT / "features/rename/config.default.yaml").read_text())
    store = CentralConfigStore(tmp_path / "config.yaml", initial={"features": {"rename": {"ai": api}}})
    assert store.effective("rename", defaults)["ai"]["api_url"] == api.get("api_url", "https://ai.example/v1")
