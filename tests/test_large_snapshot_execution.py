"""Consume persistent pages, then execute the existing 10k-file stress scenario."""
import asyncio
import importlib.util
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def test_paged_10000_files_feed_real_executor_and_replay_without_moves(tmp_path, monkeypatch):
    for name in ("download", "rename"):
        monkeypatch.syspath_prepend(str(ROOT / "features" / name / "src"))
    from telepiplex_download.jobs import DownloadJobStore
    from telepiplex_download.service import DownloadFeature
    from telepiplex_rename.jobs import RenameJobStore
    from telepiplex_rename.snapshot_reader import read_snapshot
    from telepiplex_plugin_sdk.storage_snapshot import SnapshotStore, build_snapshot, encoded

    spec = importlib.util.spec_from_file_location("large_file_execution_fixture", ROOT / "features/rename/tests/test_regression_pressure.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    original_execute = fixture.execute_file_resolutions
    jobs = DownloadJobStore(tmp_path / "download.db")
    consumer_jobs = RenameJobStore(tmp_path / "rename.db")
    provider = DownloadFeature(config={}, host=None, client=object(), jobs=jobs)
    reference = None
    page_reads = 0
    max_response_bytes = 0
    executions = 0

    class Host:
        async def call_capability(self, capability, method, payload, **kwargs):
            nonlocal page_reads, max_response_bytes
            assert capability == "storage.provider"
            if method == "get_tree_snapshot_page":
                page_reads += 1
            response = await provider.storage_capability({"method": method, "payload": payload})
            max_response_bytes = max(max_response_bytes, len(encoded({
                "type": "response", "id": "f" * 32, "ok": True, "result": response,
            })) + 1)
            return response

    def execute_from_durable_pages(storage, resolutions, **kwargs):
        nonlocal reference, executions, provider
        if reference is None:
            tree = [{"file_id": "dir:" + path, "name": PurePosixPath(path).name,
                     "relative_path": str(PurePosixPath(path).relative_to("/Downloads")),
                     "path": path, "is_dir": True, "size": 0, "sha1": ""}
                    for path in sorted(storage.directories) if path.startswith("/Downloads/")]
            tree.extend({"file_id": item.source_id, "name": PurePosixPath(item.source_path).name,
                         "relative_path": str(PurePosixPath(item.source_path).relative_to("/Downloads")),
                         "path": item.source_path, "is_dir": False, "size": 300_000_000, "sha1": ""}
                        for item in resolutions)
            reference, pages = build_snapshot(tree, job_id="large-execution", root_path="/Downloads", root_id="source-root")
            SnapshotStore(str(jobs.path) + ".snapshots.sqlite3").put(reference, pages)
            assert reference["node_count"] == 10_500
            assert reference["file_count"] == 10_000
        else:
            # Both services are recreated; source nodes have already moved.
            provider = DownloadFeature(config={}, host=None, client=object(), jobs=DownloadJobStore(jobs.path))
        reads_before = page_reads
        copied = asyncio.run(read_snapshot(Host(), RenameJobStore(consumer_jobs.path), reference,
            job_id="large-execution", root_path="/Downloads", check_cancelled=lambda: None))
        if executions:
            assert page_reads == reads_before  # Independent consumer copy survives restart.
        else:
            assert page_reads == reference["page_count"]
        by_id = {item.source_id: item for item in resolutions}
        files = [node for node in copied if not node["is_dir"]]
        assert {node["file_id"] for node in files} == set(by_id)
        assert {node["path"] for node in files} == {item.source_path for item in resolutions}
        result = original_execute(storage, [by_id[node["file_id"]] for node in files], **kwargs)
        assert result.organized_files + result.canonical_no_ops + result.failed_files == 10_000
        executions += 1
        return result

    monkeypatch.setattr(fixture, "execute_file_resolutions", execute_from_durable_pages)
    fixture.test_file_execution_and_cleanup_pressure_10_000_files_500_directories()
    assert executions == 2
    assert max_response_bytes <= 1_048_576
