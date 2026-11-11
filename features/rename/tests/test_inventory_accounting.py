import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telepiplex_rename.service import RenameFeature


def test_metadata_failure_still_accounts_for_every_scanned_file():
    feature = RenameFeature(config={}, host=SimpleNamespace())
    owner, operation_id = (1, 1), "inventory-test"
    feature.inventory_sessions[owner] = {
        "operation_id": operation_id, "pending": [{"job_id": "job", "source_ids": ["one", "two"]}],
    }
    feature._raise_if_cancelled = lambda _: None
    feature._inventory_item_payload = lambda *args: {}
    feature._run_organization = AsyncMock()
    reports = []
    async def report(*args, **kwargs):
        reports.append(kwargs)
    feature._report_if_active = report
    asyncio.run(feature._run_inventory_batch(owner, operation_id))
    counts = reports[-1]["details"]["file_results"]
    assert counts["media_files_total"] == 2
    assert counts["kept_unresolved"] == 2
    assert counts["organized_files"] == 0
    assert reports[-1]["details"]["failed"] == 1
