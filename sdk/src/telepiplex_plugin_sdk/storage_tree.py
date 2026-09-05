"""Fail-closed, bounded storage enumeration for the inline tree contract."""
from pathlib import PurePosixPath


class TreeIntegrityError(RuntimeError):
    pass


def collect_complete_tree(storage, root_path, *, max_depth=8, limit=1000):
    return _collect_complete_tree(storage, root_path, max_depth=max_depth, limit=limit, ceiling=1000)


def collect_snapshot_tree(storage, root_path, *, max_depth=8, limit=20000):
    """Explicit large-tree scanner; ordinary get_file_tree retains its 1000 cap."""
    return _collect_complete_tree(storage, root_path, max_depth=max_depth, limit=limit, ceiling=20000)


def _collect_complete_tree(storage, root_path, *, max_depth, limit, ceiling):
    root_path = '/' + str(root_path or '').strip('/')
    if not 0 <= int(max_depth) <= 8 or not 1 <= int(limit) <= ceiling:
        raise TreeIntegrityError(f'file tree bounds must be depth 0..8 and nodes 1..{ceiling}')

    def identity(item, name=None, *, root=False):
        if not isinstance(item, dict):
            raise TreeIntegrityError('file tree contains a malformed node')
        file_id = str(item.get('fid') or item.get('file_id') or item.get('cid') or item.get('id') or '').strip()
        name = name if name is not None else str(item.get('fn') or item.get('n') or item.get('file_name') or item.get('name') or '')
        if not file_id or not name or name in {'.', '..'} or ('/' in name and not (root and name == '/')) or '\x00' in name:
            raise TreeIntegrityError('file tree node is missing a stable identity or valid name')
        if 'is_dir' in item:
            value = item['is_dir']
            if value not in (True, False, 0, 1, '0', '1'):
                raise TreeIntegrityError('file tree node has invalid directory type')
            is_dir = value in (True, 1, '1')
        elif 'file_category' in item:
            if not str(item['file_category']).isdigit():
                raise TreeIntegrityError('file tree node has invalid file category')
            is_dir = str(item['file_category']) == '0'
        elif 'fc' in item and str(item['fc']) in {'0', '1'}:
            is_dir = str(item['fc']) == '0'
        else:
            raise TreeIntegrityError('file tree node has no valid directory type')
        return file_id, name, is_dir

    root = storage.get_file_info(root_path)
    root_id, root_name, root_is_dir = identity(root, PurePosixPath(root_path).name or '/', root=True)
    tree, seen_ids, seen_paths = [], {root_id}, set()

    def node(item, file_id, name, is_dir, relative, path):
        # Provider aliases are ordered by precedence, not truthiness. A present
        # malformed primary value must never fall through to another alias/zero.
        present = next((key for key in ('fs', 'size', 'size_byte') if key in item), None)
        if present is None and not is_dir:
            raise TreeIntegrityError('file tree file is missing its size')
        # Directory sizes are not needed for file cleanup; providers may omit them.
        size = item[present] if present is not None else 0
        valid_integer = type(size) is int and size >= 0
        valid_string = isinstance(size, str) and size.isascii() and size.isdigit()
        if not (valid_integer or valid_string):
            raise TreeIntegrityError('file tree node has an invalid size')
        return {'name': name, 'relative_path': relative, 'path': path,
                'is_dir': is_dir, 'file_id': file_id,
                'size': int(size),
                'sha1': str(item.get('sha1') or item.get('sha') or item.get('file_sha1') or '').strip()}

    if not root_is_dir:
        return [node(root, root_id, root_name, False, root_name, root_path)]

    def walk(parent_id, prefix='', depth=0):
        if depth > int(max_depth):
            raise TreeIntegrityError('file tree exceeds maximum depth 8 or requested depth')
        offset, expected_count = 0, None
        while True:
            response = storage.get_file_list({'cid': parent_id, 'offset': offset, 'limit': min(1000, int(limit)), 'show_dir': 1})
            wrappers = []
            while isinstance(response, dict):
                wrappers.append(response)
                if response.get('state') is False or response.get('success') is False:
                    raise TreeIntegrityError('file tree listing failed')
                response = response.get('list') if 'list' in response else response.get('data')
            if not isinstance(response, list):
                raise TreeIntegrityError('file tree listing is malformed')
            for wrapper in wrappers:
                if 'count' in wrapper:
                    count = wrapper['count']
                    if isinstance(count, bool) or not str(count).isdigit():
                        raise TreeIntegrityError('file tree listing count is invalid')
                    count = int(count)
                    if expected_count is not None and expected_count != count:
                        raise TreeIntegrityError('file tree listing count changed during scan')
                    expected_count = count
                if 'offset' in wrapper and str(wrapper['offset']) != str(offset):
                    raise TreeIntegrityError('file tree listing offset does not match request')
            if not response:
                if expected_count is not None and expected_count != offset:
                    raise TreeIntegrityError('file tree ended before its declared count')
                return
            if expected_count is not None and offset + len(response) > expected_count:
                raise TreeIntegrityError('file tree exceeds its declared count')
            for item in response:
                file_id, name, is_dir = identity(item)
                relative = f'{prefix}/{name}'.lstrip('/')
                if file_id in seen_ids or relative in seen_paths:
                    raise TreeIntegrityError('file tree repeats an object, page, path or directory cycle')
                if len(tree) >= int(limit):
                    raise TreeIntegrityError(f'file tree exceeds maximum node count {ceiling} or requested limit')
                seen_ids.add(file_id)
                seen_paths.add(relative)
                tree.append(node(item, file_id, name, is_dir, relative, f"{root_path.rstrip('/')}/{relative}"))
                if is_dir:
                    walk(file_id, relative, depth + 1)
            offset += len(response)

    walk(root_id)
    return tree
