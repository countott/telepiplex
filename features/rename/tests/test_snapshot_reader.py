import copy
import sqlite3
import unittest
import tempfile
from pathlib import Path
from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.storage_snapshot import SnapshotStore, build_snapshot, encoded
from telepiplex_rename.jobs import RenameJobStore
from telepiplex_rename.snapshot_reader import read_snapshot
from telepiplex_rename.operations import OperationCancelled
from telepiplex_rename.service import RenameFeature
from tests import test_feature_processor as fixtures


def make_snapshot(count=1001,job_id='job'):
    rows=[dict(name=f'{i}.mkv',relative_path=f'{i}.mkv',path=f'/root/{i}.mkv',
               file_id=str(i),is_dir=False,size=1024,sha1='') for i in range(count)]
    return build_snapshot(rows,job_id=job_id,root_path='/root',root_id='root')


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.jobs=RenameJobStore(Path(self.temp.name)/'jobs.db')
        self.ref,self.pages=make_snapshot();self.calls=[];self.cancelled=False
        self.transform=lambda page: page
        outer=self
        class Host:
            async def call_capability(self,capability,method,payload,**kwargs):
                outer.calls.append((method,payload))
                if method=='acknowledge_tree_snapshot':
                    store=SnapshotStore(str(outer.jobs.path)+'.snapshots.sqlite3')
                    assert store.get(outer.ref)==outer.pages
                    return {'value':{'retained':True}}
                cursor=payload['args'][1];index=int(cursor.split(':')[1]) if cursor else 0
                return {'value':outer.transform(copy.deepcopy(outer.pages[index]))}
        self.host=Host()
    def check(self):
        if self.cancelled:raise OperationCancelled('cancelled')
    async def read(self,**kwargs):
        return await read_snapshot(self.host,self.jobs,kwargs.get('ref',self.ref),job_id='job',root_path='/root',check_cancelled=self.check)
    async def test_restart_copy_survives_result_overwrite_ack_and_provider_loss(self):
        self.jobs.claim('job')
        rows=await self.read();self.assertEqual(len(rows),1001)
        self.jobs.update('job','processed',{'replacement':'no snapshot in result'})
        self.jobs=RenameJobStore(self.jobs.path)
        async def offline(*args,**kwargs):raise RuntimeError('provider offline')
        self.host.call_capability=offline
        self.assertEqual(await self.read(),rows)
        self.assertEqual(self.jobs.get('job')['result'],{'replacement':'no snapshot in result'})
    async def test_page_faults_fail_without_ack_or_partial_copy(self):
        def faults(kind,page):
            if page['index']==1:
                if kind=='drop':return None
                if kind=='duplicate':return copy.deepcopy(self.pages[0])
                if kind=='digest':page['entries'][0]['size']+=1
                if kind=='start':page['start']+=1
                if kind=='crossref':page['reference']['root_id']='different'
                if kind=='boolean_version':page['reference']['version']=True
                if kind=='cursor':page['next_cursor']='wrong'
                if kind=='oversized':page['entries'][0]['name']='中'*100000
            return page
        for kind in ['drop','duplicate','digest','start','crossref','boolean_version','cursor','oversized']:
            with self.subTest(kind=kind):
                self.calls.clear();self.transform=lambda page:faults(kind,page)
                with self.assertRaises(FeatureError):await self.read()
                self.assertNotIn('acknowledge_tree_snapshot',[x[0] for x in self.calls])
                self.assertFalse(SnapshotStore(str(self.jobs.path)+'.snapshots.sqlite3').contains(self.ref))
    async def test_cancel_during_page_fetch_creates_no_copy_and_no_ack(self):
        def cancel(page):
            if page['index']==1:self.cancelled=True
            return page
        self.transform=cancel
        with self.assertRaises(OperationCancelled):await self.read()
        self.assertFalse(SnapshotStore(str(self.jobs.path)+'.snapshots.sqlite3').contains(self.ref))
        self.assertEqual(len(self.calls),2)
    async def test_bad_job_root_unknown_version_or_different_snapshot_rejected(self):
        for field,value in [('job_id','other'),('root_path','/elsewhere'),('version',99)]:
            with self.subTest(field=field):
                with self.assertRaises(FeatureError):await self.read(ref={**self.ref,field:value})
        self.assertEqual(self.calls,[])
        await self.read();self.calls.clear()
        other,_=make_snapshot()
        with self.assertRaises(FeatureError):await self.read(ref=other)
        self.assertEqual(self.calls,[])
    async def test_old_job_schema_and_transaction_rollback(self):
        with sqlite3.connect(self.jobs.path) as db:before=db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
        await self.read()
        with sqlite3.connect(self.jobs.path) as db:self.assertEqual(before,db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall())
        store=SnapshotStore(Path(self.temp.name)/'rollback.db')
        with sqlite3.connect(store.path) as db:
            db.execute("CREATE TRIGGER fail_page BEFORE INSERT ON snapshot_pages_v1 WHEN NEW.page_index=1 BEGIN SELECT RAISE(ABORT, 'disk write failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):store.put(self.ref,self.pages)
        self.assertFalse(store.contains(self.ref))
    async def test_invalid_pages_through_actual_service_never_run_processor(self):
        for kind in ['drop','digest','unknown']:
            with self.subTest(kind=kind):
                host=fixtures.FakeHost();runtime=fixtures.FakeRuntime()
                job_id='service-'+kind;ref,pages=make_snapshot(job_id=job_id)
                async def capability(capability,method,payload,**kwargs):
                    if method!='get_tree_snapshot_page':raise AssertionError('no ack allowed')
                    index=0 if payload['args'][1] is None else int(payload['args'][1].split(':')[1])
                    page=copy.deepcopy(pages[index])
                    if kind=='drop':return {'value':None}
                    page['entries'][0]['size']+=1
                    return {'value':page}
                host.call_capability=capability
                feature=RenameFeature(config={},host=host,jobs=self.jobs);feature.bind_runtime(runtime)
                called=[];feature._process=lambda event:called.append(event)
                await feature.download_completed({'payload':{'job_id':job_id,'file_tree_transport':'snapshot_ref_v1' if kind!='unknown' else 'future_v7','file_tree_snapshot':ref,'snapshot_complete':True,'final_path':'/root','resource_name':'Release','user_id':1}})
                await runtime.wait()
                self.assertEqual(called,[])
                self.assertEqual(host.storage.renamed+host.storage.moved+host.storage.deleted,[])
                self.assertEqual(self.jobs.get(job_id)['state'],'failed')

    async def test_service_paging_cancellation_has_zero_mutations(self):
        host=fixtures.FakeHost();runtime=fixtures.FakeRuntime()
        feature=RenameFeature(config={},host=host,jobs=self.jobs);feature.bind_runtime(runtime)
        async def capability(capability,method,payload,**kwargs):
            if method!='get_tree_snapshot_page':raise AssertionError('no ack allowed')
            cursor=payload['args'][1];index=int(cursor.split(':')[1]) if cursor else 0
            if index==1:
                for operation in feature.operations.values():operation['cancel_event'].set()
            return {'value':copy.deepcopy(self.pages[index])}
        host.call_capability=capability
        called=[];feature._process=lambda event:called.append(event)
        await feature.download_completed({'payload':{'job_id':'job','operation_id':'op-cancel-page','file_tree_transport':'snapshot_ref_v1','file_tree_snapshot':self.ref,'snapshot_complete':True,'final_path':'/root','resource_name':'Release','user_id':1}})
        await runtime.wait()
        self.assertEqual(called,[])
        self.assertEqual(host.storage.renamed+host.storage.moved+host.storage.deleted,[])
        self.assertEqual(self.jobs.get('job')['state'],'cancelled')
        self.assertFalse(SnapshotStore(str(self.jobs.path)+'.snapshots.sqlite3').contains(self.ref))
