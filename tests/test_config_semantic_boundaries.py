"""Regression checks at the release, graph, and downgrade boundaries."""
import asyncio
import json
from pathlib import Path

import pytest
import yaml

from app.runtime.central_config import CentralConfigError
from app.runtime.config_projection import GITHUB, ProjectionContract
from app.runtime.config_semantics_migration import MigrationContext, export_v2_candidate
from app.runtime.directory_settings import directory_rows, prepare_directory
from app.runtime.plugin_manager import PluginOperationError
from tests.mixed_config_releases import release
from tests.test_central_config import backend
from tests.test_config_migration_bootstrap import _start_with_frozen_releases
from tests.test_config_semantics_migration import contracts


def old_context():
    rows = json.loads(Path(__file__).with_name('fixtures').joinpath('config_contracts_host_3_10_1.json').read_text())
    return MigrationContext(installed={k: (v['defaults'], v['schema']) for k, v in rows.items()})


def test_retained_caption_refuses_relative_archive_switch_and_rollback(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path('features/caption/src').absolute()))
    from telepiplex_caption.catalog_providers import LocalArchiveProvider
    path = tmp_path/'config.yaml'
    archive = {'roots': ['one', 'two'], 'mapping_file': 'map.tsv'}
    cfg = {'config_version': 2, 'features': {'sync': {'tmdb': {'api_key': 'conflict'}},
           'caption': {'providers': {'local_archive': archive}}}}
    async def exercise():
        manager = await _start_with_frozen_releases(tmp_path, cfg, ('download','search','rename','sync','caption'))
        try:
            await manager.restore_active()
            old = manager.store.active('caption')
            monkeypatch.chdir(old.path)
            for name in archive['roots']:
                (old.path/name).mkdir()
            (old.path/'map.tsv').write_text('root_index\trelative_path\ttitle\n1\topaque.srt\tSecond\n')
            def read_archive():
                local = manager.supervisor.process('caption').config['providers']['local_archive']
                provider = LocalArchiveProvider(local)
                assert provider._roots() == [old.path/'one', old.path/'two']
                assert provider._rows()[(1,'opaque.srt')]['title'] == 'Second'
            read_archive()
            artifact = release(tmp_path, 'caption')
            original = path.read_bytes()
            process = manager.supervisor.process('caption')
            with pytest.raises(PluginOperationError) as exc:
                await manager.update(artifact)
            assert 'features.caption.providers.local_archive.roots' in exc.value.details['config_error_paths']
            assert path.read_bytes() == original and manager.supervisor.process('caption') is process
            assert manager.store.active('caption').version == old.version
            assert manager.preview_semantic_migration()['blockers']
            read_archive()
            doc = yaml.safe_load(original)
            local = doc['features']['caption']['providers']['local_archive']
            local.update(roots=[str(old.path/x) for x in archive['roots']], mapping_file=str(old.path/'map.tsv'))
            path.write_text(yaml.safe_dump(doc))
            await manager.update(artifact)
            monkeypatch.chdir(manager.store.active('caption').path)
            read_archive()
            local['mapping_file'] = '~/relative-map.tsv'
            path.write_text(yaml.safe_dump(doc))
            with pytest.raises(PluginOperationError) as exc:
                await manager.rollback('caption')
            assert exc.value.details['config_error_paths'] == ['features.caption.providers.local_archive.mapping_file']
            local['mapping_file'] = str(old.path/'map.tsv')
            path.write_text(yaml.safe_dump(doc))
            await manager.rollback('caption')
            read_archive()
            assert manager.store.central_config.read() == doc
        finally:
            await manager.close()
    asyncio.run(exercise())


def test_v3_caption_edit_keeps_relative_archive_in_same_release_until_repaired(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path('features/caption/src').absolute()))
    from telepiplex_caption.catalog_providers import LocalArchiveProvider

    async def exercise():
        manager = await _start_with_frozen_releases(tmp_path, {'config_version': 2}, tuple(old_context().installed))
        try:
            assert manager.store.central_config.read()['config_version'] == 3
            await manager.restore_active()
            old = manager.store.active('caption')
            monkeypatch.chdir(old.path)
            archive = old.path/'archive'
            archive.mkdir()
            mapping = old.path/'map.tsv'
            mapping.write_text('root_index\trelative_path\ttitle\n0\tlocal.srt\tOriginal\n')
            async def set_archive(root, index):
                await manager.configure_patch('caption', {'providers': {'local_archive': {
                    'roots': [root], 'mapping_file': index}}})
            def check_archive():
                process = manager.supervisor.process('caption')
                provider = LocalArchiveProvider(process.config['providers']['local_archive'])
                assert provider._roots() == [archive]
                assert provider._rows()[(0, 'local.srt')]['title'] == 'Original'
            await set_archive('archive', 'map.tsv')
            check_archive()
            original = manager.store.central_config.path.read_bytes()
            prior = manager.supervisor.process('caption')
            artifact = release(tmp_path, 'caption')
            with pytest.raises(PluginOperationError) as exc:
                await manager.update(artifact)
            assert set(exc.value.details['config_error_paths']) == {
                'features.caption.providers.local_archive.roots', 'features.caption.providers.local_archive.mapping_file'}
            assert manager.store.central_config.path.read_bytes() == original
            assert manager.supervisor.process('caption') is prior
            assert manager.store.active('caption').version == old.version
            await set_archive(str(archive), str(mapping))
            await manager.update(artifact)
            monkeypatch.chdir(manager.store.active('caption').path)
            check_archive()
        finally:
            await manager.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('shared', [None, {}, {'directories': {}}, {'directory_roles': {}},
                                    {'directories': {'spare': {'name':'Spare','path':'/spare'}}}])
def test_sparse_old_package_saves_only_edited_roles(tmp_path, shared):
    async def exercise():
        config = {'config_version': 3}
        if shared is not None:
            config['shared'] = shared
        manager = await _start_with_frozen_releases(tmp_path, config,
                                                    ('download','search','rename','caption'))
        try:
            await manager.restore_active()
            roles = set()
            for name, field, role, value in (
                ('download','save_directories','manual_downloads',[{'name':'Q','path':'queue'}]),
                ('rename','unorganized_path','unorganized_inventory','/incoming'),
                ('caption','output_path','standalone_subtitles','/captions')):
                await manager.configure_patch(name, {field: value})
                roles.add(role)
                doc = manager.store.central_config.read()
                assert doc['shared']['directory_roles'].keys() == roles
                assert len(doc['shared']['directories']) == len(roles) + len((shared or {}).get('directories', {}))
                assert manager.supervisor.process(name).config[field] == value
        finally:
            await manager.close()
    asyncio.run(exercise())


def test_graph_editor_commit_and_preflight_reject_normalized_collisions(tmp_path):
    store = backend(tmp_path, {'config_version': 3, 'shared': {'directories': {'one': {'path':'/a','name':'A'}}}})
    row = next(r for r in directory_rows(store.read()) if r.field == 'new')
    with pytest.raises(CentralConfigError):
        prepare_directory(store.read(), row, {'id':'two','path':'/a/','name':'B'})
    pending = store.prepare_document_patch({'shared': {'directories': {'two': {'path':'/b','name':'B'}}}})
    store.commit(store.prepare_document_patch({'shared': {'directories': {'one': {'path':'/b/'}}}}))
    with pytest.raises(CentralConfigError):
        store.commit(pending)
    assert store.read()['shared']['directories'].keys() == {'one'}


def test_independent_directory_creations_merge_through_public_editor(tmp_path):
    store = backend(tmp_path, {'config_version':3})
    draft = next(r for r in directory_rows(store.read()) if r.field == 'new')
    changes = [prepare_directory(store.read(), draft, {'id':key,'name':key,'path':'/'+key})
               for key in ('one','two')]
    for change in changes:
        store.commit(change)
    assert set(store.read()['shared']['directories']) == {'one','two'}


def test_sparse_sentinel_patch_does_not_create_an_entity_map(tmp_path):
    store = backend(tmp_path, {'config_version':3})
    base = store.effective('rename', old_context().installed['rename'][0])
    store.commit(store.prepare_patch('rename', {'unorganized_path':'/'}, baseline=base))
    assert store.read()['shared'] == {'directory_roles':{'unorganized_inventory':None}}


@pytest.mark.parametrize('shared,field', [
    ({'category_folder': []}, 'shared.category_folder'),
    ({'directories': {'bad': {'path':'/../bad','name':'bad'}}}, 'shared.directories.bad.path'),
    ({'directories': {'a': {'path':'/same','name':'a'}, 'b': {'path':'/same/','name':'b'}}}, 'shared.directories.b.path'),
])
def test_bad_hand_graph_has_specific_diagnosis_and_unrelated_recovery(tmp_path,shared,field):
    store = backend(tmp_path, {'config_version':3,'shared':shared})
    assert field in store.preflight(old_context()).blockers
    store.commit(store.prepare_patch('download', {'access_token':'latest'}))
    assert store.read()['features']['download']['access_token'] == 'latest'


def test_only_consuming_role_rejects_ambiguous_hand_edited_entity(tmp_path):
    shared = {'directories': {'one':{'name':'One','path':'/q'}, 'two':{'name':'Two','path':'/q/'}},
              'directory_roles': {'manual_downloads':[{'directory_id':'one'}]}}
    store = backend(tmp_path, {'config_version':3,'shared':shared})
    defaults, schema = old_context().installed['download']
    with pytest.raises(CentralConfigError) as exc:
        store.effective('download',defaults,contract=ProjectionContract(schema))
    assert exc.value.details['config_error_paths'] == ['shared.directories.one.path']
    assert store.effective('caption',old_context().installed['caption'][0])['output_path'] == '/字幕'


def test_export_preserves_anonymous_vs_inherited_and_refuses_unbound_resource(tmp_path):
    ctx = old_context()
    store = backend(tmp_path, {'config_version':2,'features':{'caption':{'providers':{'mingy':{'github_token':''}}}}})
    store.commit_semantics(store.preflight(ctx))
    candidate = export_v2_candidate(store.read(), context=ctx)
    assert 'download' not in candidate['features'] or 'base_url' not in candidate['features']['download']
    rec = backend(tmp_path, candidate)
    rec.commit_semantics(rec.preflight(ctx))
    rec.commit(rec.prepare_document_patch({'features': {'caption':{'provider_defaults':{'github':{'github_token':'later'}}}}}))
    providers = rec.effective('caption',ctx.installed['caption'][0])['providers']
    assert [providers[p]['github_token'] for p in GITHUB] == ['later','','later','later']
    rec.commit(rec.prepare_document_patch({'shared':{'directories':{'unused':{'name':'spare','path':'/spare'}}}}))
    with pytest.raises(CentralConfigError) as exc:
        export_v2_candidate(rec.read(),context=ctx)
    assert any(p.startswith('shared.directories.unused') for p in exc.value.details['config_error_paths'])


def test_export_refuses_reset_automatic_but_preserves_explicit_caption_policy(tmp_path):
    from app.runtime.config_settings import prepare_setting, setting_rows
    from app.runtime.config_semantics_migration import plan_v3_migration

    async def exercise():
        context = old_context()
        manager = await _start_with_frozen_releases(tmp_path, {'config_version': 2}, tuple(context.installed))
        store = manager.store.central_config
        def automatic():
            return next(row for row in setting_rows(manager, 'caption_policy') if row.field == 'automatic')
        try:
            for value in (False, True):
                store.commit(prepare_setting(manager, automatic(), value, reset=False))
                candidate = export_v2_candidate(store.read(), context=context)
                imported = plan_v3_migration(candidate, context=context)
                assert not imported.blockers
                assert imported.after['features']['caption']['automatic'] is value
            store.commit(prepare_setting(manager, automatic(), None, reset=True))
            assert automatic().status == '默认'
            original = store.path.read_bytes()
            with pytest.raises(CentralConfigError) as exc:
                export_v2_candidate(store.read(), context=context)
            assert exc.value.details['config_error_paths'] == ['features.caption.automatic']
            assert store.path.read_bytes() == original
        finally:
            await manager.close()
    asyncio.run(exercise())


def test_retained_old_download_ambiguous_only_at_runtime_admission(tmp_path):
    raw = {'save_directories':[{'name':'Q','path':'after /'}]}
    store = backend(tmp_path, {'config_version':2,'features':{'download':raw}})
    with pytest.raises(CentralConfigError):
        store.effective('download', {}, contract=ProjectionContract({'type':'object'}))
    for contract in (ProjectionContract({}, enforce_runtime=False, migration_source=True),
                     ProjectionContract(contracts()['download'][1])):
        assert store.effective('download', {}, contract=contract)['save_directories'][0]['path'] == 'after '


@pytest.mark.parametrize('maximum,value,valid', [(100000,90000,True),(100,101,False)])
def test_selected_caption_bounds_apply_even_when_all_providers_override(tmp_path,maximum,value,valid):
    defaults,schema = contracts()['caption']
    schema['$defs']['githubProvider']['properties']['cache_ttl_seconds']['maximum'] = maximum
    raw = {'provider_defaults':{'github':{'cache_ttl_seconds':value}},
           'providers':{p:{'cache_ttl_seconds':60} for p in GITHUB}}
    store = backend(tmp_path, {'config_version':3,'features':{'caption':raw}})
    if valid:
        assert store.effective('caption',defaults,contract=ProjectionContract(schema))['providers']['mingy']['cache_ttl_seconds'] == 60
    else:
        with pytest.raises(CentralConfigError) as exc:
            store.effective('caption',defaults,contract=ProjectionContract(schema))
        assert exc.value.details['config_error_paths'] == ['features.caption.provider_defaults.github.cache_ttl_seconds']


def test_selected_caption_token_limit_and_final_newline_with_masked_common(tmp_path):
    defaults,schema = contracts()['caption']
    schema['$defs']['githubProvider']['properties']['github_token']['maxLength'] = 2048
    raw = {'provider_defaults':{'github':{'github_token':'x'*2048}},
           'providers':{p:{'github_token':''} for p in GITHUB}}
    doc = {'config_version':3,'features':{'caption':raw}}
    store = backend(tmp_path, doc)
    assert store.effective('caption', defaults,contract=ProjectionContract(schema))['providers']['mingy']['github_token'] == ''
    for token in ('x'*2049, 'sensitive\n'):
        raw['provider_defaults']['github']['github_token'] = token
        with pytest.raises(CentralConfigError) as exc:
            store.effective('caption',defaults,document=doc,contract=ProjectionContract(schema))
        assert exc.value.details['config_error_paths'] == ['features.caption.provider_defaults.github.github_token']


def test_alias_notices_mark_masked_and_applied_without_values(tmp_path):
    raw = {'rename':{'ai':{'base_url':'private-host','api_url':''}},
           'download':{'poll_interval':2,'request_interval':1,'poll_initial_interval':3}}
    store = backend(tmp_path, {'config_version':2,'features':raw})
    plan = store.preflight(old_context())
    assert not plan.blockers
    assert set(plan.notices) >= {'Masked features.rename.ai.base_url','Masked features.download.poll_interval',
                                 'Migrated features.download.request_interval'}
    assert 'private-host' not in repr(plan.notices)
