"""Translate canonical settings into the private snapshot of one release."""
from copy import deepcopy
from dataclasses import dataclass
import re

from telepiplex_plugin_sdk.storage_paths import normalize_115_directory
from app.runtime.config_aliases import normalize_explicit_aliases
from app.runtime.central_config import CentralConfigError, _V2_CATEGORIES, _defaults, _set, _KEY, _get, _ABSENT

from app.runtime.config_defaults import GITHUB_PROVIDERS as GITHUB, MEDIA_CATEGORIES, TMDB_TOKEN, category_defaults

ADAPTERS = ('tmdb', 'tvdb', 'anilist')
KINDS = tuple(kind for kind, name in MEDIA_CATEGORIES)
ROLE_CONSUMERS = {'media_categories': ('search', 'rename', 'sync'), 'manual_downloads': ('download',),
                  'unorganized_inventory': ('rename',), 'standalone_subtitles': ('caption',)}


@dataclass(frozen=True)
class ProjectionContract:
    schema: dict
    enforce_runtime: bool = True
    migration_source: bool = False

    @property
    def extended_timeout(self):
        return self.schema.get('x-telepiplex-metadata-timeout-contract') == 'seconds_1_300_v1'


def invalid(field, message='Invalid configuration field'):
    raise CentralConfigError('invalid_config', message, {'config_error_paths': [field]})


def cloud(value, field, *, root=False, relative=False):
    try:
        return normalize_115_directory(value, allow_root=root, allow_relative=relative)
    except ValueError:
        invalid(field, 'Invalid 115 directory')


def inventory_path(path, field):
    # These inventory consumers do not agree on a trailing segment space:
    # Rename cleanup and historical Caption menus strip it; 115 storage does not.
    if path != path.rstrip():
        invalid(field, 'Current inventory consumers disagree on trailing whitespace')
    return path


def bind(directories, path, label, hint):
    for key, entity in directories.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(entity, dict):
            invalid('shared.directories')
        if cloud(entity.get('path'), 'shared.directories.' + key + '.path', root=True) == path:
            return key
    key = hint
    index = 1
    while key in directories:
        index += 1
        key = f'{hint}_{index}'
    directories[key] = {'name': label, 'path': path}
    return key


def validate_directory_graph(shared):
    if 'category_folder' in shared:
        invalid('shared.category_folder', 'Obsolete category list in v3; repair the directory bindings')
    entities = shared.get('directories', {})
    if not isinstance(entities, dict):
        invalid('shared.directories')
    paths = set()
    for key, entity in entities.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            invalid('shared.directories')
        field = 'shared.directories.' + key
        if not isinstance(entity, dict) or not isinstance(entity.get('name'), str) or not entity['name']:
            invalid(field + '.name')
        path = cloud(entity.get('path'), field + '.path', root=True)
        if path in paths:
            invalid(field + '.path', 'Another directory ID already owns this 115 path')
        paths.add(path)
    for role in ROLE_CONSUMERS:
        role_value(shared, role, None)


def role_value(shared, role, default):
    roles = shared.get('directory_roles', {})
    field = 'shared.directory_roles.' + role
    if not isinstance(roles, dict):
        invalid('shared.directory_roles')
    if role == 'media_categories':
        order = roles.get('media_category_order', list(KINDS))
        if (not isinstance(order, list) or not all(isinstance(kind, str) for kind in order)
                or len(order) != len(KINDS) or set(order) != set(KINDS)):
            invalid('shared.directory_roles.media_category_order')
        if role not in roles and 'media_category_order' in roles:
            fallback = {row['kind']: row for row in (default or category_defaults())}
            return [deepcopy(fallback[kind]) for kind in order]
    if role not in roles:
        return deepcopy(default)
    binding = roles[role]
    entities = shared.get('directories', {})
    seen, ambiguous = set(), set()
    if isinstance(entities, dict):
        for entity in entities.values():
            if not isinstance(entity, dict):
                continue
            try:
                path = normalize_115_directory(entity.get('path'), allow_relative=False, allow_root=True)
            except ValueError:
                continue  # Report malformed paths only to their actual consumers.
            if path in seen:
                ambiguous.add(path)
            seen.add(path)

    def resolve(binding, field, label=False):
        if not isinstance(binding, dict) or not isinstance(entities, dict):
            invalid(field)
        key = binding.get('directory_id')
        entity = entities.get(key) if isinstance(key, str) and _KEY.fullmatch(key) else None
        if not isinstance(entity, dict):
            invalid(field + '.directory_id', 'Unknown 115 directory')
        name = binding.get('label', entity.get('name'))
        if label and not isinstance(name, str):
            invalid(field + '.label')
        path = cloud(entity.get('path'), 'shared.directories.' + key + '.path', root=role == 'standalone_subtitles')
        if path in ambiguous:
            invalid('shared.directories.' + key + '.path', 'Another directory ID already owns this 115 path')
        if role in ('media_categories', 'unorganized_inventory'):
            inventory_path(path, field)
        return path, name

    if role == 'media_categories':
        if not isinstance(binding, dict) or set(binding) != set(KINDS):
            invalid(field)
        rows = []
        for kind in order:
            row = binding[kind]
            path, name = resolve(row, field + '.' + kind, True)
            if not isinstance(row.get('plex_library_id'), str):
                invalid(field + '.' + kind + '.plex_library_id')
            rows.append({'kind': kind, 'name': name, 'path': path, 'plex_library_id': row['plex_library_id']})
        return rows
    if role == 'manual_downloads':
        if not isinstance(binding, list):
            invalid(field)
        return [{'path': path[1:], 'name': name} for path, name in (resolve(row, field, True) for row in binding)]
    if role == 'unorganized_inventory' and binding is None:
        return '/'
    return resolve(binding, field)[0]


def tmdb_token(shared, *, v3=True):
    tmdb = shared.get('tmdb', {})
    if not isinstance(tmdb, dict):
        invalid('shared.tmdb')
    token = tmdb.get('api_key', TMDB_TOKEN if v3 else '')
    if not isinstance(token, str):
        invalid('shared.tmdb.api_key')
    return token


def category_view(document):
    shared = document.get('shared', {})
    if document.get('config_version') == 3:
        if 'category_folder' in shared:
            invalid('shared.category_folder', 'Obsolete category list in v3; repair the directory bindings')
        return role_value(shared, 'media_categories', category_defaults())
    return deepcopy(shared.get('category_folder', _V2_CATEGORIES))


def github_defaults(raw, schema=None):
    field = 'features.caption.provider_defaults'
    section = raw.get('provider_defaults', {})
    if not isinstance(section, dict) or section.keys() - {'github'}:
        invalid(field)
    common = section.get('github', {})
    if not isinstance(common, dict) or common.keys() - {'github_token', 'cache_ttl_seconds'}:
        invalid(field + '.github')
    if common:
        from app.runtime.config_settings import leaf_schema, load_descriptor
        from jsonschema import Draft202012Validator
        for key, value in common.items():
            path = ('providers', GITHUB[0], key)
            spec = leaf_schema(schema or {}, path)
            if not spec:
                spec = leaf_schema(load_descriptor().get('features', {}).get('caption', {}).get('schema', {}), path)
            if (not spec or not Draft202012Validator(spec).is_valid(value)
                    or (isinstance(value, str) and spec.get('pattern', '').startswith('^') and spec['pattern'].endswith('$')
                        and not re.fullmatch(spec['pattern'], value))):
                invalid(field + '.github.' + key)
    return common


def shared_view(document, *, include_categories=True):
    shared = deepcopy(document.get('shared', {}))
    if include_categories:
        shared['category_folder'] = category_view(document)
    # Preserve an invalid explicit value so the repair draft can compare it.
    # Its replacement and any consuming release are validated separately.
    tmdb = shared.setdefault('tmdb', {})
    if isinstance(tmdb, dict):
        tmdb.setdefault('api_key', TMDB_TOKEN if document.get('config_version') == 3 else '')
    return shared


def shared_snapshot_changed(plugin, document, running, defaults):
    """Compare each independently readable shared field to this live instance."""
    shared = document.get('shared', {})
    v3 = document.get('config_version') == 3
    roles = [('media_categories', 'category_folder', category_defaults())]
    if v3:
        roles.extend([('unorganized_inventory', 'unorganized_path', defaults.get('unorganized_path')),
                      ('manual_downloads', 'save_directories', defaults.get('save_directories')),
                      ('standalone_subtitles', 'output_path', defaults.get('output_path'))])
    for role, key, fallback in roles:
        if plugin not in ROLE_CONSUMERS[role]:
            continue
        try:
            if v3:
                wanted = role_value(shared, role, fallback)
            else:
                snapshot = {key: category_view(document)}
                legacy_paths(plugin, snapshot)
                wanted = snapshot[key]
        except CentralConfigError:
            # This field has no safe new value. Any candidate actually needing
            # it is rejected by full projection before the transaction switches.
            continue
        if running.get(key) != wanted:
            return True
    if plugin in ('search', 'sync'):
        path = ('metadata', 'tmdb', 'api_key') if plugin == 'search' else ('tmdb', 'api_key')
        try:
            if _get(running, path) != tmdb_token(shared, v3=v3):
                return True
        except CentralConfigError:
            pass
    if v3 and plugin in ('caption', 'rename'):
        key = 'automatic' if plugin == 'caption' else 'caption_enabled'
        wanted = document.get('features', {}).get('caption', {}).get('automatic', defaults.get(key))
        return running.get(key) != wanted
    return False


def timeout_bounds(extended):
    return (1, 300) if extended else (5, 60)


def timeouts(value, *, legacy, extended):
    low, high = timeout_bounds(extended and not legacy)
    for adapter in ADAPTERS:
        section = value.get('metadata', {}).get(adapter, {})
        if not isinstance(section, dict) or 'timeout' not in section:
            continue
        timeout = section['timeout']
        if type(timeout) not in (int, float):
            invalid(f'features.search.metadata.{adapter}.timeout')
        if legacy:
            section['timeout'] = min(high, max(low, timeout))
        elif not low <= timeout <= high:
            invalid(f'features.search.metadata.{adapter}.timeout', 'Timeout unsupported by target Search contract')


def legacy_paths(plugin, value):
    """Freeze the effective v2 input before handing it to old or new packages."""
    if plugin in ROLE_CONSUMERS['media_categories']:
        rows = value['category_folder']
        if (not isinstance(rows, list) or len(rows) != 4 or
            not all(isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ('kind','path','name','plex_library_id')) for r in rows)
            or {r['kind'] for r in rows} != set(KINDS)):
            invalid('shared.category_folder')
        for row in rows:
            path = cloud(row['path'], 'shared.category_folder')
            row['path'] = inventory_path(path, 'shared.category_folder')
    if plugin == 'download' and 'save_directories' in value:
        field = 'features.download.save_directories'
        names, paths = set(), set()
        if not isinstance(value['save_directories'], list):
            invalid(field)
        for row in value['save_directories']:
            if not isinstance(row, dict) or set(row) != {'name', 'path'} or not all(isinstance(v,str) for v in row.values()):
                invalid(field)
            name, raw = row['name'].strip(), row['path'].strip()
            if not name or raw.startswith('/') or '\n' in name or '\r' in name:
                invalid(field)
            path = cloud(raw, field, relative=True)[1:]
            if name in names or path in paths:
                invalid(field)
            names.add(name)
            paths.add(path)
            row.update(name=name, path=path)
    if plugin == 'caption' and 'output_path' in value:
        raw = value['output_path']
        value['output_path'] = cloud(raw, 'features.caption.output_path', root=True)
    if plugin == 'rename' and 'unorganized_path' in value:
        field = 'features.rename.unorganized_path'
        value['unorganized_path'] = inventory_path(cloud(value['unorganized_path'], field, root=True, relative=True), field)


def project_feature_config(plugin_id, defaults, *, document, contract):
    features = document.get('features', {})
    shared = document.get('shared', {})
    v3 = document.get('config_version') == 3
    raw = normalize_explicit_aliases(plugin_id, features.get(plugin_id, {}))
    if v3:
        shadows = {'search': [('search',), ('category_folder',), ('metadata','tmdb','api_key')],
                   'sync': [('category_folder',), ('tmdb','api_key')],
                   'rename': [('category_folder',), ('unorganized_path',), ('caption_timeout',), ('caption_enabled',)],
                   'caption': [('output_path',)], 'download': [('save_directories',)]}
        for path in shadows.get(plugin_id, ()):
            if _get(raw, path) is not _ABSENT:
                invalid('features.' + plugin_id + '.' + '.'.join(path), 'Obsolete private shadow in v3 settings')
    if v3 and plugin_id == 'search':
        # Search packages retain the nested wrapper; metadata remains a sibling.
        wrapped = {k: raw.pop(k) for k in ('prowlarr', 'scoring') if k in raw}
        if wrapped:
            raw['search'] = wrapped
    if v3 and plugin_id == 'caption':
        common = github_defaults(raw, contract.schema if contract else None)
        raw.pop('provider_defaults', None)
        for provider in GITHUB:
            if common:
                target = raw.setdefault('providers', {}).setdefault(provider, {})
                for key in ('github_token', 'cache_ttl_seconds'):
                    if key in common:
                        target.setdefault(key, common[key])
    if v3 and plugin_id == 'rename':
        if 'caption_stage_timeout_seconds' in raw:
            raw['caption_timeout'] = raw.pop('caption_stage_timeout_seconds')
        if 'automatic' in features.get('caption', {}):
            raw['caption_enabled'] = features['caption']['automatic']
    value = _defaults(defaults, raw)
    if plugin_id in ('search', 'rename', 'sync'):
        value['category_folder'] = category_view(document)
    if plugin_id in ('search', 'sync'):
        path = ('metadata', 'tmdb', 'api_key') if plugin_id == 'search' else ('tmdb', 'api_key')
        _set(value, path, tmdb_token(shared, v3=v3))
    if v3:
        for consumer, role, key in [('download', 'manual_downloads', 'save_directories'),
                                    ('rename', 'unorganized_inventory', 'unorganized_path'),
                                    ('caption', 'standalone_subtitles', 'output_path')]:
            if consumer == plugin_id and (key in value or role in shared.get('directory_roles', {})):
                value[key] = role_value(shared, role, value.get(key))
    if v3 and contract is not None and contract.enforce_runtime:
        if (plugin_id == 'rename' and _get(shared, ('directory_roles', 'unorganized_inventory')) is None
            and contract.schema.get('x-telepiplex-inventory-root-contract') != 'null_disables_extra_root_v1'):
            invalid('shared.directory_roles.unorganized_inventory', 'Upgrade Rename to disable the extra root in all inventory menus')
        if (plugin_id == 'download' and contract.schema.get('x-telepiplex-download-directory-contract') != 'preserve_segment_spaces_v1'
            and any(row['path'].strip() != row['path'] for row in value.get('save_directories', []))):
            invalid('shared.directory_roles.manual_downloads', 'Upgrade Download to preserve directory segment spaces')
    if document.get('config_version') == 2 and contract is not None:
        legacy_paths(plugin_id, value)
        if (plugin_id == 'download' and contract.enforce_runtime
                and contract.schema.get('x-telepiplex-download-directory-contract') != 'preserve_segment_spaces_v1'
                and any(row['path'] != row['path'].strip() for row in value.get('save_directories', []))):
            invalid('features.download.save_directories', 'Upgrade Download to preserve directory segment spaces')
    if plugin_id == 'search' and contract is not None:
        timeouts(value, legacy=not v3, extended=contract.migration_source or contract.extended_timeout)
    return value
