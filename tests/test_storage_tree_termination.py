"""Known listing totals finish a tree without an out-of-range tail request."""

from copy import deepcopy

import pytest

from telepiplex_plugin_sdk.storage_tree import (
    TreeIntegrityError,
    collect_complete_tree,
    collect_snapshot_tree,
)


def file_node(file_id):
    return {'fid': file_id, 'fn': f'{file_id}.mkv', 'fc': '1', 'fs': 1}


class StoragePages:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get_file_info(self, path):
        return {'file_id': 'root', 'file_category': '0'}

    def get_file_list(self, params):
        key = params['cid'], params['offset']
        self.calls.append(key)
        assert key in self.pages, f'scanner requested an unnecessary page: {key}'
        return deepcopy(self.pages[key])


@pytest.fixture(params=[collect_complete_tree, collect_snapshot_tree])
def collect_tree(request):
    return request.param


def test_known_count_finishes_without_an_empty_tail(collect_tree):
    storage = StoragePages({
        ('root', 0): {'state': True, 'code': 0, 'offset': 0,
                      'count': 1, 'data': [file_node('a')]},
    })

    tree = collect_tree(storage, '/root')

    assert [(row['file_id'], row['path']) for row in tree] == [('a', '/root/a.mkv')]
    assert storage.calls == [('root', 0)]


def test_nested_count_finishes_after_all_pages_when_final_page_omits_count(collect_tree):
    storage = StoragePages({
        ('root', 0): {'state': True, 'data': {
            'offset': 0, 'count': '2', 'list': [file_node('a')],
        }},
        ('root', 1): {'state': True, 'offset': 1, 'data': [file_node('b')]},
    })

    tree = collect_tree(storage, '/root')

    assert [row['file_id'] for row in tree] == ['a', 'b']
    assert storage.calls == [('root', 0), ('root', 1)]


def test_parent_count_does_not_skip_child_directory_contents(collect_tree):
    storage = StoragePages({
        ('root', 0): {'count': 1, 'data': [
            {'fid': 'folder', 'fn': 'folder', 'fc': '0'},
        ]},
        ('folder', 0): {'count': 1, 'data': [file_node('child')]},
    })

    tree = collect_tree(storage, '/root')

    assert [row['relative_path'] for row in tree] == ['folder', 'folder/child.mkv']
    assert storage.calls == [('root', 0), ('folder', 0)]


def test_unknown_count_short_pages_still_require_an_empty_tail(collect_tree):
    storage = StoragePages({
        ('root', 0): {'data': [file_node('a')]},
        ('root', 1): {'data': [file_node('b')]},
        ('root', 2): {'data': []},
    })

    tree = collect_tree(storage, '/root')

    assert [row['file_id'] for row in tree] == ['a', 'b']
    assert storage.calls == [('root', 0), ('root', 1), ('root', 2)]


@pytest.mark.parametrize('pages, message', [
    ({('root', 0): {'count': 2, 'data': [file_node('a')]},
      ('root', 1): {'count': 3, 'data': [file_node('b')]}}, 'count changed'),
    ({('root', 0): {'count': 2, 'data': [file_node('a')]},
      ('root', 1): {'count': 2, 'data': []}}, 'ended before'),
    ({('root', 0): {'count': 0, 'data': [file_node('a')]}}, 'exceeds its declared count'),
    ({('root', 0): {'count': 2, 'data': [file_node('a')]},
      ('root', 1): {'count': 2, 'data': [file_node('a')]}}, 'repeats an object'),
])
def test_known_count_completion_preserves_integrity_checks(collect_tree, pages, message):
    with pytest.raises(TreeIntegrityError, match=message):
        collect_tree(StoragePages(pages), '/root')
