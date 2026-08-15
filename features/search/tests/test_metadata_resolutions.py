import tempfile
from pathlib import Path

from telepiplex_search.metadata_resolutions import MetadataResolutionStore


def test_resolution_store_survives_restart_and_replays_cached_result():
    clock = [1_000.0]
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "metadata-resolutions.db"
        first = MetadataResolutionStore(
            path,
            ttl_seconds=60,
            now=lambda: clock[0],
        )
        first.save("resolution-1", {
            "query": "同名剧集",
            "probe": {"content_shape": "season_pack"},
            "plan": {"candidates": [{"candidate_key": "tvdb:1"}]},
        })
        first.cache_result(
            "resolution-1",
            "tvdb:1",
            {"status": "resolved", "media_metadata": {"metadata_id": "m1"}},
        )

        restarted = MetadataResolutionStore(
            path,
            ttl_seconds=60,
            now=lambda: clock[0],
        )
        state, record = restarted.load("resolution-1")

        assert state == "found"
        assert record["selected_candidate_ref"] == "tvdb:1"
        assert record["result"]["status"] == "resolved"


def test_resolution_store_distinguishes_expired_from_unknown_and_prunes_capacity():
    clock = [2_000.0]
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MetadataResolutionStore(
            Path(tmpdir) / "metadata-resolutions.db",
            ttl_seconds=10,
            max_entries=3,
            now=lambda: clock[0],
        )
        for index in range(5):
            clock[0] += 1
            store.save(f"resolution-{index}", {"index": index})

        assert store.load("resolution-0")[0] == "missing"
        assert store.load("resolution-4")[0] == "found"
        clock[0] += 11
        assert store.load("resolution-4")[0] == "expired"
        assert store.load("never-created")[0] == "missing"
