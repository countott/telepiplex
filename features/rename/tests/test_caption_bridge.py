import base64
import asyncio
from functools import wraps
import pytest
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

from telepiplex_plugin_sdk import FeatureError
from telepiplex_rename.caption_bridge import capability, name_subtitle
from telepiplex_rename.service import RenameFeature
from telepiplex_rename.models import DownloadCompletedEvent
from telepiplex_rename.processor import validate_inline_tree
from telepiplex_rename.subtitles import build_movie_subtitle_plan
from tests.test_media_metadata_v2 import contract


def async_test(fn):
    @wraps(fn)
    def wrapped():
        return asyncio.run(fn())
    return wrapped


def test_video_name_retains_exact_stem_and_no_duplicate_extension():
    result=name_subtitle({'video_path':'/media/Show.S01E01.1080p.mkv','language':'cht','extension':'.ass'})
    assert result['path']=='/media/Show.S01E01.1080p.cht.ass'


def test_no_video_uses_frozen_title_folder_and_scope():
    value=contract('episode')
    result=name_subtitle({'media_metadata':value,'target_dir':'/字幕','language':'chi','extension':'srt'})
    assert result['path']=='/字幕/中文剧集 (2024) ⋯ English Series/English Series Season 02/English Series S02E03.chi.srt'
    with pytest.raises(FeatureError,match='scope'):
        name_subtitle({'media_metadata':value,'target_dir':'/字幕','language':'chi','extension':'srt','season':3,'episode':1})
    with pytest.raises(FeatureError):
        name_subtitle({'target_dir':'/字幕','language':'chi','extension':'srt'})


def test_chi_and_cht_do_not_collide_or_get_variant_suffixes():
    plan=build_movie_subtitle_plan(final_path='/input',target_dir='/movie',target_stem='Movie',file_tree=[
        {'relative_path':'source.chi.srt','file_id':'1'},
        {'relative_path':'source.cht.srt','file_id':'2'},
    ])
    assert {operation['rename_to'] for operation in plan['operations']}=={'Movie.chi.srt','Movie.cht.srt'}


@async_test
async def test_place_checks_video_and_delegates_only_named_target():
    host=SimpleNamespace(call_capability=AsyncMock(return_value={'value':{'status':'placed','path':'/media/Movie.cht.ass'}}))
    feature=RenameFeature(config={},host=host)
    feature._storage_value=AsyncMock(return_value={'file_id':'v','is_dir':False})
    result=await capability(feature,{'method':'place_subtitle','payload':{'video_path':'/media/Movie.mkv','language':'cht','extension':'ass','content_base64':base64.b64encode(b'subtitle').decode()}})
    assert result['status']=='placed'
    assert host.call_capability.call_args.args[2]['path']=='/media/Movie.cht.ass'
    feature._storage_value=AsyncMock(return_value=None)
    with pytest.raises(FeatureError,match='no longer exists'):
        await capability(feature,{'method':'place_subtitle','payload':{'video_path':'/media/Movie.mkv','language':'chi','extension':'srt'}})


@async_test
async def test_scanner_paginated_stable_media_only_and_root_bound_cursor():
    feature=RenameFeature(config={},host=object())
    feature._storage_value=AsyncMock(return_value={'file_id':'root','is_dir':True})
    feature._inventory_file_tree=AsyncMock(return_value=[{'path':f'/library/Movie{i}.mkv','file_id':str(i),'name':f'Movie{i}.mkv','relative_path':f'Movie{i}.mkv','size':100,'is_dir':False} for i in range(3)])
    first=await capability(feature,{'method':'scan_library','payload':{'root_path':'/library','limit':2}})
    assert first['total']==3 and len(first['media'])==2 and first['next_cursor']
    last=await capability(feature,{'method':'scan_library','payload':{'root_path':'/library','limit':2,'cursor':first['next_cursor']}})
    assert len(last['media'])==1 and last['next_cursor']==''
    assert feature._inventory_file_tree.await_count==1
    with pytest.raises(FeatureError,match='another root'):
        await capability(feature,{'method':'scan_library','payload':{'root_path':'/other','cursor':first['next_cursor']}})


def test_single_file_scope_allows_only_exact_matching_sidecars():
    video={'path':'/download/Movie.mkv','relative_path':'Movie.mkv','file_id':'v','is_dir':False}
    sidecar={'path':'/download/Movie.cht.srt','relative_path':'Movie.cht.srt','file_id':'s','is_dir':False}
    validate_inline_tree([video,sidecar],root_path='/download/Movie.mkv')
    sidecar.update(path='/download/Unrelated.cht.srt',relative_path='Unrelated.cht.srt')
    with pytest.raises(FeatureError,match='outside'):
        validate_inline_tree([video,sidecar],root_path='/download/Movie.mkv')


@async_test
async def test_caption_hook_refreshes_before_processing_and_keeps_root():
    video={'path':'/download/Movie.mkv','relative_path':'Movie.mkv','file_id':'v','name':'Movie.mkv','is_dir':False,'size':100}
    sidecar={'path':'/download/Movie.cht.srt','relative_path':'Movie.cht.srt','file_id':'s','name':'Movie.cht.srt','is_dir':False,'size':10}
    host=SimpleNamespace(call_capability=AsyncMock(return_value={'status':'completed','added_count':1}))
    feature=RenameFeature(config={},host=host)
    feature._report_if_active=AsyncMock()
    feature._storage_value=AsyncMock(side_effect=lambda method,path: {'/download/Movie.mkv':video,'/download/Movie.cht.srt':sidecar}.get(path))
    event=DownloadCompletedEvent(link='',selected_path='/library',user_id=1,final_path='/download/Movie.mkv',resource_name='Movie',file_tree=[video])
    await feature._prepare_caption({'final_path':'/download/Movie.mkv','file_tree':[video]},event,'')
    assert event.final_path=='/download/Movie.mkv'
    assert event.file_tree==[video,sidecar]
    assert event.caption_tree_verified


@async_test
async def test_download_caption_is_placed_before_rename_and_preserves_cht():
    from tests.test_feature_processor import FakeHost, EmptyAfterMoveStorage, FakeRuntime, movie_contract_v2
    storage = EmptyAfterMoveStorage([{'fn':'Movie.2024.mkv','fid':'video','fc':'1','fs':1000}])
    class CaptionHost(FakeHost):
        async def call_capability(self, capability, method, payload, **kwargs):
            if capability == 'subtitle.caption':
                assert method == 'prepare_download'
                assert storage.renamed == [] and storage.moved == []
                assert payload['file_tree'][0]['file_id'] == 'video'
                storage.items.append({'fn':'Movie.2024.cht.srt','fid':'subtitle','fc':'1','fs':50})
                return {'status':'completed','added_count':1,'existing_count':0,'warnings':[]}
            return await super().call_capability(capability, method, payload, **kwargs)
    host = CaptionHost(storage)
    feature = RenameFeature(config={'storage_timeout':3},host=host)
    runtime = FakeRuntime()
    feature.bind_runtime(runtime)
    await feature.download_completed({'event_id':'caption-event','payload':{
        'job_id':'caption-job','user_id':123,'chat_id':10,'selected_path':'/Movies',
        'download_root':'/Downloads/Movie.2024.mkv','final_path':'/Downloads/Movie.2024.mkv',
        'resource_name':'Movie.2024.mkv','operation_id':'caption-op','operation_revision':2,
        'media_metadata':movie_contract_v2(),
    }})
    await runtime.wait()
    assert ('/Downloads/Movie.2024.cht.srt','English Movie.cht.srt') in storage.renamed
    assert ('/Downloads/Movie.2024.mkv','English Movie.mkv') in storage.renamed
    assert '/Downloads' not in storage.deleted
    assert any('English Movie.cht.srt' in source for source,_ in storage.moved)


@async_test
async def test_library_scan_rejects_silent_truncation_and_duplicate_nodes():
    feature=RenameFeature(config={},host=object())
    feature._storage_value=AsyncMock(side_effect=[{'count':2,'list':[{'file_id':'1','name':'Movie.mkv','is_dir':False}]}, {'count':2,'list':[]}])
    with pytest.raises(FeatureError,match='declared count'):
        await feature._inventory_directory_items('root')
    duplicate={'file_id':'1','name':'Movie.mkv','is_dir':False}
    feature._storage_value=AsyncMock(return_value=[duplicate,duplicate])
    with pytest.raises(FeatureError,match='repeats'):
        await feature._inventory_directory_items('root')


@async_test
async def test_automatic_caption_batches_all_300_videos_from_verified_tree():
    import json
    tree=[{'path':f'/download/Show.S01E{i:03d}.mkv','relative_path':f'Show.S01E{i:03d}.mkv','name':f'Show.S01E{i:03d}.mkv','file_id':str(i),'size':10,'is_dir':False} for i in range(1,301)]
    calls=[]
    async def request(capability, method, payload, **kwargs):
        assert len(payload['file_tree'])<=50
        assert len(json.dumps(payload).encode())<1024*1024
        calls.extend(item['file_id'] for item in payload['file_tree'])
        return {'status':'completed','added_count':0,'existing_count':0,'processed_count':len(payload['file_tree'])}
    feature=RenameFeature(config={},host=SimpleNamespace(call_capability=request))
    feature._report_if_active=AsyncMock()
    event=DownloadCompletedEvent(link='',selected_path='/library',user_id=1,final_path='/download',resource_name='Show',file_tree=tree)
    payload={'job_id':'batch','media_metadata':contract(),'file_tree': [{'stale':'not-used'}], 'file_tree_snapshot':{'large':'x'*2000000}}
    await feature._prepare_caption(payload,event,'')
    assert calls==[str(i) for i in range(1,301)]
    assert payload['caption_result']['status']=='completed'


@async_test
async def test_ambiguous_caption_waits_for_write_barrier_before_rescan():
    barrier_entered, finish_write = asyncio.Event(), asyncio.Event()
    async def request(capability, method, payload, **kwargs):
        if capability=='subtitle.caption':raise FeatureError('deadline_exceeded','caption deadline')
        assert method=='wait_subtitle_uploads'
        barrier_entered.set()
        await finish_write.wait()
        return {'value':{'settled':True}}
    feature=RenameFeature(config={},host=SimpleNamespace(call_capability=request))
    feature._report_if_active=AsyncMock()
    video={'path':'/download/Movie.mkv','name':'Movie.mkv','relative_path':'Movie.mkv','file_id':'v','is_dir':False,'size':100}
    feature._storage_value=AsyncMock(side_effect=lambda method,path:video if path=='/download/Movie.mkv' else None)
    event=DownloadCompletedEvent(link='',selected_path='/library',user_id=1,final_path='/download/Movie.mkv',resource_name='Movie',file_tree=[video])
    task=asyncio.create_task(feature._prepare_caption({'final_path':event.final_path},event,''))
    await barrier_entered.wait()
    assert feature._storage_value.await_count==0 and not task.done()
    finish_write.set()
    await task
    assert feature._storage_value.await_count>0
    assert event.caption_tree_verified


@async_test
async def test_caption_uses_rename_evidence_to_skip_samples_and_extras():
    tree=[{'path':f'/download/{name}','relative_path':name,'name':name,'file_id':str(i),'size':100,'is_dir':False} for i,name in enumerate(('Movie.mkv','Movie.trailer.mkv','sample.mkv','Show.NCOP-01.mkv'))]
    host=SimpleNamespace(call_capability=AsyncMock(return_value={'status':'completed','added_count':0,'existing_count':0}))
    feature=RenameFeature(config={},host=host)
    feature._report_if_active=AsyncMock()
    event=DownloadCompletedEvent(link='',selected_path='/library',user_id=1,final_path='/download',resource_name='Movie',file_tree=tree)
    await feature._prepare_caption({},event,'')
    assert [node['name'] for node in host.call_capability.call_args.args[2]['file_tree']]==['Movie.mkv']


@async_test
async def test_disabled_caption_stops_first_batch_without_scan_or_completion_note():
    from telepiplex_rename.service import _caption_completion_note
    tree = [{'path': f'/download/Show.S01E{i:03d}.mkv', 'name': f'Show.S01E{i:03d}.mkv',
             'relative_path': f'Show.S01E{i:03d}.mkv', 'file_id': str(i), 'size': 100, 'is_dir': False}
            for i in range(1, 102)]
    host = SimpleNamespace(call_capability=AsyncMock(return_value={'status': 'disabled', 'added_count': 0}))
    feature = RenameFeature(config={}, host=host)
    feature._report_if_active = AsyncMock()
    feature._storage_value = AsyncMock()
    event = DownloadCompletedEvent(link='', selected_path='/library', user_id=1, final_path='/download', resource_name='Show', file_tree=tree)
    payload = {'final_path': '/download'}
    await feature._prepare_caption(payload, event, '')
    assert host.call_capability.await_count == 1
    assert feature._storage_value.await_count == 0
    assert payload['caption_result']['status'] == 'disabled'
    assert _caption_completion_note({'event_payload': payload}) == ''
    assert feature._report_if_active.call_args.kwargs['stage'] == 'organizing'


@async_test
async def test_disable_after_uploaded_batch_retains_counts_source_credit_and_refresh():
    from telepiplex_rename.service import _caption_completion_note
    tree = [{'path': f'/download/Show.S01E{i:03d}.mkv', 'name': f'Show.S01E{i:03d}.mkv',
             'relative_path': f'Show.S01E{i:03d}.mkv', 'file_id': str(i), 'size': 100, 'is_dir': False}
            for i in range(1, 102)]
    attribution = [{'provider': 'assrt', 'source_page': 'https://assrt.net/xml/sub/42/fixture.html', 'text': '字幕服务由 assrt.net 提供'}]
    host = SimpleNamespace(call_capability=AsyncMock(side_effect=[
        {'status': 'completed', 'added_count': 1, 'existing_count': 2, 'processed_count': 50, 'attribution': attribution},
        {'status': 'disabled', 'added_count': 0},
    ]))
    feature = RenameFeature(config={}, host=host)
    feature._report_if_active = AsyncMock()
    feature._storage_value = AsyncMock(return_value={'file_id': 'root', 'is_dir': True})
    feature._inventory_file_tree = AsyncMock(return_value=tree)
    event = DownloadCompletedEvent(link='', selected_path='/library', user_id=1, final_path='/download', resource_name='Show', file_tree=tree)
    payload = {'final_path': '/download'}
    await feature._prepare_caption(payload, event, '')
    result = payload['caption_result']
    assert host.call_capability.await_count == 2
    assert result['status'] == 'partial'
    assert result['added_count'] == 1 and result['existing_count'] == 2
    assert result['attribution'] == attribution
    assert 'caption_disabled_after_partial' in result['warnings']
    assert feature._inventory_file_tree.await_count == 1
    note = _caption_completion_note({'event_payload': payload})
    assert '新增 1，已存在 2' in note
    assert '字幕服务由 assrt.net 提供' in note
    assert attribution[0]['source_page'] in note


def test_failed_media_organization_keeps_successful_subtitle_attribution_in_terminal_report():
    feature = RenameFeature(config={}, host=object())
    feature.operations['op'] = {'operation_id': 'op', 'chat_id': 10, 'user_id': 1, 'revision': 2,
                                'state': 'running', 'stage': 'organizing', 'control': 'cancel', 'status_text': '正在整理'}
    source_page = 'https://assrt.net/xml/sub/42/fixture.html'
    outcome = {'organized': False, 'cleanup_complete': True, 'message': '目标冲突，媒体整理失败。',
               'final_path': '/download', 'file_results': {}, 'event_payload': {'caption_result': {
                   'status': 'completed', 'added_count': 1, 'existing_count': 0,
                   'attribution': [{'provider': 'assrt', 'source_page': source_page}],
               }}}
    feature._prepare_terminal_outcome('job', outcome, 'op')
    report = outcome['terminal_operation_report']
    assert report['state'] == 'failed'
    assert report['status_text'].startswith('目标冲突，媒体整理失败。')
    assert '外挂字幕：新增 1' in report['status_text']
    assert '字幕服务由 assrt.net 提供' in report['status_text']
    assert source_page in report['status_text']


async def _assert_caption_retained_after_processor_exit(error, expected_state):
    import tempfile
    from pathlib import Path
    from telepiplex_rename.jobs import RenameJobStore
    from tests.test_feature_processor import FakeHost, EmptyAfterMoveStorage, FakeRuntime, movie_contract_v2

    source_page = 'https://assrt.net/xml/sub/42/fixture.html'
    caption_result = {'status': 'completed', 'added_count': 1, 'existing_count': 0,
                      'attribution': [{'provider': 'assrt', 'source_page': source_page}]}
    storage = EmptyAfterMoveStorage([{'fn': 'Movie.2024.mkv', 'fid': 'video', 'fc': '1', 'fs': 1000}])

    class CaptionHost(FakeHost):
        async def call_capability(self, capability, method, payload, **kwargs):
            if capability == 'subtitle.caption':
                storage.items.append({'fn': 'Movie.2024.chi.srt', 'fid': 'subtitle', 'fc': '1', 'fs': 50})
                return caption_result
            return await super().call_capability(capability, method, payload, **kwargs)

    with tempfile.TemporaryDirectory() as directory:
        jobs = RenameJobStore(Path(directory) / 'jobs.db')
        host = CaptionHost(storage)
        feature = RenameFeature(config={'storage_timeout': 3}, host=host, jobs=jobs)
        feature._process = Mock(side_effect=error)
        runtime = FakeRuntime()
        feature.bind_runtime(runtime)
        await feature.download_completed({'event_id': 'caption-event', 'payload': {
            'job_id': 'caption-job', 'user_id': 123, 'chat_id': 10, 'selected_path': '/Movies',
            'download_root': '/Downloads/Movie.2024.mkv', 'final_path': '/Downloads/Movie.2024.mkv',
            'resource_name': 'Movie.2024.mkv', 'operation_id': 'caption-op', 'operation_revision': 2,
            'media_metadata': movie_contract_v2(),
        }})
        await runtime.wait()
        saved = jobs.get('caption-job')
        assert saved['state'] == expected_state
        assert saved['result']['event_payload']['caption_result']['attribution'] == caption_result['attribution']
        assert saved['result']['event_payload']['operation_id'] == 'caption-op'
        report = host.reports[-1]
        assert report['state'] == expected_state
        assert '外挂字幕：新增 1，已存在 0' in report['status_text']
        assert '字幕服务由 assrt.net 提供' in report['status_text']
        assert source_page in report['status_text']
        assert report['status_text'] == saved['result']['message']
        assert any(item['fid'] == 'subtitle' for item in storage.items)
        assert not storage.renamed and not storage.moved and not storage.deleted


@async_test
async def test_cancelled_organization_preserves_uploaded_subtitle_and_credit():
    await _assert_caption_retained_after_processor_exit(asyncio.CancelledError(), 'cancelled')


@async_test
async def test_exception_after_caption_preserves_uploaded_subtitle_and_credit():
    await _assert_caption_retained_after_processor_exit(RuntimeError('processor failed'), 'failed')


@async_test
async def test_cancel_during_later_caption_batch_keeps_prior_results():
    import threading
    tree = [{'path': f'/download/Show.S01E{i:03d}.mkv', 'name': f'Show.S01E{i:03d}.mkv',
             'relative_path': f'Show.S01E{i:03d}.mkv', 'file_id': str(i), 'size': 100, 'is_dir': False}
            for i in range(1, 102)]
    attribution = [{'provider': 'assrt', 'source_page': 'https://assrt.net/xml/sub/42/fixture.html'}]
    next_batch = asyncio.Event()
    calls = []

    async def request(capability, method, payload, **kwargs):
        calls.append(payload)
        if len(calls) == 1:
            return {'status': 'completed', 'added_count': 1, 'existing_count': 2,
                    'processed_count': 50, 'attribution': attribution}
        next_batch.set()
        await asyncio.Event().wait()

    feature = RenameFeature(config={}, host=SimpleNamespace(call_capability=request))
    feature.operations['op'] = {'cancel_event': threading.Event()}
    feature._report_if_active = AsyncMock()
    feature._storage_value = AsyncMock()
    event = DownloadCompletedEvent(link='', selected_path='/library', user_id=1, final_path='/download', resource_name='Show', file_tree=tree)
    payload = {'final_path': '/download'}
    task = asyncio.create_task(feature._prepare_caption(payload, event, 'op'))
    await asyncio.wait_for(next_batch.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = payload['caption_result']
    assert result['status'] == 'partial'
    assert result['added_count'] == 1 and result['existing_count'] == 2
    assert result['attribution'] == attribution
    assert 'caption_cancelled' in result['warnings']
    assert feature.operations['op']['caption_result'] == result
    assert feature._storage_value.await_count == 0


@async_test
async def test_rollback_terminal_keeps_caption_credit_and_explains_retained_sidecar():
    source_page = 'https://assrt.net/xml/sub/42/fixture.html'
    result = {'status': 'completed', 'added_count': 1, 'existing_count': 0,
              'attribution': [{'provider': 'assrt', 'source_page': source_page}]}
    for state in ('rolled_back', 'partially_rolled_back'):
        feature = RenameFeature(config={}, host=object())
        feature.operations['op'] = {'caption_result': result}
        feature._report_operation = AsyncMock()
        journal = SimpleNamespace(rollback=AsyncMock(return_value={'state': state, 'restored': [], 'remaining': []}))
        await feature._rollback_after_forward_stop('op', journal, None)
        report = feature._report_operation.call_args.kwargs
        assert report['state'] == state
        assert report['details']['caption_result'] == result
        assert '已获取的外挂字幕保留。' in report['status_text']
        assert '字幕服务由 assrt.net 提供' in report['status_text']
        assert source_page in report['status_text']
