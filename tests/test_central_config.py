import stat
from pathlib import Path

import pytest
import yaml


def backend(tmp_path, document):
    from app.runtime.central_config import CentralConfigStore
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(document), encoding='utf-8')
    return CentralConfigStore(path)


def test_sparse_projection_preserves_empty_scores_false_zero_and_shared_authority(tmp_path):
    store = backend(tmp_path, {'shared': {'tmdb': {'api_key': 'shared'}}, 'features': {
        'search': {'search': {'enable': False, 'scoring': {'keyword_scores': {}}},
                   'metadata': {'tmdb': {'api_key': 'private', 'timeout': 0}}}}})
    defaults = yaml.safe_load(Path('features/search/config.default.yaml').read_text())
    value = store.effective('search', defaults)
    assert value['search']['enable'] is False
    assert value['search']['scoring']['keyword_scores'] == {}
    assert value['metadata']['tmdb']['timeout'] == 0
    assert value['metadata']['tmdb']['api_key'] == 'shared'
    assert len(store.effective('sync', {'category_folder': []})['category_folder']) == 4


def test_path_commit_and_conditional_rollback_preserve_concurrent_tokens(tmp_path):
    store = backend(tmp_path, {'features': {'download': {'token': 'old', 'size': 2}}, 'other': 9})
    size = store.prepare_patch('download', {'size': 0})
    token = store.prepare_patch('download', {'token': 'new'})
    store.commit(token)
    store.commit(size)
    assert store.read()['features']['download'] == {'token': 'new', 'size': 0}
    store.commit(store.prepare_patch('download', {'size': 4}))
    store.rollback(size)
    assert store.read()['features']['download'] == {'token': 'new', 'size': 4}
    assert store.read()['other'] == 9
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_shared_patch_maps_legacy_shape_and_conflicts_do_not_leak(tmp_path):
    from app.runtime.central_config import CentralConfigError
    store = backend(tmp_path, {'shared': {'tmdb': {'api_key': 'old-secret'}}})
    change = store.prepare_patch('search', {'metadata': {'tmdb': {'api_key': 'new-secret', 'timeout': 0}}})
    assert change.after['shared']['tmdb']['api_key'] == 'new-secret'
    assert change.after['features']['search'] == {'metadata': {'tmdb': {'timeout': 0}}}
    assert set(change.affected_plugins) == {'search', 'sync'}
    store.commit(store.prepare_patch('sync', {'tmdb': {'api_key': 'latest-secret'}}))
    with pytest.raises(CentralConfigError) as caught:
        store.commit(change)
    assert caught.value.code == 'config_conflict'
    assert 'secret' not in str(caught.value) + str(caught.value.details)


def test_legacy_migration_conflict_is_atomic_and_secret_free(tmp_path):
    from app.runtime.central_config import CentralConfigError
    store = backend(tmp_path, {'bot_token': 'bot'})
    root = tmp_path / 'plugins'
    for plugin, value in [('search', {'metadata': {'tmdb': {'api_key': 'secret-one'}}}),
                          ('sync', {'tmdb': {'api_key': 'secret-two'}})]:
        target = root / plugin / 'config.yaml'
        target.parent.mkdir(parents=True)
        target.write_text(yaml.safe_dump(value))
    original = store.path.read_bytes()
    with pytest.raises(CentralConfigError) as caught:
        store.migrate_legacy(root)
    assert store.path.read_bytes() == original
    assert 'secret-' not in str(caught.value) + str(caught.value.details)


def test_legacy_migration_new_explicit_values_win_and_originals_remain(tmp_path):
    store = backend(tmp_path, {'features': {'download': {'size': 0}}, 'unrelated': {'x': 1}})
    target = tmp_path / 'plugins' / 'download' / 'config.yaml'
    target.parent.mkdir(parents=True)
    target.write_text('size: 12\ntoken: saved\n')
    original = target.read_bytes()
    assert store.migrate_legacy(target.parent.parent) is True
    assert store.read()['features']['download'] == {'size': 0, 'token': 'saved'}
    assert store.read()['config_version'] == 2
    assert target.read_bytes() == original
    assert store.migrate_legacy(target.parent.parent) is False


def test_migration_removes_owned_old_fields_preserving_other_feature_updates(tmp_path):
    store = backend(tmp_path, {'features': {'rename': {'metadata': {'tvdb': 'old'}, 'ai': {'token': 'keep'}}}})
    change = store.prepare_migration('rename', {'metadata': {'tvdb': 'old'}, 'ai': {'token': 'keep'}},
                                     {'ai': {'token': 'keep'}})
    store.commit(store.prepare_patch('rename', {'ai': {'token': 'new'}}))
    store.commit(change)
    assert store.read()['features']['rename'] == {'ai': {'token': 'new'}}
    store.rollback(change)
    assert store.read()['features']['rename']['metadata'] == {'tvdb': 'old'}
    assert store.read()['features']['rename']['ai']['token'] == 'new'


def test_real_five_feature_legacy_defaults_migrate_to_valid_projection(tmp_path):
    from app.runtime.plugin_store import _validate
    import json
    store = backend(tmp_path, {'features': {'future': {'keep': True}}})
    root = tmp_path / 'plugins'
    for plugin in ('download', 'search', 'rename', 'sync', 'caption'):
        source = Path('features') / plugin
        target = root / plugin / 'config.yaml'
        target.parent.mkdir(parents=True)
        target.write_text((source / 'config.default.yaml').read_text())
    assert store.migrate_legacy(root)
    for plugin in ('download', 'search', 'rename', 'sync', 'caption'):
        source = Path('features') / plugin
        defaults = yaml.safe_load((source / 'config.default.yaml').read_text())
        schema = json.loads((source / 'config.schema.json').read_text())
        _validate(schema, store.effective(plugin, defaults))
    assert store.read()['features']['future'] == {'keep': True}


def test_missing_file_can_bootstrap_explicit_initial_without_early_write(tmp_path):
    from app.runtime.central_config import CentralConfigStore
    path = tmp_path / 'config.yaml'
    store = CentralConfigStore(path, initial={'bot_token': 'initial'})
    assert store.read() == {'bot_token': 'initial'}
    assert not path.exists()
    assert store.migrate_legacy(tmp_path / 'plugins')
    assert store.read() == {'bot_token': 'initial', 'config_version': 2}


@pytest.mark.parametrize('text', ['token: [secret-broken', 'features: secret-invalid', 'shared:\n  tmdb: secret-invalid'])
def test_invalid_main_config_reports_no_values(tmp_path, text):
    from app.runtime.central_config import CentralConfigStore, CentralConfigError
    path = tmp_path / 'config.yaml'
    path.write_text(text)
    with pytest.raises(CentralConfigError) as caught:
        CentralConfigStore(path).read()
    assert caught.value.code == 'invalid_config'
    assert 'secret-' not in str(caught.value) + str(caught.value.details)


def test_store_candidate_migration_preserves_explicit_empty_scoring(tmp_path):
    from types import SimpleNamespace
    from app.runtime.plugin_store import PluginStore
    central = backend(tmp_path, {'features': {'search': {'search': {'scoring': {'keyword_scores': {}}}}}})
    store = PluginStore(tmp_path / 'plugins', central_config=central)
    release = SimpleNamespace(plugin_id='search', path=Path('features/search'),
                              manifest=SimpleNamespace(config_schema_version=1))
    before = central.path.read_bytes()
    _, candidate, _, _ = store.migrate_config(release)
    assert candidate['search']['scoring']['keyword_scores'] == {}
    assert central.path.read_bytes() == before


def test_category_migration_merges_by_kind_but_rejects_nonempty_path_conflict(tmp_path):
    from app.runtime.central_config import CentralConfigError
    central = backend(tmp_path, {})
    root = tmp_path / 'plugins'
    for plugin, entry in [('search', {'kind': 'animated_movie', 'path': '/movies'}),
                          ('rename', {'kind': 'animated_movie', 'plex_library_id': '123'})]:
        path = root / plugin / 'config.yaml'
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({'category_folder': [entry]}))
    assert central.migrate_legacy(root)
    routes = central.read()['shared']['category_folder']
    movie = next(item for item in routes if item['kind'] == 'animated_movie')
    assert movie['path'] == '/movies'
    assert movie['plex_library_id'] == '123'
    central.path.write_text('{}')
    (root / 'rename/config.yaml').write_text(yaml.safe_dump({'category_folder': [
        {'kind': 'animated_movie', 'path': '/different'}]}))
    with pytest.raises(CentralConfigError) as caught:
        central.migrate_legacy(root)
    assert caught.value.code == 'legacy_config_conflict'
    assert set(caught.value.details['sources']) == {'search', 'rename'}
    assert central.read() == {}


@pytest.mark.parametrize('document', [
    {'config_version': 3}, {'config_version': True},
    {'shared': {'tmdb': {'api_key': 123}}},
    {'shared': {'category_folder': []}},
    {'shared': {'category_folder': [{'kind': 'unknown', 'path': '/a'}]}},
    {'shared': {'category_folder': [{'kind': 'animated_movie', 'path': ''}]}},
])
def test_invalid_shared_fields_rejected_without_any_installed_consumers(tmp_path, document):
    from app.runtime.central_config import CentralConfigError
    central = backend(tmp_path, {})
    with pytest.raises(CentralConfigError):
        central.prepare_document_patch(document)
    assert central.read() == {}


def test_boolean_zero_change_is_not_lost_to_python_value_equality(tmp_path):
    central = backend(tmp_path, {'features': {'custom': {'value': False}}})
    change = central.prepare_patch('custom', {'value': 0})
    central.commit(change)
    assert type(central.read()['features']['custom']['value']) is int


def test_leaf_patch_rejects_concurrent_scalar_parent_replacement(tmp_path):
    from app.runtime.central_config import CentralConfigError
    central = backend(tmp_path, {'features': {'custom': {}}})
    pending = central.prepare_patch('custom', {'nested': {'value': 1}})
    central.commit(central.prepare_patch('custom', {'nested': 'new-owner'}))
    with pytest.raises(CentralConfigError):
        central.commit(pending)
    assert central.read()['features']['custom']['nested'] == 'new-owner'


def test_explicit_empty_scoring_map_wins_over_nonempty_legacy_map(tmp_path):
    central = backend(tmp_path, {'features': {'search': {'search': {'scoring': {'keyword_scores': {}}}}}})
    target = tmp_path / 'plugins' / 'search' / 'config.yaml'
    target.parent.mkdir(parents=True)
    target.write_text('search:\n  scoring:\n    keyword_scores:\n      WEB: 12\n')
    central.migrate_legacy(target.parent.parent)
    assert central.read()['features']['search']['search']['scoring']['keyword_scores'] == {}


def test_custom_scoring_table_does_not_reintroduce_default_keywords(tmp_path):
    central = backend(tmp_path, {'features': {'search': {'search': {'scoring': {'keyword_scores': {'custom': 2}}}}}})
    defaults = yaml.safe_load(Path('features/search/config.default.yaml').read_text())
    assert central.effective('search', defaults)['search']['scoring']['keyword_scores'] == {'custom': 2}


def test_rollback_prunes_created_ancestors_restoring_real_search_defaults(tmp_path):
    central = backend(tmp_path, {'features': {'search': {}}})
    defaults = yaml.safe_load(Path('features/search/config.default.yaml').read_text())
    change = central.prepare_patch('search', {'metadata': {'tmdb': {'timeout': 5}}})
    central.commit(change)
    central.rollback(change)
    assert central.read() == {'features': {'search': {}}}
    effective = central.effective('search', defaults)
    assert effective['metadata']['tmdb']['enable'] is True
    assert effective['metadata']['tmdb']['timeout'] == 15


def test_rollback_pruning_preserves_concurrent_sibling_settings(tmp_path):
    central = backend(tmp_path, {'features': {'search': {}}})
    change = central.prepare_patch('search', {'metadata': {'tmdb': {'timeout': 5}}})
    central.commit(change)
    central.commit(central.prepare_patch('search', {'metadata': {'tmdb': {'enable': False}}}))
    central.rollback(change)
    assert central.read()['features']['search'] == {'metadata': {'tmdb': {'enable': False}}}


def test_rollback_pruning_keeps_original_explicit_empty_map(tmp_path):
    central = backend(tmp_path, {'features': {'search': {'metadata': {'tmdb': {}}}}})
    change = central.prepare_patch('search', {'metadata': {'tmdb': {'timeout': 5}}})
    central.commit(change)
    central.rollback(change)
    assert central.read()['features']['search'] == {'metadata': {'tmdb': {}}}


@pytest.mark.parametrize('table', ['keyword_scores', 'indexer_scores'])
def test_legacy_migration_preserves_whole_explicit_nonempty_scoring_table(tmp_path, table):
    central = backend(tmp_path, {'features': {'search': {'search': {'scoring': {table: {'custom': 2}}}}}})
    target = tmp_path / 'plugins' / 'search' / 'config.yaml'
    target.parent.mkdir(parents=True)
    target.write_text(yaml.safe_dump({'search': {'scoring': {table: {'WEB': 12}},
                                                'prowlarr': {'timeout': 50}}}))
    central.migrate_legacy(target.parent.parent)
    config = central.read()['features']['search']['search']
    assert config['scoring'][table] == {'custom': 2}
    assert config['prowlarr']['timeout'] == 50
