import pytest
from telepiplex_download.client import Open115Client, Open115Error


def make_client(children, page_size=137):
    client = Open115Client({'access_token': 'test', 'request_interval': 0})
    client.get_file_info = lambda path: {'file_id': 'root', 'file_category': '0'}
    calls = []
    def listing(params):
        calls.append(dict(params))
        rows = children.get(params['cid'], [])
        offset = params.get('offset', 0)
        return {'list': rows[offset:offset + min(page_size, params['limit'])]}
    client.get_file_list = listing
    return client, calls


def node(i, directory=False):
    return {'fn': f'{i}.mkv', 'fid': str(i), 'fc': '0' if directory else '1', 'fs': 1024}


@pytest.mark.parametrize('count', [999, 1000])
def test_tree_reads_all_pages_and_verifies_empty_tail(count):
    client, calls = make_client({'root': [node(i) for i in range(count)]})
    assert len(client.get_file_tree('/downloads')) == count
    assert calls[-1]['offset'] == count


@pytest.mark.parametrize('children', [
    {'root': [node(i) for i in range(1001)]},
    {'root': [node('a', True), node('b', True)], 'a': [node(i) for i in range(600)], 'b': [node(i+600) for i in range(400)]},
    {'root': [node('root', True)]},
    {'root': [{'fn': 'missing.mkv', 'fc': '1'}]},
    {'root': [{'fid': 'x', 'fc': '1'}]},
    {'root': ['invalid']},
    {'root': [{'fn': '../outside', 'fid': 'x', 'fc': '1'}]},
])
def test_incomplete_or_ambiguous_tree_is_an_error(children):
    client, _ = make_client(children)
    with pytest.raises(Open115Error):
        client.get_file_tree('/downloads')


@pytest.mark.parametrize('response', [None, {}, {'list': None}, {'state': False, 'list': []}, {'list': [], 'count': 3}])
def test_invalid_listing_response_is_not_an_empty_directory(response):
    client, _ = make_client({})
    client.get_file_list = lambda params: response
    with pytest.raises(Open115Error):
        client.get_file_tree('/downloads')


def test_repeated_page_is_rejected():
    client, _ = make_client({})
    client.get_file_list = lambda params: [node(1)]
    with pytest.raises(Open115Error):
        client.get_file_tree('/downloads')


def test_depth_overflow_cannot_return_truncated_success():
    client, _ = make_client({'root': [node('a', True)], 'a': [node('b', True)], 'b': [node('x')]})
    with pytest.raises(Open115Error):
        client.get_file_tree('/downloads', max_depth=1)


def test_single_file_requires_stable_identity():
    client, _ = make_client({})
    client.get_file_info = lambda path: {'file_category': '1'}
    with pytest.raises(Open115Error):
        client.get_file_tree('/downloads/a.mkv')


def test_root_directory_can_be_enumerated():
    client, _ = make_client({'root': [node('a')]})
    assert client.get_file_tree('/')[0]['path'] == '/a.mkv'


@pytest.mark.parametrize('patch', [{'fs': {'bad': 1}}, {'fs': -1}, {'fc': 'invalid'}, {'file_category': 'invalid'}])
def test_malformed_file_attributes_are_rejected(patch):
    client, _ = make_client({'root': [{**node('a'), **patch}]})
    with pytest.raises(Open115Error):
        client.get_file_tree('/downloads')


@pytest.mark.parametrize('attributes', [
    {'fs': {}}, {'fs': []}, {'fs': False}, {'fs': None}, {'fs': ''}, {},
    {'fs': {}, 'size': 200 * 1024 * 1024},
    {'size': False, 'size_byte': 200 * 1024 * 1024},
])
def test_file_size_is_validated_before_fallback_or_cleanup(attributes):
    from telepiplex_download.cleanup import plan_download_cleanup
    good = {**node('good'), 'fs': 200 * 1024 * 1024}
    suspect = {'fn': 'suspect.mkv', 'fid': 'suspect', 'fc': '1', **attributes}
    client, _ = make_client({'root': [good, suspect]})
    deleted = []
    with pytest.raises(Open115Error):
        tree = client.get_file_tree('/downloads')
        deleted.extend(plan_download_cleanup(tree, minimum_video_size_bytes=100 * 1024 * 1024).rejected_paths)
    assert deleted == []


def test_directory_can_omit_size_but_file_must_supply_it():
    client, _ = make_client({'root': [{'fn': 'folder', 'fid': 'folder', 'fc': '0'}], 'folder': [node('good')]})
    tree = client.get_file_tree('/downloads')
    assert tree[0]['size'] == 0
    assert tree[1]['size'] == 1024


def test_valid_zero_size_is_not_replaced_by_another_alias():
    client, _ = make_client({'root': [{**node('zero'), 'fs': 0, 'size': 999}]})
    assert client.get_file_tree('/downloads')[0]['size'] == 0
