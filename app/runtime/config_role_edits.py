"""Translate authenticated legacy edits back to their canonical owners."""
from copy import deepcopy

from app.runtime.central_config import ConfigChange, CentralConfigError, _get, _leaves, _set, _mapped, _ABSENT, _equal, PUBLIC_SCORE_MAPS, PRIVATE_SCORE_MAPS
from app.runtime.config_defaults import category_defaults
from app.runtime.config_projection import ADAPTERS, KINDS, ROLE_CONSUMERS, bind, cloud, role_value, invalid, validate_directory_graph
from app.runtime.config_semantics_migration import differences


def check_timeout_edit(document, plugin, patch, contract, baseline):
    if plugin != 'search' or contract is None:
        return
    extended = document.get('config_version') == 3 and contract.extended_timeout
    for adapter in ADAPTERS:
        path = ('metadata', adapter, 'timeout')
        timeout = _get(patch, path)
        if timeout is _ABSENT:
            continue
        from app.runtime.config_projection import timeout_bounds
        low, high = timeout_bounds(extended)
        if type(timeout) not in (int, float) or not low <= timeout <= high:
            invalid('features.search.' + '.'.join(path), 'Unsupported timeout; upgrade Search and migrate configuration')


def _conflict(field):
    raise CentralConfigError('config_conflict', 'Directory settings changed; reopen the editor',
                             {'config_error_paths': [field]})


def _rows(rows, keys, field):
    if not isinstance(rows, list) or not all(isinstance(row, dict) and set(row) == keys
        and all(isinstance(v, str) for v in row.values()) for row in rows):
        invalid(field)


def _category_order(rows, field):
    _rows(rows, {'kind', 'name', 'path', 'plex_library_id'}, field)
    order = [row['kind'] for row in rows]
    if len(order) != len(KINDS) or set(order) != set(KINDS):
        invalid(field)
    return order


def merge_category_draft(raw, latest, rows, baseline):
    """Merge the fields actually edited, preserving concurrent raw v2 values."""
    field = 'shared.category_folder'
    wanted = _category_order(rows, field)
    previous = _category_order(baseline, field)
    current_order = _category_order(latest, field)
    if wanted != previous and current_order != previous:
        _conflict(field)
    old = {row['kind']: row for row in baseline}
    actual = {row['kind']: row for row in latest}
    result = {row['kind']: deepcopy(row) for row in raw}
    for row in rows:
        kind = row['kind']
        for key in ('name', 'path', 'plex_library_id'):
            if row[key] != old[kind][key]:
                if actual[kind][key] != old[kind][key]:
                    _conflict(field)
                result[kind][key] = row[key]
    return [result[kind] for kind in (wanted if wanted != previous else current_order)]


def _entity_guard(guards, binding, field):
    if isinstance(binding, dict):
        guards.add(('shared', 'directories', binding['directory_id'], field))


def _materialize_categories(shared, resolved, rows, baseline, guards):
    # The draft was opened against implicit bindings. Preserve those values
    # by kind, using existing entities whose paths still match at commit.
    merged = merge_category_draft(resolved, resolved, rows, baseline)
    by_kind = {row['kind']: row for row in merged}
    roles = shared.setdefault('directory_roles', {})
    if list(by_kind) != [row['kind'] for row in resolved]:
        roles['media_category_order'] = list(by_kind)
    directories = shared.setdefault('directories', {})
    bindings = {}
    for kind in KINDS:
        row = by_kind[kind]
        path = cloud(row['path'], 'shared.directory_roles.media_categories.' + kind, relative=True)
        directory_id = bind(directories, path, row['name'], kind)
        binding = {'directory_id': directory_id, 'label': row['name'], 'plex_library_id': row['plex_library_id']}
        bindings[kind] = binding
        _entity_guard(guards, binding, 'path')
    guards.add(('shared', 'directory_roles', 'media_categories'))
    roles['media_categories'] = bindings


def _category_edit(shared, rows, baseline, guards):
    field = 'shared.directory_roles.media_categories'
    wanted_order = _category_order(rows, field)
    if baseline is None:
        invalid(field, 'Directory edits require the source snapshot')
    previous_order = _category_order(baseline, field)
    if rows == baseline:
        return
    resolved = role_value(shared, 'media_categories', category_defaults())
    if 'media_categories' not in shared.get('directory_roles', {}):
        _materialize_categories(shared, resolved, rows, baseline, guards)
        return
    latest = {r['kind']: r for r in resolved}
    if wanted_order != previous_order:
        if list(latest) != previous_order:
            _conflict(field)
        shared['directory_roles']['media_category_order'] = wanted_order
    old = {r['kind']: r for r in baseline}
    for row in rows:
        kind = row['kind']
        if kind not in old or kind not in latest:
            _conflict(field)
        target = shared['directory_roles']['media_categories'][kind]
        source = target.copy()
        for key, dest in [('path', 'directory_id'), ('name', 'label'), ('plex_library_id', 'plex_library_id')]:
            if row[key] == old[kind][key]:
                continue
            if latest[kind][key] != old[kind][key]:
                _conflict(field + '.' + kind)
            if key == 'path':
                _entity_guard(guards, source, 'path')
            elif key == 'name' and 'label' not in source:
                _entity_guard(guards, source, 'name')
                guards.add(('shared', 'directory_roles', 'media_categories', kind, 'directory_id'))
            target[dest] = (bind(shared['directories'], cloud(row[key], field, relative=True), row['name'], kind)
                            if key == 'path' else row[key])
            if key == 'path':
                _entity_guard(guards, target, 'path')


def _manual_edit(shared, rows, baseline, guards, fallback):
    field = 'shared.directory_roles.manual_downloads'
    _rows(rows, {'name', 'path'}, field)
    if baseline is None:
        invalid(field, 'Directory edits require the source snapshot')
    if rows == baseline:
        return
    latest = role_value(shared, 'manual_downloads', fallback)
    if latest != baseline:
        _conflict(field)
    roles = shared.setdefault('directory_roles', {})
    for binding in roles.get('manual_downloads', []):
        _entity_guard(guards, binding, 'path')
        if 'label' not in binding:
            _entity_guard(guards, binding, 'name')
    entries = []
    names, paths = set(), set()
    for row in rows:
        path = cloud(row['path'], field, relative=True)
        if path in paths or row['name'] in names:
            invalid(field)
        names.add(row['name'])
        paths.add(path)
        entries.append({'directory_id': bind(shared.setdefault('directories', {}), path, row['name'], 'manual'), 'label': row['name']})
    for binding in entries:
        _entity_guard(guards, binding, 'path')
    roles['manual_downloads'] = entries


def canonical_path(plugin, path):
    if plugin == 'search' and path[:1] == ('search',):
        if len(path) < 2 or path[1] not in ('prowlarr', 'scoring'):
            invalid('features.search.search', 'Obsolete or unknown Search setting')
        return ('features', 'search', *path[1:])
    if plugin == 'rename' and path == ('caption_enabled',):
        return ('features', 'caption', 'automatic')
    if plugin == 'rename' and path == ('caption_timeout',):
        return ('features', 'rename', 'caption_stage_timeout_seconds')
    return _mapped(plugin, path)


def feature_change(before, plugin, patch, baseline, *, defaults=None, contract=None):
    after = deepcopy(before)
    shared = after.get('shared', {})
    guards = set()
    for path, value in _leaves(patch, atomic=PRIVATE_SCORE_MAPS if plugin == 'search' else ()):
        if not all(isinstance(key, str) for key in path):
            invalid('features')
        previous = _get(baseline or {}, path)
        if path == ('category_folder',) and plugin in ROLE_CONSUMERS['media_categories']:
            _category_edit(shared, value, (baseline or {}).get(path[0]), guards)
        elif plugin == 'download' and path == ('save_directories',):
            _manual_edit(shared, value, (baseline or {}).get(path[0]), guards, (defaults or {}).get(path[0], []))
        elif (plugin, path) in [('rename', ('unorganized_path',)), ('caption', ('output_path',))]:
            if _equal(previous, value):
                continue
            role = 'unorganized_inventory' if plugin == 'rename' else 'standalone_subtitles'
            fallback = (defaults or {}).get(path[0], previous)
            if previous is not _ABSENT and role_value(shared, role, fallback) != previous:
                _conflict('shared.directory_roles.' + role)
            roles = shared.setdefault('directory_roles', {})
            guards.add(('shared', 'directory_roles', role))
            target = None if plugin == 'rename' and value == '/' else {'directory_id': bind(shared.setdefault('directories', {}),
                cloud(value, 'shared.directory_roles.' + role, relative=True, root=plugin == 'caption'), role, role)}
            _entity_guard(guards, roles.get(role), 'path')
            _entity_guard(guards, target, 'path')
            roles[role] = target
        else:
            _set(after, canonical_path(plugin, path), value)
    if shared:
        after['shared'] = shared
    if plugin == 'caption' and 'provider_defaults' in patch:
        from app.runtime.config_projection import github_defaults
        github_defaults(after['features']['caption'], contract.schema if contract else None)
    return make_change(before, after, guards)


def make_change(before, after, guards=()):
    paths = tuple(differences(before, after))
    affected = set()
    for path in paths:
        if path[:1] == ('features',) and len(path) > 1:
            affected.add(path[1])
            if path[:3] == ('features', 'caption', 'automatic'):
                affected.add('rename')
        if path[:2] == ('shared', 'tmdb'):
            affected.update(('search', 'sync'))
    if any(p[:2] == ('shared', 'tmdb') for p in paths):
        from app.runtime.config_projection import tmdb_token
        tmdb_token(after.get('shared', {}))
    if after.get('config_version') == 3 and any(
        p == ('shared',) or p[:2] in (('shared','directory_roles'),('shared','directories'),('shared','category_folder'))
        for p in paths
    ):
        shared = after.get('shared', {})
        validate_directory_graph(shared)
        for role, consumers in ROLE_CONSUMERS.items():
            new = role_value(shared, role, None)
            try:
                old = role_value(before.get('shared', {}), role, None)
            except CentralConfigError:
                old = _ABSENT
            if old != new:
                affected.update(consumers)
    return ConfigChange(before, after, paths, tuple(sorted(affected)), read_paths=tuple(sorted(set(guards) - set(paths))))


def canonical_change(before, patch):
    guards = set()
    after = deepcopy(before)
    for path, value in _leaves(patch, atomic=PUBLIC_SCORE_MAPS):
        if path == ('shared', 'category_folder'):
            shared = after.setdefault('shared', {})
            _category_edit(shared, value, role_value(shared, 'media_categories', category_defaults()), guards)
        else:
            _set(after, path, value)
    if _get(patch, ('features','caption','provider_defaults')) is not _ABSENT:
        from app.runtime.config_projection import github_defaults
        github_defaults(after['features']['caption'])
    return make_change(before, after, guards)
