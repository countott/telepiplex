"""Download's immutable, persistent snapshot provider."""
from telepiplex_plugin_sdk.storage_snapshot import SnapshotStore, SnapshotError
from telepiplex_plugin_sdk.storage_tree import collect_snapshot_tree, TreeIntegrityError
from .client import Open115Error


def store_for(jobs):
    if jobs is None or not getattr(jobs,'path',None):
        raise SnapshotError('durable download job store is required for snapshots')
    return SnapshotStore(str(jobs.path)+'.snapshots.sqlite3')


def scan_snapshot(client,path):
    def root_id(info):
        if not isinstance(info,dict):return ''
        return str(info.get('fid') or info.get('file_id') or info.get('cid') or info.get('id') or '').strip()
    def fresh_info():
        invalidate = getattr(client, '_remove_cached_file', None)
        if callable(invalidate): invalidate(path)
        return client.get_file_info(path)
    first=fresh_info()
    identity=root_id(first)
    if not identity:raise SnapshotError('snapshot root has no stable identity')
    # The scanner itself verifies the root and all descendants. The extra root
    # read brackets that scan so its bound identity cannot be silently replaced.
    try:tree=collect_snapshot_tree(client,path)
    except TreeIntegrityError as exc:
        raise Open115Error(str(exc),code='file_tree_incomplete',operation='get_file_tree') from exc
    if root_id(fresh_info())!=identity:raise SnapshotError('snapshot root changed during scan')
    return tree,identity
