import asyncio
from unittest.mock import Mock

from telepiplex_sync.feature import SyncFeature
from telepiplex_sync.sync_service import LibrarySyncService
from tests.test_feature_runtime import FakeHost, FakeRuntime, FakeService
from tests.test_sync_service import make_media_metadata_v2


def test_scan_availability_requires_url_and_token_and_does_not_build_service(tmp_path):
    async def run():
        for config, available in (({}, False), ({"base_url": "http://plex"}, False),
                                  ({"token": "token"}, False),
                                  ({"base_url": "http://plex", "token": "token"}, True)):
            factory = Mock(side_effect=AssertionError("status must not access Plex"))
            feature = SyncFeature(config={"plex": config}, host=FakeHost(), state_path=tmp_path, service_factory=factory)
            result = await feature.management_capability({"method": "scan_status"})
            assert result == {"available": available}
            factory.assert_not_called()
    asyncio.run(run())


def test_post_rename_scans_only_mapped_library_after_explicit_command(tmp_path):
    async def run():
        feature = SyncFeature(config={}, host=FakeHost(), state_path=tmp_path)
        service = FakeService(feature.jobs)
        service.category_folders = [{"kind": "live_action_series", "path": "/TV", "plex_library_id": "13"}]
        service._media_metadata = lambda job: LibrarySyncService._media_metadata(service, job)
        service._route_library = lambda job: LibrarySyncService._route_library(service, job)
        feature.service = service
        feature.runtime = FakeRuntime()
        assert service.scan_requests == []
        result = await feature.command({"command": "scan", "chat_id": 10, "user_id": 1,
            "post_rename": {"media_metadata": make_media_metadata_v2(media_type="series", category_kind="live_action_series"),
                            "final_path": "/TV/Show"}})
        assert result["operation"]["state"] == "running"
        assert service.scan_requests == []
        await next(iter(feature.runtime.tasks.values()))
        assert service.scan_requests == [["13"]]
        assert service.runs == 0
    asyncio.run(run())


def test_missing_route_opens_existing_library_choice_without_scanning(tmp_path):
    async def run():
        feature = SyncFeature(config={}, host=FakeHost(), state_path=tmp_path)
        feature.service = FakeService(feature.jobs)
        feature.service._route_library = Mock(side_effect=LookupError("unmapped"))
        result = await feature.command({"command": "scan", "chat_id": 10, "user_id": 1,
                                        "post_rename": {"final_path": "/TV/Show"}})
        assert "请选择要扫描的 Plex 媒体库" in result["actions"][0]["text"]
        assert feature.service.scan_requests == []
    asyncio.run(run())
