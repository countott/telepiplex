"""Authoritative YAML storage and isolated, backwards-compatible projections."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

import yaml



class CentralConfigError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = deepcopy(details or {})


@dataclass(frozen=True)
class ConfigChange:
    before: dict
    after: dict
    changed_paths: tuple[tuple[str, ...], ...]
    affected_plugins: tuple[str, ...]
    read_paths: tuple[tuple[str, ...], ...] = field(default=(), kw_only=True)
    directory_reference_ids: tuple[str, ...] = field(default=(), kw_only=True)


_ABSENT = object()
_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,63}$')
_V2_CATEGORIES = [
    {'kind': kind, 'name': name, 'path': '/' + name, 'plex_library_id': ''}
    for kind, name in [('live_action_movie', '真人电影'), ('animated_movie', '动画电影'),
                       ('live_action_series', '真人剧集'), ('animated_series', '动画剧集')]
]


def _equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _parents_compatible(current, expected, path):
    for length in range(1, len(path)):
        actual = _get(current, path[:length])
        old = _get(expected, path[:length])
        if actual is not _ABSENT and not isinstance(actual, dict) and not _equal(actual, old):
            return False
    return True


def _get(document, path):
    value = document
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return _ABSENT
        value = value[part]
    return value


def _set(document, path, value):
    parent = document
    for part in path[:-1]:
        if not isinstance(parent.get(part), dict):
            parent[part] = {}
        parent = parent[part]
    if value is _ABSENT:
        parent.pop(path[-1], None)
    else:
        parent[path[-1]] = deepcopy(value)


_SCORE_TABLES = ("keyword_scores", "indexer_scores")
PRIVATE_SCORE_MAPS = {("search", "scoring", key) for key in _SCORE_TABLES}
PUBLIC_SCORE_MAPS = {("features", "search", "scoring", key) for key in _SCORE_TABLES}


def _leaves(value, prefix=(), *, atomic=()):
    for key, item in value.items():
        path = (*prefix, key)
        if isinstance(item, dict) and item and path not in atomic:
            yield from _leaves(item, path, atomic=atomic)
        else:
            yield path, item


def _defaults(default, current, prefix=()):
    result = deepcopy(default)
    for key, value in current.items():
        path = (*prefix, key)
        if (isinstance(value, dict) and value and isinstance(result.get(key), dict)
            and path not in PRIVATE_SCORE_MAPS):
            result[key] = _defaults(result[key], value, path)
        else:
            result[key] = deepcopy(value)
    return result


def _mapped(plugin_id, path):
    if plugin_id in ('search', 'rename', 'sync') and path[:1] == ('category_folder',):
        return ('shared', *path)
    credential = ('metadata', 'tmdb', 'api_key') if plugin_id == 'search' else ('tmdb', 'api_key')
    if plugin_id in ('search', 'sync') and path == credential:
        return ('shared', 'tmdb', 'api_key')
    return ('features', plugin_id, *path)


def _safe_paths(paths):
    return ['.'.join(path) for path in paths if all(_KEY.fullmatch(p) for p in path)][:20]


def _check_document(document, *, semantic=True):
    if not isinstance(document, dict):
        raise CentralConfigError('invalid_config', 'main config must be a mapping')
    invalid = []
    if 'config_version' in document and (
        type(document['config_version']) is not int or document['config_version'] not in (1, 2, 3)
    ):
        invalid.append(('config_version',))
    for key in ('shared', 'features'):
        if key in document and not isinstance(document[key], dict):
            invalid.append((key,))
    for plugin, section in (document.get('features') or {}).items() if not invalid else ():
        if not isinstance(plugin, str) or not _KEY.fullmatch(plugin) or not isinstance(section, dict):
            invalid.append(('features',))
    if isinstance(document.get('shared'), dict):
        shared = document['shared']
        if semantic and 'category_folder' in shared and not isinstance(shared['category_folder'], list):
            invalid.append(('shared', 'category_folder'))
        elif semantic and 'category_folder' in shared:
            routes = shared['category_folder']
            kinds = {item['kind'] for item in _V2_CATEGORIES}
            seen = set()
            for item in routes:
                if (not isinstance(item, dict)
                    or not isinstance(item.get('kind'), str)
                    or item['kind'] not in kinds or item['kind'] in seen
                    or any(not isinstance(item.get(key), str) for key in ('name', 'path', 'plex_library_id'))
                    or not item.get('path', '').strip().strip('/')):
                    invalid.append(('shared', 'category_folder'))
                    break
                seen.add(item['kind'])
            if seen != kinds:
                invalid.append(('shared', 'category_folder'))
    if invalid:
        raise CentralConfigError('invalid_config', 'main config has invalid sections',
                                 {'config_error_paths': _safe_paths(invalid)})
    return document


class CentralConfigStore:
    def __init__(self, path: Path, *, initial: dict | None = None):
        self.path = Path(path)
        self._initial = deepcopy(initial)
        self._lock = threading.RLock()

    def read(self) -> dict:
        with self._lock:
            if not self.path.exists() and self._initial is not None:
                return deepcopy(_check_document(self._initial, semantic=False))
            try:
                value = yaml.safe_load(self.path.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                raise CentralConfigError('invalid_config', 'cannot read main config') from None
            return deepcopy(_check_document(value, semantic=False))

    def effective(self, plugin_id: str, defaults: dict, *, document=None, contract=None) -> dict:
        from app.runtime.config_projection import project_feature_config
        document = self.read() if document is None else _check_document(document, semantic=False)
        return project_feature_config(plugin_id, defaults, document=document, contract=contract)

    def preflight(self, context):
        from app.runtime.config_semantics_migration import plan_v3_migration
        return plan_v3_migration(self.read(), context=context)

    def commit_semantics(self, plan):
        return self.commit(plan)

    def prepare_patch(self, plugin_id: str, patch: dict, *, contract=None, baseline=None, defaults=None) -> ConfigChange:
        if not isinstance(plugin_id, str) or not _KEY.fullmatch(plugin_id):
            raise CentralConfigError('invalid_config', 'invalid plugin identity')
        if not isinstance(patch, dict):
            raise CentralConfigError('invalid_config', 'config patch must be a mapping')
        from app.runtime.config_role_edits import feature_change, check_timeout_edit
        before = self.read()
        check_timeout_edit(before, plugin_id, patch, contract, baseline)
        if before.get('config_version') == 3:
            return feature_change(before, plugin_id, patch, baseline, defaults=defaults, contract=contract)
        mapped = {}
        for path, value in _leaves(patch):
            if not all(isinstance(part, str) for part in path):
                raise CentralConfigError('invalid_config', 'invalid config path')
            _set(mapped, _mapped(plugin_id, path), value)
        if baseline is not None and ('category_folder' in patch and plugin_id in ('search', 'rename', 'sync')):
            from app.runtime.config_role_edits import merge_category_draft
            raw = before.get('shared', {}).get('category_folder', _V2_CATEGORIES)
            latest = self.effective(plugin_id, {}, document=before, contract=contract)['category_folder']
            merged = merge_category_draft(raw, latest, patch['category_folder'], baseline.get('category_folder'))
            if merged == raw:
                mapped['shared'].pop('category_folder')
                if not mapped['shared']:
                    mapped.pop('shared')
            else:
                mapped['shared']['category_folder'] = merged
        return self._prepare_document_patch(before, mapped)

    def prepare_document_patch(self, patch: dict) -> ConfigChange:
        if not isinstance(patch, dict):
            raise CentralConfigError('invalid_config', 'config patch must be a mapping')
        return self._prepare_document_patch(self.read(), patch)

    def _prepare_document_patch(self, before, patch):
        if 'config_version' in patch:
            raise CentralConfigError('invalid_config', 'Document version is managed by migration')
        if before.get('config_version') == 3:
            from app.runtime.config_role_edits import canonical_change
            return canonical_change(before, patch)
        after = deepcopy(before)
        changed = []
        affected = set()
        for path, value in _leaves(patch):
            if not all(isinstance(part, str) for part in path):
                raise CentralConfigError('invalid_config', 'invalid config path')
            if _equal(_get(before, path), value):
                continue
            _set(after, path, value)
            owned = path
            for length in range(1, len(path)):
                parent = _get(before, path[:length])
                if parent is not _ABSENT and not isinstance(parent, dict):
                    owned = path[:length]
                    break
            if owned not in changed:
                changed.append(owned)
            if path[:1] == ('features',) and len(path) > 1:
                affected.add(path[1])
            elif path[:2] == ('shared', 'category_folder'):
                affected.update(('search', 'rename', 'sync'))
            elif path[:2] == ('shared', 'tmdb'):
                affected.update(('search', 'sync'))
            elif path == ('shared',):
                affected.update(('search', 'rename', 'sync'))
        if any(path[:2] == ('shared', 'tmdb') or path == ('shared',) for path in changed):
            from app.runtime.config_projection import tmdb_token
            tmdb_token(after.get('shared', {}))
        _check_document(after, semantic=any(path[:2] == ('shared', 'category_folder') or path == ('shared',) for path in changed))
        return ConfigChange(before, after, tuple(changed), tuple(sorted(affected)))

    def prepare_directory_delete(self, directory_id):
        from app.runtime.config_role_edits import make_change
        before = self.read()
        if before.get('config_version') != 3 or not isinstance(directory_id, str) or not _KEY.fullmatch(directory_id):
            raise CentralConfigError('invalid_config', 'Invalid directory ID')
        after = deepcopy(before)
        directories = after.get('shared', {}).get('directories', {})
        if directory_id not in directories:
            raise CentralConfigError('invalid_config', 'Unknown directory ID')
        del directories[directory_id]
        return make_change(before, after)

    def prepare_migration(self, plugin_id: str, before_effective: dict, after_effective: dict) -> ConfigChange:
        """Prepare schema changes without materializing unchanged package defaults."""
        before = self.read()
        after = deepcopy(before)
        paths = []
        def changes(old, new, prefix=()):
            for key in old.keys() | new.keys():
                path = (*prefix, key)
                left, right = old.get(key, _ABSENT), new.get(key, _ABSENT)
                if isinstance(left, dict) and isinstance(right, dict):
                    yield from changes(left, right, path)
                elif not _equal(left, right):
                    yield path, right
        for path, value in changes(before_effective, after_effective):
            if before.get('config_version') == 3:
                from app.runtime.config_role_edits import canonical_path
                if path[0] in ('category_folder', 'save_directories', 'output_path', 'unorganized_path'):
                    raise CentralConfigError('config_migration_required', 'Package migration cannot change directory roles')
                mapped = canonical_path(plugin_id, path)
            else:
                mapped = _mapped(plugin_id, path)
            if mapped[:2] != ('features', plugin_id) or mapped == ('features', 'caption', 'automatic'):
                raise CentralConfigError('config_migration_required', 'Package migration cannot change shared settings')
            if value is _ABSENT and _get(before, mapped) is _ABSENT:
                continue
            if _equal(_get(before, mapped), value):
                continue
            _set(after, mapped, value)
            paths.append(mapped)
        return ConfigChange(before, after, tuple(paths), (plugin_id,))

    def _write(self, document):
        temporary = self.path.with_name(f'.{self.path.name}.{uuid.uuid4().hex}.tmp')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode('utf-8')
            with temporary.open('xb') as handle:
                os.chmod(temporary, 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except (OSError, yaml.YAMLError):
            raise CentralConfigError('config_write_failed', 'cannot write main config') from None
        finally:
            temporary.unlink(missing_ok=True)

    def commit(self, change: ConfigChange) -> dict:
        with self._lock:
            current = self.read()
            conflicts = [p for p in (*change.changed_paths, *change.read_paths) if not _equal(_get(current, p), _get(change.before, p))
                         or not _parents_compatible(current, change.before, p)]
            if change.directory_reference_ids:
                from app.runtime.directory_settings import refs
                roles = ('shared','directory_roles')
                for key in change.directory_reference_ids:
                    if refs(current.get('shared',{}).get('directory_roles',{}),key) != refs(
                            change.before.get('shared',{}).get('directory_roles',{}),key):
                        conflicts.append(roles)
            if conflicts:
                raise CentralConfigError('config_conflict', 'config fields changed during operation',
                                         {'config_error_paths': _safe_paths(conflicts)})
            from app.runtime.config_semantics_migration import MigrationPlan, plan_v3_migration
            if isinstance(change, MigrationPlan):
                latest = plan_v3_migration(current, context=change.context)
                if change.blockers or latest.blockers or latest.changed_paths != change.changed_paths or any(
                    not _equal(_get(change.after, p), _get(latest.after, p)) for p in change.changed_paths
                ):
                    raise CentralConfigError('config_conflict', 'Semantic preflight no longer valid')
                if change.changed_paths and self.path.exists():
                    backup = self.path.with_name(f'{self.path.name}.pre-v3.{uuid.uuid4().hex}.bak')
                    try:
                        summary = {'source_version': change.source_version, 'target_version': change.target_version,
                                   'changed_field_count': len(change.changed_paths), 'notices': change.notices}
                        for target, data in ((backup, self.path.read_bytes()),
                                             (backup.with_suffix('.summary.json'), json.dumps(summary).encode('utf-8'))):
                            with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as handle:
                                handle.write(data)
                                handle.flush()
                                os.fsync(handle.fileno())
                    except OSError:
                        raise CentralConfigError('config_write_failed', 'Cannot back up main config') from None
                    self.last_semantic_backup = backup
            before_rebase = deepcopy(current)
            for path in change.changed_paths:
                _set(current, path, _get(change.after, path))
            if change.changed_paths:
                if current.get('config_version') == 3 and any(p[:1] == ('shared',) for p in change.changed_paths):
                    from app.runtime.config_role_edits import make_change
                    actual = make_change(before_rebase, current)
                    if not isinstance(change, MigrationPlan) and set(actual.affected_plugins) != set(change.affected_plugins):
                        raise CentralConfigError('config_conflict', 'Directory consumers changed during operation')
                self._write(_check_document(current, semantic=any(p[:2] == ('shared', 'category_folder')
                                                                 for p in change.changed_paths)))
            return deepcopy(current)

    def rollback(self, change: ConfigChange) -> dict:
        with self._lock:
            current = self.read()
            restored = False
            for path in change.changed_paths:
                if _equal(_get(current, path), _get(change.after, path)) and _parents_compatible(current, change.after, path):
                    _set(current, path, _get(change.before, path))
                    # Newly created empty parents would suppress package defaults.
                    # Keep preexisting empty maps and any concurrent sibling edits.
                    for length in range(len(path) - 1, 0, -1):
                        parent_path = path[:length]
                        if _get(change.before, parent_path) is not _ABSENT or _get(current, parent_path) != {}:
                            break
                        _set(current, parent_path, _ABSENT)
                    restored = True
            if restored:
                self._write(_check_document(current))
            return deepcopy(current)

    def migrate_legacy(self, plugin_root: Path) -> bool:
        # Local imports avoid the Store/backend dependency cycle.
        from app.runtime.plugin_store import _default_at, _schema_at, _validate, _manifest_at
        with self._lock:
            before = self.read()
            if before.get('config_version') in (2, 3):
                return False
            # Legacy imports land in v2 first. Preserve explicit public values
            # while merging old package files at their private snapshot paths.
            search = _get(before, ('features', 'search'))
            if isinstance(search, dict):
                for key in ('scoring', 'prowlarr'):
                    if key not in search:
                        continue
                    wrapped = search.setdefault('search', {})
                    if not isinstance(wrapped, dict) or key in wrapped:
                        raise CentralConfigError('legacy_config_conflict', 'Conflicting Search layouts',
                                                 {'config_error_paths': [f'features.search.{key}']})
                    wrapped[key] = search.pop(key)
            candidate = deepcopy(before)
            shared_sources = {}
            categories = {}
            category_sources = {}
            explicit_shared = before.get('shared', {})
            root = Path(plugin_root)
            for source in sorted(root.glob('*/config.yaml')):
                plugin_id = source.parent.name
                if not _KEY.fullmatch(plugin_id):
                    continue
                try:
                    legacy = yaml.safe_load(source.read_text(encoding='utf-8'))
                except (OSError, UnicodeDecodeError, yaml.YAMLError):
                    raise CentralConfigError('invalid_config', 'cannot read legacy config',
                                             {'plugin_id': plugin_id}) from None
                if not isinstance(legacy, dict):
                    raise CentralConfigError('invalid_config', 'legacy config must be a mapping', {'plugin_id': plugin_id})
                for path, value in _leaves(legacy, atomic=PRIVATE_SCORE_MAPS if plugin_id == 'search' else ()):
                    mapped = _mapped(plugin_id, path)
                    if mapped == ('shared', 'category_folder'):
                        if 'category_folder' in explicit_shared:
                            continue
                        if not isinstance(value, list):
                            raise CentralConfigError('invalid_config', 'legacy categories must be a list')
                        for entry in value:
                            if not isinstance(entry, dict) or not isinstance(entry.get('kind'), str):
                                raise CentralConfigError('invalid_config', 'legacy category must have a kind')
                            kind = entry['kind']
                            old = categories.setdefault(kind, {})
                            for field, item in entry.items():
                                if field in ('path', 'plex_library_id') and item and old.get(field) and old[field] != item:
                                    raise CentralConfigError('legacy_config_conflict', 'legacy shared categories disagree',
                                                             {'config_error_paths': ['shared.category_folder'],
                                                              'sources': [category_sources[(kind, field)], plugin_id]})
                                if item or field not in old:
                                    old[field] = deepcopy(item)
                                    category_sources[(kind, field)] = plugin_id
                        continue
                    if mapped[:1] == ('shared',):
                        if _get(before, mapped) is not _ABSENT:
                            continue
                        if not value:
                            continue
                        if mapped in shared_sources and _get(candidate, mapped) != value:
                            raise CentralConfigError('legacy_config_conflict', 'legacy shared credentials disagree',
                                                     {'config_error_paths': _safe_paths([mapped]),
                                                      'sources': [shared_sources[mapped], plugin_id]})
                        shared_sources[mapped] = plugin_id
                    elif (_get(before, mapped) is not _ABSENT
                          or any(_get(before, mapped[:length]) == {}
                                 for length in range(3, len(mapped)))):
                        continue
                    _set(candidate, mapped, value)
            if categories:
                merged = []
                for default in _V2_CATEGORIES:
                    merged.append({**default, **categories.pop(default['kind'], {})})
                merged.extend(categories.values())
                _set(candidate, ('shared', 'category_folder'), merged)
            # Validate every active installed release before touching the authority.
            for record in sorted(root.glob('*/active.json')):
                try:
                    metadata = json.loads(record.read_text(encoding='utf-8'))
                    version = str(metadata['active_version'])
                    if Path(version).name != version:
                        raise ValueError()
                    release = record.parent / 'releases' / version
                    manifest = _manifest_at(release)
                    from app.runtime.config_projection import ProjectionContract
                    schema = _schema_at(release)
                    _validate(schema, self.effective(manifest.plugin_id, _default_at(release), document=candidate,
                                                    contract=ProjectionContract(schema)))
                except Exception as exc:
                    raise CentralConfigError('invalid_config', 'legacy config cannot be migrated',
                                             {'plugin_id': record.parent.name,
                                              'config_error_paths': getattr(exc, 'details', {}).get('config_error_paths', [])}) from None
            candidate['config_version'] = 2
            self._write(_check_document(candidate))
            return True
