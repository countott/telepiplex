"""Public settings and recovery across mixed release contracts."""
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.runtime.central_config import CentralConfigError
from app.runtime.config_projection import KINDS
from app.runtime.config_semantics_migration import MigrationContext, export_v2_candidate
from tests.test_central_config import backend
from tests.test_config_semantics_migration import contracts
from tests.test_bot_runtime_startup import load_bot_module


def test_v2_old_category_draft_merges_only_changed_rows(tmp_path):
    store = backend(tmp_path, {'config_version': 2})
    source = store.effective('rename', {})
    rows = deepcopy(source['category_folder'])
    concurrent = deepcopy(rows)
    concurrent[0]['path'] = '/public'
    store.commit(store.prepare_document_patch({'shared': {'category_folder': concurrent}}))
    rows[1]['path'] = '/editor'
    store.commit(store.prepare_patch('rename', {'category_folder': rows}, baseline=source))
    assert [r['path'] for r in store.effective('rename', {})['category_folder'][:2]] == ['/public', '/editor']
    rows[0]['path'] = '/another'
    with pytest.raises(CentralConfigError, match='changed'):
        store.prepare_patch('rename', {'category_folder': rows}, baseline=source)


def test_v3_prepared_rebind_detects_later_change_of_original_entity(tmp_path):
    ctx = MigrationContext(builtin=contracts())
    store = backend(tmp_path, {'config_version': 2})
    store.commit_semantics(store.preflight(ctx))
    source = store.effective('rename', {})
    rows = deepcopy(source['category_folder'])
    rows[0]['path'] = '/editor'
    draft = store.prepare_patch('rename', {'category_folder': rows}, baseline=source)
    key = store.read()['shared']['directory_roles']['media_categories'][rows[0]['kind']]['directory_id']
    store.commit(store.prepare_document_patch({'shared': {'directories': {key: {'path': '/public'}}}}))
    with pytest.raises(CentralConfigError, match='changed'):
        store.commit(draft)
    assert store.effective('rename', {})['category_folder'][0]['path'] == '/public'
    assert ('shared', 'directories', key, 'path') not in draft.changed_paths


@pytest.mark.parametrize('wrapper,field', [
    (None, ''), ({'other': 'secret'}, ''), ({'github': None}, '.github'),
    ({'github': 'secret'}, '.github'), ({'github': {'github_tokne': 'secret'}}, '.github'),
    ({'github': {'github_token': 1}}, '.github.github_token'),
    ({'github': {'github_token': 'a\nsecret'}}, '.github.github_token'),
    ({'github': {'github_token': 'a' * 1025}}, '.github.github_token'),
    ({'github': {'cache_ttl_seconds': True}}, '.github.cache_ttl_seconds'),
    ({'github': {'cache_ttl_seconds': 59}}, '.github.cache_ttl_seconds'),
    ({'github': {'cache_ttl_seconds': 86401}}, '.github.cache_ttl_seconds'),
])
def test_invalid_public_caption_defaults_reject_manual_and_preinstall_edits(tmp_path, wrapper, field):
    store = backend(tmp_path, {'config_version': 3})
    patch = {'features': {'caption': {'provider_defaults': wrapper}}}
    target = 'features.caption.provider_defaults' + field
    with pytest.raises(CentralConfigError) as exc:
        store.prepare_document_patch(patch)
    assert exc.value.details['config_error_paths'] == [target]
    assert 'secret' not in str(exc.value)
    doc = {**store.read(), **patch}
    with pytest.raises(CentralConfigError) as exc:
        store.effective('caption', contracts()['caption'][0], document=doc)
    assert exc.value.details['config_error_paths'] == [target]
    store.path.write_text(yaml.safe_dump(doc))
    assert target in store.preflight(MigrationContext(builtin=contracts())).blockers


def test_category_menu_order_survives_yaml_and_real_inventory_readers(tmp_path, monkeypatch):
    ctx = MigrationContext(builtin=contracts())
    rows = list(reversed(ctx.builtin['rename'][0]['category_folder']))
    store = backend(tmp_path, {'config_version': 2, 'shared': {'category_folder': rows}})
    store.commit_semantics(store.preflight(ctx))
    document = yaml.safe_load(yaml.safe_dump(store.read(), sort_keys=True))
    assert document['shared']['directory_roles']['media_category_order'] == [r['kind'] for r in rows]
    assert export_v2_candidate(document, context=ctx)['shared']['category_folder'] == rows
    monkeypatch.syspath_prepend(str(Path('features/rename/src').absolute()))
    from telepiplex_rename.service import RenameFeature
    from telepiplex_rename.caption_bridge import scan_library
    feature = SimpleNamespace(config=store.effective('rename', ctx.builtin['rename'][0], document=document))
    expected = [r['path'] for r in rows]
    assert [r['path'] for r in RenameFeature._inventory_roots(feature)[:4]] == expected
    assert [r['path'] for r in asyncio.run(scan_library(feature, {}))['roots'][:4]] == expected
    with pytest.raises(CentralConfigError):
        store.prepare_document_patch({'shared': {'directory_roles': {'media_category_order': [KINDS[0]] * 4}}})


def test_main_config_bad_service_credential_preserves_management_and_download(tmp_path):
    path = tmp_path/'config.yaml'
    cfg = {'config_version': 3, 'shared': {'tmdb': {'api_key': None}},
           'plugins': {'root': str(tmp_path/'plugins'), 'runtime_root': str(tmp_path/'runtime')}}
    path.write_text(yaml.safe_dump(cfg))
    original = path.read_bytes()
    manager = load_bot_module().build_plugin_manager(cfg, tmp_path/'db', config_path=path)
    try:
        assert 'shared.tmdb.api_key' in manager.migration_status()['blockers']
        store = manager.store.central_config
        assert store.effective('download', {'access_token': 'old'})['access_token'] == 'old'
        assert store.effective('rename', {})['category_folder']
        assert path.read_bytes() == original
        store.commit(store.prepare_patch('download', {'access_token': 'new'}))
        from app.handlers.shared_config_handler import _shared
        assert _shared(manager, include_categories=False)['tmdb']['api_key'] is None
        asyncio.run(manager.configure_shared({'tmdb': {'api_key': ''}}, expected={'tmdb': {'api_key': None}}))
        assert not asyncio.run(manager.retry_semantic_migration())['blockers']
    finally:
        asyncio.run(manager.close())


def test_combined_rebind_and_label_checks_original_entity_name(tmp_path):
    store = backend(tmp_path, {'config_version': 2})
    store.commit_semantics(store.preflight(MigrationContext(builtin=contracts())))
    doc = store.read()
    kind = KINDS[0]
    binding = doc['shared']['directory_roles']['media_categories'][kind]
    key = binding['directory_id']
    del binding['label']
    doc['shared']['directories']['reuse'] = {'name': 'Reuse', 'path': '/reuse'}
    store.path.write_text(yaml.safe_dump(doc))
    source = store.effective('rename', {})
    rows = deepcopy(source['category_folder'])
    rows[0].update(path='/reuse', name='Draft label')
    draft = store.prepare_patch('rename', {'category_folder': rows}, baseline=source)
    store.commit(store.prepare_document_patch({'shared': {'directories': {key: {'name': 'Public rename'}}}}))
    with pytest.raises(CentralConfigError, match='changed'):
        store.commit(draft)
    assert store.effective('rename', {})['category_folder'][0]['name'] == 'Public rename'
    assert ('shared', 'directories', key, 'name') in draft.read_paths
    assert ('shared', 'directories', key, 'name') not in draft.changed_paths
    assert ('shared', 'directories', 'reuse', 'path') in draft.read_paths


@pytest.mark.parametrize('editor', ['rename', 'host'])
def test_first_category_edit_on_order_only_document_preserves_defaults_and_existing_entities(tmp_path, editor):
    default = contracts()['rename'][0]['category_folder'][0]
    order = list(reversed(KINDS))
    original = {'config_version': 3, 'shared': {
        'directory_roles': {'media_category_order': order},
        'directories': {'existing': {'name': 'Entity title', 'path': default['path']}}}}
    store = backend(tmp_path, original)
    source = store.effective('rename', {})
    rows = deepcopy(source['category_folder'])
    rows[0]['path'] = '/edited'
    if editor == 'host':
        draft = store.prepare_document_patch({'shared': {'category_folder': rows}})
    else:
        draft = store.prepare_patch('rename', {'category_folder': rows}, baseline=source)
    # A concurrent edit of an already existing, reused destination must conflict.
    store.commit(store.prepare_document_patch({'shared': {'directories': {'existing': {'path': '/moved'}}}}))
    with pytest.raises(CentralConfigError):
        store.commit(draft)
    store.path.write_text(yaml.safe_dump(original))
    store.commit(draft)
    assert store.effective('rename', {})['category_folder'] == rows
    shared = store.read()['shared']
    assert shared['directory_roles']['media_category_order'] == order
    assert shared['directory_roles']['media_categories'][default['kind']]['directory_id'] == 'existing'
    assert shared['directories']['existing'] == original['shared']['directories']['existing']
