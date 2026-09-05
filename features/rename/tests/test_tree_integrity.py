import pytest
from telepiplex_plugin_sdk import FeatureError
from telepiplex_rename.models import DownloadCompletedEvent
from telepiplex_rename.processor import _event_file_tree


class Storage:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
    def get_file_info(self, path):
        return {'file_id': 'root', 'file_category': '0'}
    def get_file_list(self, params):
        self.calls.append(params)
        offset = params.get('offset', 0)
        return self.rows[offset:offset + 137]


def event(storage, **kwargs):
    value = DownloadCompletedEvent('', '/Downloads', 1, '/Downloads/Release', 'Release', storage=storage, **kwargs)
    return value


def test_legacy_tree_is_rescanned_instead_of_using_partial_inline_data():
    storage = Storage([{'fid': str(i), 'fn': f'{i}.mkv', 'fc': '1', 'fs': 1024} for i in range(999)])
    value = event(storage, file_tree=[{'name': 'partial.mkv'}])
    value.snapshot_complete = None
    result = _event_file_tree(value)
    assert len(result) == 999
    assert storage.calls[-1]['offset'] == 999


def test_unknown_transport_is_rejected_without_storage_reads():
    storage = Storage([])
    value = event(storage)
    value.file_tree_transport = 'future_v9'
    with pytest.raises(FeatureError, match='transport'):
        _event_file_tree(value)
    assert not storage.calls


def test_incomplete_snapshot_is_rejected_at_tree_entry():
    with pytest.raises(FeatureError):
        _event_file_tree(event(Storage([]), snapshot_complete=False, file_tree=[{'name': 'partial.mkv'}]))


@pytest.mark.parametrize('rows', [
    [{'fid': str(i), 'fn': f'{i}.mkv', 'fc': '1', 'fs': 1024} for i in range(1001)],
    [{'fn': 'missing.mkv', 'fc': '1'}],
    [{'fid': 'root', 'fn': 'cycle', 'fc': '0'}],
])
def test_legacy_scan_rejects_incomplete_tree(rows):
    value = event(Storage(rows))
    value.snapshot_complete = None
    with pytest.raises(FeatureError):
        _event_file_tree(value)


@pytest.mark.parametrize('tree', [
    [{'file_id': '1', 'relative_path': 'a.mkv', 'is_dir': False, 'path': '/outside/a.mkv'}],
    [{'file_id': '1', 'relative_path': 'missing/a.mkv', 'is_dir': False}],
    [{'file_id': str(i), 'relative_path': '/'.join(['dir'] * (i + 1)), 'is_dir': True} for i in range(9)],
])
def test_marked_inline_tree_rejects_invalid_topology_before_mutation(tree):
    value = event(Storage([]), file_tree=tree)
    value.file_tree_transport = 'inline_v1'
    with pytest.raises(FeatureError):
        _event_file_tree(value)


@pytest.mark.parametrize('size_attributes', [{'fs': {}}, {'fs': []}, {'fs': False}, {'fs': None}, {'fs': ''}, {}])
def test_legacy_scanner_rejects_malformed_or_missing_file_size(size_attributes):
    value = event(Storage([{'fid': '1', 'fn': 'suspect.mkv', 'fc': '1', **size_attributes}]))
    value.snapshot_complete = None
    with pytest.raises(FeatureError, match='size'):
        _event_file_tree(value)
