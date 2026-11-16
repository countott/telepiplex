"""Read-only preflight of the document layout; never reads user media or releases."""
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.runtime.central_config import (
    CentralConfigError, ConfigChange, _ABSENT, _get, _set, _equal, _V2_CATEGORIES, PUBLIC_SCORE_MAPS,
)
from app.runtime.config_projection import (
    ADAPTERS, GITHUB, KINDS, ProjectionContract, bind, invalid, legacy_paths, project_feature_config, timeout_bounds,
    validate_directory_graph,
)
from app.runtime.config_aliases import normalize_explicit_aliases

KNOWN = ('download', 'search', 'rename', 'sync', 'caption')


@dataclass(frozen=True)
class MigrationContext:
    builtin: dict = field(default_factory=dict, repr=False)
    installed: dict = field(default_factory=dict, repr=False)
    caption_cwd: str | None = None
    caption_home: str | None = None


@dataclass(frozen=True, repr=False)
class MigrationPlan(ConfigChange):
    context: MigrationContext
    notices: tuple
    blockers: tuple

    def __repr__(self):
        return f'MigrationPlan({self.source_version} -> 3, fields={len(self.changed_paths)}, blockers={len(self.blockers)})'

    @property
    def source_version(self):
        return self.before.get('config_version', 1)

    @property
    def target_version(self):
        return 3


def load_builtin_contracts():
    from app.runtime.config_settings import load_descriptor
    try:
        data = load_descriptor()
        return {key: (row['defaults'], row['schema']) for key, row in data['features'].items()}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def differences(before, after, prefix=()):
    for key in sorted(before.keys() | after.keys()):
        path = (*prefix, key)
        old, new = before.get(key, _ABSENT), after.get(key, _ABSENT)
        if _equal(old, new):
            continue
        if path not in PUBLIC_SCORE_MAPS and isinstance(new, dict) and (isinstance(old, dict) or (old is _ABSENT and new)):
            yield from differences({} if old is _ABSENT else old, new, path)
        else:
            yield path


def _local(value, context, field):
    if not isinstance(value, str) or not value:
        invalid(field)
    path = PurePosixPath(value)
    if value.startswith('~'):
        if path.parts[0] != '~' or not context.caption_home or not PurePosixPath(context.caption_home).is_absolute():
            invalid(field, 'Unknown previous Caption HOME; specify an absolute path')
        path = PurePosixPath(context.caption_home, *path.parts[1:])
    if not path.is_absolute():
        if not context.caption_cwd or not PurePosixPath(context.caption_cwd).is_absolute():
            invalid(field, 'Unknown previous Caption release CWD; specify an absolute path')
        path = PurePosixPath(context.caption_cwd, path)
    return str(path)


def _convert(document, packages, context, notices):
    result = deepcopy(document)
    shared = result.setdefault('shared', {})
    if any(k in shared for k in ('directories', 'directory_roles')):
        invalid('shared.directory_roles', 'Existing v3 settings conflict with migration')
    features = result.setdefault('features', {})
    aliases = [('rename', ('ai','base_url'), ('ai','api_url')),
               ('download', ('request_interval',), ('endpoint_intervals',)),
               ('download', ('poll_interval',), ('poll_initial_interval',))]
    for plugin, alias, canonical in aliases:
        section = features.get(plugin, {})
        if _get(section, alias) is not _ABSENT:
            action = 'Masked' if _get(section, canonical) is not _ABSENT else 'Migrated'
            notices.append(action + ' features.' + '.'.join((plugin, *alias)))
    for key in KNOWN:
        features[key] = normalize_explicit_aliases(key, features.get(key, {}))
    for key in ('request_interval', 'poll_interval'):
        if key in features['download']:
            del features['download'][key]
    categories = shared.pop('category_folder', deepcopy(_V2_CATEGORIES))
    from app.runtime.config_projection import tmdb_token
    token = tmdb_token(shared, v3=False)
    for plugin in ('search', 'rename', 'sync'):
        section = features[plugin]
        if 'category_folder' in section and section.pop('category_folder') != categories:
            invalid(f'features.{plugin}.category_folder', 'Private categories conflict with shared categories')
        path = ('metadata', 'tmdb', 'api_key') if plugin == 'search' else ('tmdb', 'api_key')
        if plugin != 'rename' and _get(section, path) is not _ABSENT:
            if _get(section, path) != token:
                invalid(f'features.{plugin}.' + '.'.join(path), 'Private credential conflicts with shared credential')
            _set(section, path, _ABSENT)
            for size in range(len(path) - 1, 0, -1):
                if _get(section, path[:size]) != {}:
                    break
                _set(section, path[:size], _ABSENT)
    values = {key: project_feature_config(key, defaults, document=document, contract=None)
              for key, (defaults, schema) in packages.items() if key in KNOWN}
    directories = shared['directories'] = {}
    roles = shared['directory_roles'] = {}
    media = roles['media_categories'] = {}
    fields = 'shared.category_folder'
    if not isinstance(categories, list) or len(categories) != len(KINDS):
        invalid(fields)
    for plugin, snapshot in values.items():
        legacy_paths(plugin, snapshot)
    by_kind = {row['kind']: row for row in values['search']['category_folder']}
    if tuple(by_kind) != KINDS:
        roles['media_category_order'] = list(by_kind)
    for kind in KINDS:
        row = by_kind[kind]
        media[kind] = {'directory_id': bind(directories, row['path'], row['name'], kind), 'label': row['name'],
                       'plex_library_id': row['plex_library_id']}
    menu = roles['manual_downloads'] = []
    for row in values['download']['save_directories']:
        menu.append({'directory_id': bind(directories, '/' + row['path'], row['name'], 'manual'), 'label': row['name']})
    features['download'].pop('save_directories', None)
    for plugin, key, role, label in [
        ('rename', 'unorganized_path', 'unorganized_inventory', '额外盘点'),
        ('caption', 'output_path', 'standalone_subtitles', '独立字幕导出'),
    ]:
        raw = values[plugin][key]
        if role == 'unorganized_inventory' and raw == '/':
            roles[role] = None
        else:
            roles[role] = {'directory_id': bind(directories, raw, label, role)}
        features[plugin].pop(key, None)
    rename, caption, search = (features[key] for key in ('rename', 'caption', 'search'))
    for key in ('caption_enabled', 'automatic'):
        section = values['rename'] if key == 'caption_enabled' else values['caption']
        if type(section[key]) is not bool:
            invalid('features.' + ('rename.' if key == 'caption_enabled' else 'caption.') + key)
    caption['automatic'] = values['rename']['caption_enabled'] and values['caption']['automatic']
    rename.pop('caption_enabled', None)
    if 'caption_timeout' in rename:
        if 'caption_stage_timeout_seconds' in rename:
            invalid('features.rename.caption_stage_timeout_seconds')
        rename['caption_stage_timeout_seconds'] = rename.pop('caption_timeout')
    nested = search.pop('search', {})
    if 'enable' in nested:
        notices.append('Removed unused features.search.search.enable')
        nested.pop('enable')
    for key in ('scoring', 'prowlarr'):
        if key in nested:
            if key in search:
                invalid('features.search.' + key)
            search[key] = nested.pop(key)
    if nested:
        invalid('features.search.search', 'Unrecognized nested Search settings; migration requires review')
    low, high = timeout_bounds(False)
    minimum, maximum = timeout_bounds(True)
    for key in ADAPTERS:
        section = search.get('metadata', {}).get(key, {})
        if 'timeout' in section:
            value = section['timeout']
            if type(value) not in (int, float) or not minimum <= value <= maximum:
                invalid(f'features.search.metadata.{key}.timeout')
            section['timeout'] = min(high, max(low, value))

    if 'provider_defaults' in caption:
        invalid('features.caption.provider_defaults')
    common = caption.setdefault('provider_defaults', {}).setdefault('github', {})
    for key in ('github_token', 'cache_ttl_seconds'):
        previous = [values['caption']['providers'][provider][key] for provider in GITHUB]
        default = packages['caption'][0]['providers'][GITHUB[0]][key]
        common[key] = previous[0] if all(item == previous[0] for item in previous) else default
        for provider, value in zip(GITHUB, previous):
            section = caption.get('providers', {}).get(provider, {})
            if value != common[key]:
                caption.setdefault('providers', {}).setdefault(provider, {})[key] = value
            elif key in section and not (key == 'github_token' and value == ''):
                section.pop(key)
    local = values['caption']['providers']['local_archive']
    for key in ('roots', 'mapping_file'):
        if local[key]:
            field = 'features.caption.providers.local_archive.' + key
            if key == 'roots' and not isinstance(local[key], list):
                invalid(field)
            caption.setdefault('providers', {}).setdefault('local_archive', {})[key] = (
                [_local(v, context, field) for v in local[key]] if key == 'roots' else _local(local[key], context, field))
    for key in KNOWN:
        if not features[key] and key not in document.get('features', {}):
            del features[key]
    result['config_version'] = 3
    validate_directory_graph(shared)
    return result


def plan_v3_migration(document, *, context):
    from app.runtime.plugin_store import _validate, StoreError
    candidate = document
    notices, blockers = [], []
    packages = {**context.builtin, **context.installed}
    if document.get('config_version') != 3:
        low, high = timeout_bounds(False)
        minimum, maximum = timeout_bounds(True)
        for key in ADAPTERS:
            value = _get(document, ('features', 'search', 'metadata', key, 'timeout'))
            if type(value) in (int, float) and minimum <= value <= maximum and not low <= value <= high:
                notices.append(f'features.search.metadata.{key}.timeout: {value} -> {min(high, max(low, value))} (legacy effective)')
        blockers = [f'contracts.{key}: missing historical contract' for key in KNOWN if key not in packages]
        try:
            from app.runtime.config_projection import tmdb_token
            tmdb_token(document.get('shared', {}), v3=False)
        except CentralConfigError as exc:
            blockers.extend(exc.details['config_error_paths'])
        if not blockers:
            try:
                candidate = _convert(document, packages, context, notices)
                for plugin, (defaults, schema) in packages.items():
                    snapshot = project_feature_config(plugin, defaults, document=candidate,
                                                      contract=ProjectionContract(schema, plugin in context.installed))
                    _validate(schema, snapshot)
            except (CentralConfigError, StoreError) as exc:
                blockers = exc.details.get('config_error_paths') or ['feature contract']
                blockers = [f'{path}: {exc.message}' for path in blockers]
            except (KeyError, TypeError, ValueError, AttributeError):
                blockers = ['features: invalid legacy settings or incomplete historical contract']
    else:
        # Existing IDs are authoritative. Diagnose each consumer independently;
        # never regenerate the graph as a side effect of restarting a v3 host.
        try:
            validate_directory_graph(document.get('shared', {}))
        except CentralConfigError as exc:
            blockers.extend(exc.details['config_error_paths'])
        for plugin in (*KNOWN, *(key for key in packages if key not in KNOWN)):
            try:
                defaults, schema = packages.get(plugin, ({}, {}))
                contract = ProjectionContract(schema, plugin in context.installed) if plugin in packages else None
                snapshot = project_feature_config(plugin, defaults, document=document, contract=contract)
                if plugin in packages:
                    _validate(schema, snapshot, flat_search=plugin == 'search')
            except (CentralConfigError, StoreError) as exc:
                blockers.extend(exc.details.get('config_error_paths') or [f'features.{plugin}'])
            except (KeyError, TypeError, AttributeError, ValueError):
                blockers.append(f'features.{plugin}: invalid settings')
    if blockers:
        candidate = document
    return MigrationPlan(deepcopy(document), deepcopy(candidate), tuple(differences(document, candidate)),
                         tuple(packages), context, tuple(notices), tuple(dict.fromkeys(blockers)))


def export_v2_candidate(document, *, context):
    """Project *current* canonical settings through each target schema, without writing."""
    from app.runtime.central_config import _check_document
    from app.runtime.plugin_store import _validate
    if document.get('config_version') != 3:
        invalid('config_version')
    packages = {**context.builtin, **context.installed}
    candidate = deepcopy(document)
    shared = candidate.setdefault('shared', {})
    validate_directory_graph(shared)
    expected = {}
    for key in (*KNOWN, *(k for k in packages if k not in KNOWN)):
        if key not in packages:
            invalid('contracts.' + key)
        defaults, schema = packages[key]
        projected = project_feature_config(key, defaults, document=document, contract=ProjectionContract(schema))
        if key == 'search':
            from app.runtime.config_projection import timeouts
            # v2 readers always use the historical range.
            timeouts(projected, legacy=False, extended=False)
        value = _validate(schema, projected)
        expected[key] = projected
        raw = deepcopy(document.get('features', {}).get(key, {}))
        if key in ('search', 'rename', 'sync'):
            shared['category_folder'] = value['category_folder']
        if key == 'search':
            for name in ('prowlarr', 'scoring'):
                if name in raw:
                    raw.setdefault('search', {})[name] = raw.pop(name)
        elif key == 'download':
            raw['save_directories'] = value['save_directories']
        elif key == 'rename':
            raw.update(unorganized_path=value['unorganized_path'], caption_enabled=value['caption_enabled'])
            if 'caption_stage_timeout_seconds' in raw:
                raw['caption_timeout'] = raw.pop('caption_stage_timeout_seconds')
        elif key == 'caption':
            raw.pop('provider_defaults', None)
            raw['output_path'] = value['output_path']
            for provider in GITHUB:
                for field in ('github_token', 'cache_ttl_seconds'):
                    path = ('providers', provider, field)
                    if not _equal(_get(value, path), _get(defaults, path)):
                        _set(raw, path, _get(value, path))
        if raw or key in document.get('features', {}):
            candidate.setdefault('features', {})[key] = raw
    shared.pop('directories', None)
    shared.pop('directory_roles', None)
    candidate['config_version'] = 2
    _check_document(candidate)
    for key, snapshot in expected.items():
        defaults, schema = packages[key]
        actual = project_feature_config(key, defaults, document=candidate, contract=ProjectionContract(schema))
        _validate(schema, actual)
        if not _equal(actual, snapshot):
            paths = list(differences(snapshot, actual))
            from app.runtime.central_config import _safe_paths
            raise CentralConfigError('config_migration_required', 'Old Host cannot preserve the configured behavior',
                                     {'config_error_paths': _safe_paths([('features', key, *path) for path in paths])})
    # A matching live snapshot cannot prove that a future shared edit still
    # reaches the same providers, or that bindings retain their operator IDs.
    restored = _convert(candidate, packages, context, [])
    locations = [('shared','directories'), ('shared','directory_roles'),
                 ('features','caption','provider_defaults'), ('features','caption','automatic')]
    locations.extend(('features','caption','providers',provider,key)
                     for provider in GITHUB for key in ('github_token','cache_ttl_seconds'))
    lost = []
    for path in locations:
        old, new = _get(document, path), _get(restored, path)
        if not _equal(old, new):
            if isinstance(old, dict) and isinstance(new, dict):
                lost.extend(differences(old, new, path))
            else:
                lost.append(path)
    if lost:
        from app.runtime.central_config import _safe_paths
        raise CentralConfigError('config_migration_required', 'Old Host cannot preserve the configured settings',
                                 {'config_error_paths': _safe_paths(lost)})
    return candidate
