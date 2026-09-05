import copy
import sqlite3
import pytest
from telepiplex_plugin_sdk.storage_snapshot import (
    SnapshotError, SnapshotStore, build_snapshot, encoded, PAGE_BYTES,
)
from telepiplex_plugin_sdk.storage_tree import collect_snapshot_tree
from tests.test_tree_integrity import make_client, node


def tree(count):
    return [dict(name=f'{i}.mkv', relative_path=f'{i}.mkv', path=f'/root/{i}.mkv',
                 file_id=str(i), is_dir=False, size=1024, sha1='') for i in range(count)]


@pytest.mark.parametrize('count', [999,1000,1001])
def test_snapshot_boundaries_persist_and_reopen(tmp_path,count):
    ref,pages=build_snapshot(tree(count),job_id='job',root_path='/root',root_id='root')
    store=SnapshotStore(tmp_path/'snapshots.db');store.put(ref,pages)
    reopened=SnapshotStore(tmp_path/'snapshots.db')
    assert reopened.get(ref)==pages
    assert sum(len(p['entries']) for p in pages)==count
    assert ref['node_count']==ref['file_count']==count
    assert all(len(encoded(p['entries']))<=PAGE_BYTES for p in pages)
    assert all(len(encoded({'type':'response','id':'f'*32,'ok':True,'result':{'value':p}}))<1048576 for p in pages)
    assert reopened.page(ref,None)==pages[0]
    for p in pages[:-1]: assert reopened.page(ref,p['next_cursor'])==pages[p['index']+1]
    reopened.acknowledge(ref)
    assert reopened.get(ref)==pages


def test_snapshot_large_scan_10000_files_500_directories():
    children={'root':[node(f'd{i}',True) for i in range(500)]}
    children.update({f'd{i}':[node(f'f{i}-{j}') for j in range(20)] for i in range(500)})
    client,calls=make_client(children)
    rows=collect_snapshot_tree(client,'/root')
    ref,pages=build_snapshot(rows,job_id='large',root_path='/root',root_id='root')
    assert ref['file_count']==10000 and ref['directory_count']==500 and ref['node_count']==10500
    assert sum(len(p['entries']) for p in pages)==10500
    assert len(calls)==1005


def test_cursor_cross_reference_missing_and_immutable(tmp_path):
    store=SnapshotStore(tmp_path/'snapshots.db')
    ref,pages=build_snapshot(tree(1001),job_id='job',root_path='/root',root_id='root')
    other,_=build_snapshot(tree(1001),job_id='other',root_path='/root',root_id='root')
    store.put(ref,pages)
    for cursor in [other['snapshot_id']+':1',ref['snapshot_id']+':999','bad']:
        with pytest.raises(SnapshotError):store.page(ref,cursor)
    with pytest.raises(SnapshotError):store.page(other,None)
    with pytest.raises(SnapshotError):store.page({**ref,'root_id':'other'},None)
    altered=copy.deepcopy(pages);altered[0]['entries'][0]['size']=9
    with pytest.raises(SnapshotError):store.put(ref,altered)
    assert store.get(ref)==pages


def test_oversized_single_entry():
    rows=tree(1);rows[0]['name']='长'*100000
    with pytest.raises(SnapshotError):build_snapshot(rows,job_id='job',root_path='/root',root_id='root')


def test_sidecar_does_not_migrate_old_jobs_schema(tmp_path):
    from telepiplex_download.jobs import DownloadJobStore
    path=tmp_path/'jobs.db';jobs=DownloadJobStore(path);jobs.create_or_get('job',{'legacy':True})
    with sqlite3.connect(path) as db:before=db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
    ref,pages=build_snapshot(tree(1001),job_id='job',root_path='/root',root_id='root')
    SnapshotStore(str(path)+'.snapshots.sqlite3').put(ref,pages)
    assert DownloadJobStore(path).get('job')['payload']=={'legacy':True}
    with sqlite3.connect(path) as db:assert before==db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()


import unittest
from tests import test_feature_runtime as fixtures
from telepiplex_download.jobs import DownloadJobStore
from telepiplex_download.service import DownloadFeature


class SnapshotCompletionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.DownloadFeatureTest.asyncSetUp
    asyncTearDown=fixtures.DownloadFeatureTest.asyncTearDown

    async def run_tree(self,count,**extra):
        import tempfile
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.feature.jobs=DownloadJobStore(self.directory.name+'/jobs.db')
        self.feature.config['enable_tree_snapshot_references']=True
        self.feature.config['minimum_video_size_mib']=0
        self.client.wait_for_download=lambda *a,**k:{'resource_name':'root','info_hash':'hash'}
        rows=[node(i) for i in range(count)]
        self.client.get_file_info=lambda p:{'file_id':'root','file_category':'0'}
        self.scan_calls=[]
        def listing(params):
            self.scan_calls.append(params)
            offset=params['offset']
            return {'list':rows[offset:offset+params['limit']]}
        self.client.get_file_list=listing
        await self.feature.download_capability({'method':'submit','payload':{
            'link':'magnet:?xt=urn:btih:'+'d'*40,'selected_path':'/Downloads',
            'operation_id':'op-snapshot','chat_id':10,'user_id':1,**extra,
        },'context':{'idempotency_key':'snapshot-job'}})
        await self.runtime.tasks.pop('snapshot-job')

    async def test_provider_restart_page_replay_does_not_scan_remote(self):
        await self.run_tree(1001)
        event,payload,_=self.host.events[0]
        self.assertEqual(event,'download.completed');self.assertEqual(payload['file_tree_transport'],'snapshot_ref_v1')
        self.assertEqual(payload['file_tree'],[])
        ref=payload['file_tree_snapshot'];self.assertEqual(ref['node_count'],1001)
        self.assertEqual(len(self.scan_calls),6)
        self.feature.jobs.update('snapshot-job','completed',result={'overwritten':True})
        jobs=DownloadJobStore(self.feature.jobs.path)
        restarted=DownloadFeature(config={},host=self.host,client=object(),jobs=jobs)
        request={'method':'get_tree_snapshot_page','payload':{'args':[ref,None]}}
        first=await restarted.storage_capability(request)
        self.assertEqual(first,await restarted.storage_capability(request))
        self.assertEqual(first['value']['entries'][0]['file_id'],'0')
        cursor=first['value']['next_cursor'];total=len(first['value']['entries'])
        while cursor:
            value=(await restarted.storage_capability({'method':'get_tree_snapshot_page','payload':{'args':[ref,cursor]}}))['value']
            total+=len(value['entries']);cursor=value['next_cursor']
        self.assertEqual(total,1001);self.assertEqual(len(self.scan_calls),6)
        await restarted.storage_capability({'method':'acknowledge_tree_snapshot','payload':{'args':[ref]}})
        self.assertEqual(first,await restarted.storage_capability(request))

    async def test_small_trees_stay_inline_when_opted_in(self):
        for count in (999,1000):
            with self.subTest(count=count):
                self.host.events.clear();self.feature.operations.clear();self.feature.active_job_ids.clear()
                await self.run_tree(count)
                payload=self.host.events[0][1]
                self.assertEqual(payload['file_tree_transport'],'inline_v1')
                self.assertEqual(len(payload['file_tree']),count)

    async def test_large_reference_keeps_release_capacity_guard(self):
        await self.run_tree(1001,release={'title':'长'*350000})
        self.assertEqual(self.client.deleted_files,[])
        self.assertEqual(self.host.events[0][0],'download.failed')
        self.assertEqual(self.host.events[0][1]['error_code'],'download_tree_capacity_exceeded')


def test_utf8_byte_budget_splits_pages_before_node_limit():
    rows=tree(100)
    for index,row in enumerate(rows):
        name='长'*1000+f'{index}.mkv'
        row.update(name=name,relative_path=name,path='/root/'+name)
    ref,pages=build_snapshot(rows,job_id='long-names',root_path='/root',root_id='root')
    assert len(pages)>1
    assert all(len(encoded(p['entries']))<=PAGE_BYTES for p in pages)
    assert all(len(p['entries'])<500 for p in pages)
    assert sum(len(p['entries']) for p in pages)==100
