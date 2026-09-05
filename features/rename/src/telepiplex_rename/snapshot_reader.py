"""Fetch, validate and durably copy a complete snapshot before file mutations."""
from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.storage_snapshot import (
    SnapshotError, SnapshotStore, validate_reference, validate_page, verify_snapshot,
)


async def read_snapshot(host,jobs,ref,*,job_id,root_path,check_cancelled,timeout=120):
    try:
        validate_reference(ref,job_id=job_id,root_path=root_path)
        if jobs is None or not getattr(jobs,'path',None):raise SnapshotError('durable rename job store is required for snapshots')
        store=SnapshotStore(str(jobs.path)+'.snapshots.sqlite3')
        check_cancelled()
        if store.contains(ref):
            pages=store.get(ref)
        else:
            pages=[];cursor=None;start=0
            for index in range(ref['page_count']):
                check_cancelled()
                response=await host.call_capability('storage.provider','get_tree_snapshot_page',
                    {'args':[ref,cursor],'kwargs':{}},deadline=timeout)
                check_cancelled()
                page=response.get('value') if isinstance(response,dict) else None
                rows=validate_page(ref,page,index,start)
                pages.append(page);start+=len(rows);cursor=page['next_cursor']
            verify_snapshot(ref,pages)
            check_cancelled()
            store.put(ref,pages)
        # Re-read the committed independent copy, including count and digest.
        rows=verify_snapshot(ref,store.get(ref))
        check_cancelled()
        # A receipt never deletes provider data. A lost receipt cannot invalidate
        # the locally durable copy, and replay therefore does not need provider.
        try:
            await host.call_capability('storage.provider','acknowledge_tree_snapshot',
                {'args':[ref],'kwargs':{}},deadline=timeout)
        except Exception:
            check_cancelled()
        check_cancelled()
        return rows
    except SnapshotError as exc:
        raise FeatureError('download_tree_incomplete',str(exc)) from exc
