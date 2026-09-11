import asyncio
import copy
import sqlite3
import threading
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import telepiplex_plugin_sdk.storage_snapshot as snapshot_storage
from telepiplex_plugin_sdk import FeatureError
from telepiplex_plugin_sdk.storage_snapshot import SnapshotStore, build_snapshot, encoded
from telepiplex_rename.jobs import RenameJobStore
import telepiplex_rename.snapshot_reader as snapshot_reader
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
        self.validate_ack_copy=True
        self.transform=lambda page: page
        outer=self
        class Host:
            async def call_capability(self,capability,method,payload,**kwargs):
                outer.calls.append((method,payload))
                if method=='acknowledge_tree_snapshot':
                    if outer.validate_ack_copy:
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

    async def test_committed_copy_has_one_read_and_only_required_full_validations(self):
        self.validate_ack_copy=False
        real_get=SnapshotStore.get;real_validate_nodes=snapshot_storage.validate_nodes
        get_calls=[];validation_calls=[]
        def counted_get(store,ref):
            get_calls.append(ref['snapshot_id'])
            return real_get(store,ref)
        def counted_validate_nodes(rows,ref):
            validation_calls.append(ref['snapshot_id'])
            return real_validate_nodes(rows,ref)
        with patch.object(SnapshotStore,'get',counted_get), \
             patch.object(snapshot_storage,'validate_nodes',counted_validate_nodes):
            await self.read()
            cold=(len(get_calls),len(validation_calls),
                  sum(method=='get_tree_snapshot_page' for method,_ in self.calls))
            get_calls.clear();validation_calls.clear();self.calls.clear()
            await self.read()
            replay=(len(get_calls),len(validation_calls),
                    sum(method=='get_tree_snapshot_page' for method,_ in self.calls))
        self.assertEqual(cold,(1,2,self.ref['page_count']))
        self.assertEqual(replay,(1,1,0))

    async def test_local_store_work_and_row_flattening_leave_event_loop_responsive(self):
        self.validate_ack_copy=False
        loop=asyncio.get_running_loop();loop_thread=threading.get_ident()
        names=('construct','contains','put','get')
        entered={name:threading.Event() for name in names}
        release={name:threading.Event() for name in names}
        responsive={};worker_threads={};entry_threads=[];check_threads=[]

        def pause(name):
            worker_threads[name]=threading.get_ident();entered[name].set()
            if not release[name].wait(2):raise AssertionError(f'{name} release timed out')
        class ThreadObservedEntries(list):
            def __iter__(self):
                entry_threads.append(threading.get_ident())
                return super().__iter__()
        class PausedStore:
            def __init__(self,path):
                pause('construct');self.inner=SnapshotStore(path)
            def contains(self,ref):
                pause('contains');return self.inner.contains(ref)
            def put(self,ref,pages):
                pause('put');return self.inner.put(ref,pages)
            def get(self,ref):
                pause('get');pages=self.inner.get(ref)
                for page in pages:page['entries']=ThreadObservedEntries(page['entries'])
                return pages
        def control():
            for name in names:
                if not entered[name].wait(2):
                    responsive[name]=False;release[name].set();continue
                tick=threading.Event();loop.call_soon_threadsafe(tick.set)
                responsive[name]=tick.wait(.25);release[name].set()
        controller=threading.Thread(target=control,daemon=True);controller.start()
        def check():
            check_threads.append(threading.get_ident());self.check()
        try:
            with patch.object(snapshot_reader,'SnapshotStore',PausedStore):
                rows=await read_snapshot(self.host,self.jobs,self.ref,job_id='job',
                    root_path='/root',check_cancelled=check)
        finally:
            for event in release.values():event.set()
            controller.join(2)
        self.assertEqual(len(rows),1001)
        self.assertEqual(responsive,{name:True for name in names})
        self.assertTrue(all(worker_threads[name]!=loop_thread for name in names))
        self.assertTrue(entry_threads)
        self.assertTrue(all(thread_id!=loop_thread for thread_id in entry_threads))
        self.assertTrue(check_threads)
        self.assertEqual(set(check_threads),{loop_thread})

    async def test_cancel_during_local_put_stops_before_committed_read_and_ack(self):
        entered=threading.Event();release=threading.Event();get_calls=[]
        class PausedPutStore:
            def __init__(self,path):self.inner=SnapshotStore(path)
            def contains(self,ref):return self.inner.contains(ref)
            def put(self,ref,pages):
                result=self.inner.put(ref,pages);entered.set()
                if not release.wait(2):raise AssertionError('put release timed out')
                return result
            def get(self,ref):
                get_calls.append(ref['snapshot_id']);return self.inner.get(ref)
        def cancel():
            if entered.wait(2):self.cancelled=True
            release.set()
        canceller=threading.Thread(target=cancel,daemon=True);canceller.start()
        try:
            with patch.object(snapshot_reader,'SnapshotStore',PausedPutStore):
                with self.assertRaises(OperationCancelled):await self.read()
        finally:
            release.set();canceller.join(2)
        self.assertEqual(get_calls,[])
        self.assertNotIn('acknowledge_tree_snapshot',[method for method,_ in self.calls])
        self.assertTrue(SnapshotStore(str(self.jobs.path)+'.snapshots.sqlite3').contains(self.ref))

    async def test_cancel_during_committed_read_stops_before_second_read_and_ack(self):
        SnapshotStore(str(self.jobs.path)+'.snapshots.sqlite3').put(self.ref,self.pages)
        entered=threading.Event();release=threading.Event();get_calls=[]
        class PausedGetStore:
            def __init__(self,path):self.inner=SnapshotStore(path)
            def contains(self,ref):return self.inner.contains(ref)
            def get(self,ref):
                get_calls.append(ref['snapshot_id']);pages=self.inner.get(ref)
                if len(get_calls)==1:
                    entered.set()
                    if not release.wait(2):raise AssertionError('get release timed out')
                return pages
        def cancel():
            if entered.wait(2):self.cancelled=True
            release.set()
        canceller=threading.Thread(target=cancel,daemon=True);canceller.start()
        try:
            with patch.object(snapshot_reader,'SnapshotStore',PausedGetStore):
                with self.assertRaises(OperationCancelled):await self.read()
        finally:
            release.set();canceller.join(2)
        self.assertEqual(len(get_calls),1)
        self.assertEqual(self.calls,[])

    async def test_persisted_missing_page_or_bad_digest_is_rejected_without_ack(self):
        self.validate_ack_copy=False
        await self.read()
        store_path=str(self.jobs.path)+'.snapshots.sqlite3'
        for kind in ('missing','digest'):
            with self.subTest(kind=kind):
                with sqlite3.connect(store_path) as db:
                    if kind=='missing':
                        db.execute('DELETE FROM snapshot_pages_v1 WHERE snapshot_id=? AND page_index=1',
                                   (self.ref['snapshot_id'],))
                    else:
                        page=copy.deepcopy(self.pages[0]);page['entries'][0]['size']+=1
                        db.execute('UPDATE snapshot_pages_v1 SET page_json=? WHERE snapshot_id=? AND page_index=0',
                                   (encoded(page).decode(),self.ref['snapshot_id']))
                self.calls.clear()
                with self.assertRaises(FeatureError):await self.read()
                self.assertEqual(self.calls,[])
                with sqlite3.connect(store_path) as db:
                    db.execute('DELETE FROM snapshot_pages_v1 WHERE snapshot_id=?',(self.ref['snapshot_id'],))
                    db.executemany('INSERT INTO snapshot_pages_v1 VALUES (?,?,?)',
                        [(self.ref['snapshot_id'],i,encoded(page).decode()) for i,page in enumerate(self.pages)])

    async def test_first_fetch_rejects_corruption_after_put_before_ack(self):
        self.validate_ack_copy=False
        real_put=SnapshotStore.put
        def corrupt_after_put(store,ref,pages):
            real_put(store,ref,pages)
            with sqlite3.connect(store.path) as db:
                db.execute('DELETE FROM snapshot_pages_v1 WHERE snapshot_id=? AND page_index=1',
                           (ref['snapshot_id'],))
        with patch.object(SnapshotStore,'put',corrupt_after_put):
            with self.assertRaises(FeatureError):await self.read()
        self.assertNotIn('acknowledge_tree_snapshot',[method for method,_ in self.calls])
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
