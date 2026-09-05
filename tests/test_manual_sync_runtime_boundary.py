"""Freeze the deployed sync runtime's read-only cross-Feature boundary."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from telepiplex_plugin_sdk import FeatureError

ROOT = Path(__file__).resolve().parents[1]


def test_real_sync_runtime_rejects_retired_event_and_mutating_capabilities(tmp_path, monkeypatch):
    async def exercise():
        monkeypatch.syspath_prepend(str(ROOT / "features/sync/src"))
        from telepiplex_sync.runtime import main

        manifest = yaml.safe_load((ROOT / "features/sync/manifest.yaml").read_text())
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump({
            "plex": {"base_url": "", "token": ""},
            "mcp": {"enabled": False},
        }))
        runtime = main(SimpleNamespace(
            config_path=config_path, state_path=tmp_path / "state",
            manifest=manifest, token="local-fixture", host=SimpleNamespace(),
        ))
        feature = runtime.capabilities["library.sync"].__self__
        job = feature.jobs.create_or_get("legacy-job", {"final_path": "/Movies/Legacy"})
        before = feature.jobs.get(job["id"])
        try:
            assert manifest["subscribes"] == []
            assert runtime.events == {}
            with pytest.raises(FeatureError) as rejected:
                await runtime._dispatch("event.deliver", {
                    "event_type": "media.organized", "event_id": "legacy-event",
                    "payload": {"metadata_id": "same-title", "final_path": "/Movies/New"},
                })
            assert rejected.value.code == "not_found"
            for method in ("run_job", "retry_job", "enqueue_organized_event", "scan_library", "apply_operation"):
                with pytest.raises(ValueError, match="unsupported library.sync method"):
                    await runtime._dispatch("capability.call", {
                        "capability": "library.sync", "method": method,
                        "payload": {"job_id": job["id"]},
                    })
            status = await runtime._dispatch("capability.call", {
                "capability": "library.sync", "method": "get_job",
                "payload": {"job_id": job["id"]},
            })
            listing = await runtime._dispatch("capability.call", {
                "capability": "library.sync", "method": "list_jobs",
                "payload": {"limit": 10},
            })
            assert status["job"]["id"] == job["id"]
            assert [item["id"] for item in listing["jobs"]] == [job["id"]]
            assert feature.service is None
            assert feature.service_error == ""
            assert feature.jobs.get(job["id"]) == before
            assert runtime.active_tasks == 0
        finally:
            await runtime.close()

    asyncio.run(exercise())
