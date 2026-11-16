from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from tests.test_central_config import backend


def contracts():
    return {key: (yaml.safe_load(Path(f'features/{key}/config.default.yaml').read_text()),
                  json.loads(Path(f'features/{key}/config.schema.json').read_text()))
            for key in ('search', 'rename', 'sync', 'caption', 'download')}


def test_migration_preserves_role_labels_empty_maps_effective_timeout_and_latest_token(tmp_path):
    from app.runtime.config_semantics_migration import MigrationContext
    from app.runtime.config_projection import ProjectionContract
    from app.runtime.plugin_store import _validate
    packages = contracts()
    store = backend(tmp_path, {'config_version': 2, 'features': {
        'download': {'save_directories': [{'name': 'menu', 'path': '真人电影/'}], 'access_token': 'first'},
        'rename': {'caption_enabled': False, 'unorganized_path': '/'},
        'caption': {'output_path': '/', 'providers': {'local_archive': {'roots': ['one', '~/two']}}},
        'search': {'metadata': {'tmdb': {'timeout': 300}}, 'search': {'scoring': {'keyword_scores': {}}}}
    }})
    plan = store.preflight(MigrationContext(builtin=packages, caption_cwd='/release', caption_home='/home'))
    assert not plan.blockers
    store.commit(store.prepare_patch('download', {'access_token': 'refreshed'}))
    original = store.path.read_bytes()
    store.commit_semantics(plan)
    doc = store.read()
    roles = doc['shared']['directory_roles']
    assert roles['manual_downloads'][0]['directory_id'] == roles['media_categories']['live_action_movie']['directory_id']
    assert roles['manual_downloads'][0]['label'] == 'menu'
    assert roles['unorganized_inventory'] is None
    assert 'search' not in doc['features']['search']
    # All snapshot shapes remain valid against the old schemas. Only a Rename
    # declaring the fixed root behavior may activate the null-inventory fixture.
    snapshots = {key: _validate(schema, store.effective(key, defaults, contract=ProjectionContract(
                    {**schema, 'x-telepiplex-inventory-root-contract': 'null_disables_extra_root_v1'} if key == 'rename' else schema)))
                 for key, (defaults, schema) in packages.items()}
    assert snapshots['download']['access_token'] == 'refreshed'
    assert snapshots['download']['save_directories'] == [{'name': 'menu', 'path': '真人电影'}]
    assert snapshots['search']['metadata']['tmdb']['timeout'] == 60
    assert snapshots['search']['search']['scoring']['keyword_scores'] == {}
    assert snapshots['caption']['providers']['local_archive']['roots'] == ['/release/one', '/home/two']
    assert snapshots['caption']['output_path'] == snapshots['rename']['unorganized_path'] == '/'
    assert snapshots['caption']['automatic'] is snapshots['rename']['caption_enabled'] is False
    assert store.last_semantic_backup.read_bytes() == original
    assert store.last_semantic_backup.stat().st_mode & 0o777 == 0o600
    summary = store.last_semantic_backup.with_suffix('.summary.json')
    assert summary.stat().st_mode & 0o777 == 0o600
    assert 'refreshed' not in summary.read_text()
    assert json.loads(summary.read_text())['target_version'] == 3
    assert not store.preflight(MigrationContext()).changed_paths


@pytest.mark.parametrize('feature,patch', [
    ('download', {'save_directories': [{'name': 'foo', 'path': 'a//b'}]}),
    ('caption', {'providers': {'local_archive': {'roots': ['relative']}}}),
    ('sync', {'tmdb': {'api_key': 'PRIVATE-SECRET'}}),
])
def test_blocker_preserves_bytes_and_unaffected_projection(tmp_path, feature, patch):
    from app.runtime.config_semantics_migration import MigrationContext
    from app.runtime.config_projection import ProjectionContract
    store = backend(tmp_path, {'config_version': 2, 'features': {feature: patch, 'search': {'metadata': {'tmdb': {'timeout': 300}}}}})
    original = store.path.read_bytes()
    plan = store.preflight(MigrationContext(builtin=contracts()))
    assert plan.blockers and 'PRIVATE-SECRET' not in repr(plan.blockers)
    assert store.path.read_bytes() == original
    assert store.effective('search', {}, contract=ProjectionContract({}))['metadata']['tmdb']['timeout'] == 60


def test_sparse_migration_never_owns_download_block_and_rebases_first_token(tmp_path):
    from app.runtime.config_semantics_migration import MigrationContext
    store = backend(tmp_path, {'config_version': 2})
    plan = store.preflight(MigrationContext(builtin=contracts()))
    assert ('features', 'download') not in plan.changed_paths
    store.commit(store.prepare_patch('download', {'access_token': 'new'}))
    store.commit_semantics(plan)
    assert store.read()['features']['download']['access_token'] == 'new'


def test_github_defaults_inherit_before_release_defaults_except_explicit_anonymous(tmp_path):
    from app.runtime.config_semantics_migration import MigrationContext
    from app.runtime.config_projection import ProjectionContract
    packages = contracts()
    raw = {'config_version': 2, 'features': {'caption': {'providers': {'mingy': {'github_token': ''}}}}}
    store = backend(tmp_path, raw)
    plan = store.preflight(MigrationContext(builtin=packages))
    store.commit_semantics(plan)
    store.commit(store.prepare_document_patch({'features': {'caption': {'provider_defaults': {'github': {
        'github_token': 'new', 'cache_ttl_seconds': 987}}}}}))
    defaults, schema = packages['caption']
    snapshot = store.effective('caption', defaults, contract=ProjectionContract(schema))
    assert 'provider_defaults' not in snapshot
    assert [snapshot['providers'][k]['github_token'] for k in ('nekomoe', 'mingy', 'kitauji', 'haruhana')] == ['new', '', 'new', 'new']
    assert snapshot['providers']['local_archive']['cache_ttl_seconds'] == 300


def test_equal_private_token_only_keeps_all_other_package_defaults(tmp_path):
    from app.runtime.config_semantics_migration import MigrationContext
    store = backend(tmp_path, {'config_version': 2, 'shared': {'tmdb': {'api_key': 'shared'}},
                              'features': {'search': {'metadata': {'tmdb': {'api_key': 'shared'}}}}})
    plan = store.preflight(MigrationContext(builtin=contracts()))
    assert not plan.blockers
    assert 'api_key' not in plan.after['features'].get('search', {}).get('metadata', {}).get('tmdb', {})


def test_stable_ids_keep_unicode_distinct_and_labels_ordered(tmp_path):
    from app.runtime.config_semantics_migration import MigrationContext
    context = MigrationContext(builtin=contracts())
    store = backend(tmp_path, {'config_version': 2, 'features': {'download': {'save_directories': [
        {'name': 'accent', 'path': 'Café'}, {'name': 'combining', 'path': 'Cafe\u0301'}]},
        'rename': {'unorganized_path': '/Café'}, 'caption': {'output_path': '/Cafe\u0301'}}})
    plan = store.preflight(context)
    assert plan.after == store.preflight(context).after
    roles = plan.after['shared']['directory_roles']
    first, second = roles['manual_downloads']
    assert first['directory_id'] != second['directory_id']
    assert first['directory_id'] == roles['unorganized_inventory']['directory_id']
    assert second['directory_id'] == roles['standalone_subtitles']['directory_id']
    store.commit_semantics(plan)
    assert store.preflight(context).after == store.read()


def test_failed_backup_refuses_main_write(tmp_path, monkeypatch):
    import os
    from app.runtime.central_config import CentralConfigError
    from app.runtime.config_semantics_migration import MigrationContext
    store = backend(tmp_path, {'config_version': 2})
    original = store.path.read_bytes()
    plan = store.preflight(MigrationContext(builtin=contracts()))
    real_open = os.open
    def deny_backup(path, *args, **kwargs):
        if str(path).endswith('.bak'):
            raise PermissionError()
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', deny_backup)
    with pytest.raises(CentralConfigError):
        store.commit_semantics(plan)
    assert store.path.read_bytes() == original


def test_relative_caption_archive_migration_preserves_tsv_root_index(tmp_path, monkeypatch):
    from app.runtime.config_semantics_migration import MigrationContext
    packages = contracts()
    monkeypatch.syspath_prepend(str(Path('features/caption/src').absolute()))
    from telepiplex_caption.catalog_providers import LocalArchiveProvider
    cwd = tmp_path/'old-release'
    for name in ('one', 'two'):
        (cwd/name).mkdir(parents=True)
    (cwd/'map.tsv').write_text('root_index\trelative_path\ttitle\n1\topaque.srt\tSecond\n')
    raw = {'roots': ['one', 'two'], 'mapping_file': 'map.tsv'}
    store = backend(tmp_path, {'config_version': 2, 'features': {'caption': {'providers': {'local_archive': raw}}}})
    monkeypatch.chdir(cwd)
    previous = LocalArchiveProvider(raw)
    assert previous._rows()[(1, 'opaque.srt')]['title'] == 'Second'
    original_roots = previous._roots()
    store.commit_semantics(store.preflight(MigrationContext(builtin=packages, caption_cwd=str(cwd))))
    monkeypatch.chdir(tmp_path)
    new = LocalArchiveProvider(store.effective('caption', packages['caption'][0])['providers']['local_archive'])
    assert original_roots == new._roots() == [cwd/'one', cwd/'two']
    assert new._rows()[(1, 'opaque.srt')]['title'] == 'Second'



