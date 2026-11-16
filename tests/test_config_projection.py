from copy import deepcopy

import pytest

from tests.test_central_config import backend


@pytest.mark.parametrize("public", [True, False])
def test_v3_score_patch_replaces_entire_explicit_map(tmp_path, public):
    from tests.test_config_semantics_migration import contracts
    store = backend(tmp_path, {"config_version": 3, "features": {"search": {"scoring": {
        "keyword_scores": {"removed": 2}, "indexer_scores": {"old": 3}}}}})
    scoring = {"keyword_scores": {"new": 1}, "indexer_scores": {}}
    change = (store.prepare_document_patch({"features": {"search": {"scoring": scoring}}}) if public
              else store.prepare_patch("search", {"search": {"scoring": scoring}}))
    store.commit(change)
    assert store.read()["features"]["search"] == {"scoring": scoring}
    snapshot = store.effective("search", contracts()["search"][0])
    assert snapshot["search"]["scoring"]["indexer_scores"] == {}
    assert snapshot["search"]["scoring"]["keyword_scores"] == {"new": 1}


def test_v3_preflight_reports_public_search_field(tmp_path):
    from tests.test_config_semantics_migration import contracts
    from app.runtime.config_semantics_migration import MigrationContext
    store = backend(tmp_path, {"config_version": 3, "features": {"search": {"prowlarr": {"timeout": "invalid"}}}})
    assert "features.search.prowlarr.timeout" in store.preflight(MigrationContext(builtin=contracts())).blockers


def test_legacy_import_preserves_explicit_flat_scores_and_fills_missing_prowlarr(tmp_path):
    import yaml
    from tests.test_config_semantics_migration import contracts
    from app.runtime.config_semantics_migration import MigrationContext
    store = backend(tmp_path, {"features": {"search": {"scoring": {"keyword_scores": {}}}}})
    source = tmp_path/"plugins/search/config.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(yaml.safe_dump({"search": {"scoring": {"keyword_scores": {"old": 3}},
                                                 "prowlarr": {"timeout": 95}}}))
    store.migrate_legacy(tmp_path/"plugins")
    plan = store.preflight(MigrationContext(builtin=contracts()))
    assert not plan.blockers
    store.commit_semantics(plan)
    assert store.read()["features"]["search"] == {"scoring": {"keyword_scores": {}}, "prowlarr": {"timeout": 95}}


def test_automatic_policy_does_not_install_or_activate_caption(tmp_path):
    import asyncio
    from app.runtime.capability_router import CapabilityRouter, RoutingError
    from tests.test_capability_router import CapabilityRouterTest, FakeClient
    store = backend(tmp_path, {'config_version': 3})
    store.commit(store.prepare_document_patch({'features': {'caption': {'automatic': True}}}))
    router = CapabilityRouter()
    client = FakeClient()
    async def exercise():
        for state in ('absent', 'stopped'):
            if state == 'stopped':
                router.activate('caption', CapabilityRouterTest()._manifest('caption', provides=(('subtitle.caption', True),)), client)
                router.deactivate('caption')
            with pytest.raises(RoutingError) as caught:
                await router.call('subtitle.caption', 'prepare_download', {}, {'deadline': 1})
            assert caught.value.code == 'capability_unavailable'
            assert router.snapshot.plugin_ids == ()
            assert store.effective('caption', {})['automatic'] is True
        assert client.calls == []
    asyncio.run(exercise())


def test_v3_structural_read_isolates_broken_caption_binding(tmp_path):
    from app.runtime.central_config import CentralConfigError
    doc = {'config_version': 3, 'shared': {'directories': {}, 'directory_roles': {
        'manual_downloads': [], 'standalone_subtitles': {'directory_id': 'gone'}}}}
    store = backend(tmp_path, doc)
    assert store.read() == doc
    assert store.effective('download', {})['save_directories'] == []
    with pytest.raises(CentralConfigError):
        store.effective('caption', {})


def test_changed_legacy_kind_does_not_revert_another_roles_stale_path(tmp_path):
    from tests.test_config_semantics_migration import contracts
    from app.runtime.config_semantics_migration import MigrationContext
    store = backend(tmp_path, {'config_version': 2})
    store.commit_semantics(store.preflight(MigrationContext(builtin=contracts())))
    baseline = store.effective('rename', {})
    routes = deepcopy(baseline['category_folder'])
    key = store.read()['shared']['directory_roles']['media_categories'][routes[0]['kind']]['directory_id']
    edit = store.prepare_document_patch({'shared': {'directories': {key: {'path': '/new'}}}})
    assert set(edit.affected_plugins) == {'search', 'rename', 'sync'}
    store.commit(edit)
    routes[1]['path'] = '/other'
    store.commit(store.prepare_patch('rename', {'category_folder': routes}, baseline=baseline))
    assert [r['path'] for r in store.effective('rename', {})['category_folder'][:2]] == ['/new', '/other']


def test_legacy_paths_do_not_change_destinations_on_new_feature_packages(tmp_path):
    from app.runtime.config_projection import ProjectionContract
    store = backend(tmp_path, {'config_version': 2, 'features': {
        'download': {'save_directories': [{'name': ' Name ', 'path': ' movies/a/ '}]},
        'caption': {'output_path': '/subtitles/'}}})
    assert store.effective('download', {}, contract=ProjectionContract({}))['save_directories'] == [
        {'name': 'Name', 'path': 'movies/a'}]
    assert store.effective('caption', {}, contract=ProjectionContract({}))['output_path'] == '/subtitles'


def test_unsafe_legacy_path_blocks_only_its_own_release(tmp_path):
    from app.runtime.central_config import CentralConfigError
    from app.runtime.config_projection import ProjectionContract
    store = backend(tmp_path, {'config_version': 2, 'features': {'caption': {'output_path': '/a//b'}}})
    assert store.effective('download', {}, contract=ProjectionContract({})) == {}
    with pytest.raises(CentralConfigError):
        store.effective('caption', {}, contract=ProjectionContract({}))


def test_legacy_target_refuses_downgrade_of_extended_timeout(tmp_path):
    from app.runtime.central_config import CentralConfigError
    from app.runtime.config_projection import ProjectionContract
    from app.runtime.config_semantics_migration import MigrationContext, export_v2_candidate
    from tests.test_config_semantics_migration import contracts
    context = MigrationContext(builtin=contracts())
    store = backend(tmp_path, {'config_version': 2, 'features': {'download': {'refresh_token': 'old'}}})
    store.commit_semantics(store.preflight(context))
    store.commit(store.prepare_patch('download', {'refresh_token': 'latest'}))
    assert export_v2_candidate(store.read(), context=context)['features']['download']['refresh_token'] == 'latest'
    store.commit(store.prepare_patch('search', {'metadata': {'tvdb': {'timeout': 300}}},
                 contract=ProjectionContract({'x-telepiplex-metadata-timeout-contract': 'seconds_1_300_v1'})))
    with pytest.raises(CentralConfigError):
        export_v2_candidate(store.read(), context=context)


def test_old_host_downgrade_rejects_lossy_menu_without_mutating_live_tokens(tmp_path):
    from app.runtime.central_config import CentralConfigError
    from app.runtime.config_semantics_migration import MigrationContext, export_v2_candidate
    from tests.test_config_semantics_migration import contracts
    context = MigrationContext(builtin=contracts())
    store = backend(tmp_path, {"config_version": 2})
    store.commit_semantics(store.preflight(context))
    store.commit(store.prepare_document_patch({"shared": {
        "directories": {"extra": {"name": "offline", "path": "/ leading"}},
        "directory_roles": {"manual_downloads": [{"directory_id": "extra", "label": "Queue"}]}}}))
    store.commit(store.prepare_patch("download", {"access_token": "fresh", "refresh_token": "new-secret"}))
    before = store.path.read_bytes()
    with pytest.raises(CentralConfigError) as err:
        export_v2_candidate(store.read(), context=context)
    assert err.value.code == "config_migration_required"
    assert "features.download.save_directories" in str(err.value.details)
    assert "new-secret" not in str(err.value.details)
    assert store.path.read_bytes() == before


def test_dangling_delete_is_rejected_and_concurrent_new_reference_is_rechecked(tmp_path):
    from app.runtime.central_config import CentralConfigError
    from app.runtime.config_semantics_migration import MigrationContext
    from tests.test_config_semantics_migration import contracts
    store = backend(tmp_path, {'config_version': 2})
    store.commit_semantics(store.preflight(MigrationContext(builtin=contracts())))
    with pytest.raises(CentralConfigError) as exc:
        store.prepare_directory_delete('live_action_movie')
    assert 'media_categories.live_action_movie' in repr(exc.value.details)
    store.commit(store.prepare_document_patch({'shared': {'directories': {'free': {'name': 'unused', 'path': '/free'}}}}))
    deletion = store.prepare_directory_delete('free')
    store.commit(store.prepare_document_patch({'shared': {'directory_roles': {'standalone_subtitles': {'directory_id': 'free'}}}}))
    with pytest.raises(CentralConfigError):
        store.commit(deletion)
    assert 'free' in store.read()['shared']['directories']


def test_unrelated_v2_category_blocker_does_not_prevent_token_refresh(tmp_path):
    store = backend(tmp_path, {'config_version': 2, 'shared': {'category_folder': []}})
    store.commit(store.prepare_patch('download', {'refresh_token': 'new'}))
    assert store.read()['features']['download']['refresh_token'] == 'new'


def test_explicit_anon_equal_to_inherited_default_is_a_real_edit(tmp_path):
    from tests.test_config_semantics_migration import contracts
    from app.runtime.config_semantics_migration import MigrationContext
    store = backend(tmp_path, {'config_version': 2})
    packages = contracts()
    store.commit_semantics(store.preflight(MigrationContext(builtin=packages)))
    baseline = store.effective('caption', packages['caption'][0])
    store.commit(store.prepare_patch('caption', {'providers': {'nekomoe': {'github_token': ''}}}, baseline=baseline))
    store.commit(store.prepare_document_patch({'features': {'caption': {'provider_defaults': {'github': {'github_token': 'later'}}}}}))
    assert store.effective('caption', packages['caption'][0])['providers']['nekomoe']['github_token'] == ''


def test_caption_storage_target_matches_v2_projection_migration_and_export(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from pathlib import Path
    from tests.test_caption_business_flow import _metadata
    from tests.test_config_semantics_migration import contracts
    from app.runtime.config_semantics_migration import MigrationContext, export_v2_candidate
    for plugin in ('caption', 'rename'):
        monkeypatch.syspath_prepend(str(Path(f'features/{plugin}/src').absolute()))
    from telepiplex_caption.service import CaptionFeature
    from telepiplex_rename.caption_bridge import name_subtitle

    class Host:
        async def call_capability(self, capability, method, payload, **kwargs):
            assert (capability, method) == ('media.rename', 'place_subtitle')
            return {'status': 'placed', **name_subtitle(payload)}

    def placed(snapshot, video=''):
        feature = object.__new__(CaptionFeature)
        feature.host, feature.config = Host(), snapshot
        selection = SimpleNamespace(quality=SimpleNamespace(normalized_text='sample', language='chi'),
                                    query=SimpleNamespace(video_path=video, season=None, episode=None),
                                    document=SimpleNamespace(format='srt'))
        return asyncio.run(feature._place(_metadata(), selection))['path']

    raw = {'output_path': '/after '}
    assert placed(raw).startswith('/after /')
    assert placed(raw, '/video/x.mkv') == '/video/x.chi.srt'
    store = backend(tmp_path, {'config_version': 2, 'features': {'caption': raw}})
    context = MigrationContext(builtin=contracts())
    store.commit_semantics(store.preflight(context))
    current = store.effective('caption', context.builtin['caption'][0])
    assert placed(current) == placed(raw)
    assert placed(current, '/video/x.mkv') == placed(raw, '/video/x.mkv')
    assert placed(export_v2_candidate(store.read(), context=context)['features']['caption']) == placed(raw)


def test_v3_shadow_and_bad_binding_are_reported_without_breaking_unrelated_projection(tmp_path):
    from app.runtime.central_config import CentralConfigError
    from app.runtime.config_semantics_migration import MigrationContext
    from tests.test_config_semantics_migration import contracts
    store = backend(tmp_path, {'config_version': 3, 'shared': {'directory_roles': {
        'standalone_subtitles': {'directory_id': 'missing'}}}, 'features': {'sync': {'tmdb': {'api_key': 'private-secret'}}}})
    assert store.preflight(MigrationContext(builtin=contracts())).blockers
    assert store.effective('download', {}) == {}
    with pytest.raises(CentralConfigError) as caught:
        store.effective('sync', {})
    assert 'private-secret' not in str(caught.value.details)


def test_null_extra_root_needs_rename_package_that_disables_both_inventory_menus(tmp_path):
    from app.runtime.central_config import CentralConfigError
    from app.runtime.config_semantics_migration import MigrationContext
    from app.runtime.config_projection import ProjectionContract
    from tests.test_config_semantics_migration import contracts
    packages = contracts()
    defaults, old_schema = packages['rename']
    old_schema.pop('x-telepiplex-inventory-root-contract', None)
    packages['rename'] = (defaults, old_schema)
    doc = {'config_version': 2, 'features': {'rename': {'unorganized_path': '/'}}}
    store = backend(tmp_path, doc)
    old = MigrationContext(builtin=packages, installed={'rename': packages['rename']})
    assert store.preflight(old).blockers
    assert store.read() == doc
    assert store.effective('rename', packages['rename'][0], contract=ProjectionContract(packages['rename'][1]))['unorganized_path'] == '/'
    store.commit_semantics(store.preflight(MigrationContext(builtin=packages)))
    with pytest.raises(CentralConfigError, match='Upgrade Rename'):
        store.effective('rename', packages['rename'][0], contract=ProjectionContract(packages['rename'][1]))
    new = {**packages['rename'][1], 'x-telepiplex-inventory-root-contract': 'null_disables_extra_root_v1'}
    assert store.effective('rename', packages['rename'][0], contract=ProjectionContract(new))['unorganized_path'] == '/'


def test_old_download_cannot_activate_path_it_would_trim(tmp_path):
    from app.runtime.central_config import CentralConfigError
    from app.runtime.config_projection import ProjectionContract
    store = backend(tmp_path, {'config_version': 3, 'shared': {'directories': {'dir': {'name': 'exact', 'path': '/ Leading'}},
        'directory_roles': {'manual_downloads': [{'directory_id': 'dir', 'label': 'menu'}]}}})
    with pytest.raises(CentralConfigError, match='Upgrade Download'):
        store.effective('download', {}, contract=ProjectionContract({}))
    schema = {'x-telepiplex-download-directory-contract': 'preserve_segment_spaces_v1'}
    assert store.effective('download', {}, contract=ProjectionContract(schema))['save_directories'][0]['path'] == ' Leading'


@pytest.mark.parametrize('role', ['media_categories', 'unorganized_inventory'])
def test_inventory_trailing_space_rejected_on_v3_save_and_activation_but_leading_space_kept(tmp_path, role):
    from app.runtime.central_config import CentralConfigError
    from app.runtime.config_projection import ProjectionContract
    from app.runtime.config_semantics_migration import MigrationContext
    from tests.test_config_semantics_migration import contracts
    store = backend(tmp_path, {'config_version': 2})
    ctx = MigrationContext(builtin=contracts())
    store.commit_semantics(store.preflight(ctx))
    doc = store.read()
    binding = doc['shared']['directory_roles'][role]
    key = (binding['live_action_movie'] if role == 'media_categories' else binding)['directory_id']
    with pytest.raises(CentralConfigError, match='inventory') as exc:
        store.prepare_document_patch({'shared': {'directories': {key: {'path': '/after '}}}})
    assert role in str(exc.value.details)
    doc['shared']['directories'][key]['path'] = '/after '
    with pytest.raises(CentralConfigError):
        store.effective('rename', ctx.builtin['rename'][0], document=doc, contract=ProjectionContract({}))
    store.commit(store.prepare_document_patch({'shared': {'directories': {key: {'path': '/ leading/inner space'}}}}))
    assert store.read()['shared']['directories'][key]['path'] == '/ leading/inner space'


def test_extra_inventory_trailing_space_blocks_migration_and_leaves_bytes_intact(tmp_path):
    from app.runtime.config_semantics_migration import MigrationContext
    from tests.test_config_semantics_migration import contracts
    store = backend(tmp_path, {'config_version': 2, 'features': {'rename': {'unorganized_path': '/after '}}})
    original = store.path.read_bytes()
    assert 'features.rename.unorganized_path' in str(store.preflight(MigrationContext(builtin=contracts())).blockers)
    assert store.path.read_bytes() == original


def test_optional_role_label_inherits_directory_display_name(tmp_path):
    from tests.test_config_semantics_migration import contracts
    from app.runtime.config_semantics_migration import MigrationContext
    store = backend(tmp_path, {'config_version': 2})
    store.commit_semantics(store.preflight(MigrationContext(builtin=contracts())))
    doc = store.read()
    binding = doc['shared']['directory_roles']['media_categories']['live_action_movie']
    binding.pop('label')
    key = binding['directory_id']
    doc['shared']['directory_roles']['manual_downloads'] = [{'directory_id': key}, {'directory_id': 'animated_movie', 'label': 'My menu title'}]
    doc['shared']['directories'][key]['name'] = 'Library'
    assert store.effective('rename', {}, document=doc)['category_folder'][0]['name'] == 'Library'
    assert [r['name'] for r in store.effective('download', {}, document=doc)['save_directories']] == ['Library', 'My menu title']






