import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sqlite3
import pytest

from telepiplex_plugin_sdk import FeatureError
from telepiplex_download.client import Open115Client
from telepiplex_download.subtitle_upload import CHUNK_BYTES, receive_chunk, upload, _put_oss


def part(content, index=0, path='/media/Movie.chi.ass'):
    return dict(path=path, transfer_id='transfer001', content_sha1=hashlib.sha1(content).hexdigest(),
                size_bytes=len(content), chunk_count=(len(content)+CHUNK_BYTES-1)//CHUNK_BYTES,
                chunk_index=index, chunk_base64=base64.b64encode(content[index*CHUNK_BYTES:(index+1)*CHUNK_BYTES]).decode())


def test_durable_chunks_reordered_replayed_and_content_removed(tmp_path):
    content = b'[Script Info]\n' + b'x' * (CHUNK_BYTES * 5)
    jobs = SimpleNamespace(path=str(tmp_path/'jobs.db'))
    result = {'status':'placed','path':'/media/Movie.chi.ass'}
    with patch('telepiplex_download.subtitle_upload.upload', return_value=result) as uploader:
        assert receive_chunk(jobs, object(), part(content, 2))['status'] == 'uploading'
        assert receive_chunk(jobs, object(), part(content, 2))['next_chunk_index'] == 0
        for index in (0,1,3,4,5):
            value = receive_chunk(SimpleNamespace(path=jobs.path), object(), part(content,index))
        assert value == result
        receipt_client = SimpleNamespace(_remove_cached_file=lambda path: None, get_file_info=lambda path: {'sha1': hashlib.sha1(content).hexdigest()}, _item_sha1=lambda item: item['sha1'])
        assert receive_chunk(jobs, receipt_client, part(content,0)) == {**result, 'status':'exists'}
        uploader.assert_called_once()
        assert uploader.call_args.args[2] == content
    with sqlite3.connect(jobs.path+'.subtitles.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] == 0


def test_chunk_identity_checksum_and_bounds_are_enforced(tmp_path):
    jobs=SimpleNamespace(path=str(tmp_path/'jobs.db'))
    content=b'a'*(CHUNK_BYTES+1)
    receive_chunk(jobs, object(), part(content))
    wrong=part(content); wrong['chunk_base64']=base64.b64encode(b'b'*CHUNK_BYTES).decode()
    with pytest.raises(FeatureError, match='changed on retry'):
        receive_chunk(jobs,object(),wrong)
    wrong=part(content,1); wrong['path']='/other/Movie.chi.ass'
    with pytest.raises(FeatureError, match='identity changed'):
        receive_chunk(jobs,object(),wrong)
    wrong=part(b'a');wrong['content_sha1']='0'*40;wrong['transfer_id']='checksum01'
    with pytest.raises(FeatureError,match='checksum'):
        receive_chunk(jobs,object(),wrong)
    wrong=part(b'a');wrong['size_bytes']=9*1024*1024
    with pytest.raises(FeatureError,match='bounds'):
        receive_chunk(jobs,object(),wrong)
    for path in ['/../bad.chi.srt','/media/bad.py','/media/bad.srt','relative.chi.srt']:
        with pytest.raises(FeatureError):receive_chunk(jobs,object(),part(b'a',path=path))


class UploadClient(Open115Client):
    def __init__(self, content, existing=None, challenge=False):
        super().__init__({})
        self.content=content;self.existing=existing;self.uploaded=False;self.calls=[];self.challenge=challenge
    def get_file_info(self,path):
        return {'file_id':'new','sha1':hashlib.sha1(self.content).hexdigest()} if self.uploaded else self.existing
    def create_dir_recursive(self,path):return {'file_id':'123'}
    def _request(self,method,endpoint,**kwargs):
        self.calls.append((method,endpoint,kwargs))
        if endpoint.endswith('/init'):
            if self.challenge and 'sign_val' not in kwargs['data']:
                return {'state':True,'data':{'status':7,'sign_key':'challenge','sign_check':'1-3'}}
            return {'state':True,'data':{'status':1,'bucket':'bucket','object':'object','callback':{'callback':'{}','callback_var':'{}'}}}
        return {'state':True,'data':{'endpoint':'https://oss-cn-shenzhen.aliyuncs.com','AccessKeyId':'id','AccessKeySecret':'secret','SecurityToken':'token'}}


def test_real_protocol_init_challenge_oss_callback_and_verification():
    client=UploadClient(b'subtitle',challenge=True)
    def put(token, initialized, content, **kwargs):client.uploaded=True
    with patch('telepiplex_download.subtitle_upload._put_oss',side_effect=put) as oss:
        assert upload(client,'/media/Movie.chi.srt',b'subtitle')['status']=='placed'
    assert len(client.calls)==3
    assert client.calls[1][2]['data']['sign_val']==hashlib.sha1(b'ubt').hexdigest().upper()
    assert client.calls[1][2]['data']['target']=='U_1_123'
    assert oss.call_args.args[2]==b'subtitle'


def test_existing_same_content_is_noop_different_content_never_overwritten():
    client=UploadClient(b'a',{'file_id':'old','sha1':hashlib.sha1(b'a').hexdigest()})
    assert upload(client,'/media/Movie.chi.srt',b'a')['status']=='exists'
    assert client.calls==[]
    with pytest.raises(FeatureError,match='preserved'):
        upload(client,'/media/Movie.chi.srt',b'b')
    assert client.calls==[]


def test_oss_only_accepts_115_issued_aliyun_endpoint_and_sanitizes_errors():
    with pytest.raises(FeatureError,match='endpoint'):
        _put_oss({'endpoint':'https://evil.invalid'},{},b'a',timeout=5)
    class OSS:
        @staticmethod
        def StsAuth(*args):raise RuntimeError('AccessKeySecret must not be logged')
    with patch.dict('sys.modules',{'oss2':OSS}):
        with pytest.raises(FeatureError) as caught:
            _put_oss({'endpoint':'https://oss-cn-shenzhen.aliyuncs.com','AccessKeyId':'a','AccessKeySecret':'b','SecurityToken':'c'},
                     {'bucket':'b','object':'o','callback':{'callback':'{}'}},b'a',timeout=5)
        assert 'AccessKeySecret' not in str(caught.value)


def test_cancelled_request_barrier_waits_for_accepted_cloud_write(tmp_path):
    import asyncio
    import threading
    from telepiplex_download.service import DownloadFeature
    started, release = threading.Event(), threading.Event()
    def slow_upload(*args):
        started.set()
        assert release.wait(3)
        return {'status':'placed','path':'/media/Movie.chi.srt'}
    async def scenario():
        feature=DownloadFeature(config={},host=object(),client=object(),jobs=SimpleNamespace(path=str(tmp_path/'jobs.db')))
        request=asyncio.create_task(feature.storage_capability({'method':'upload_subtitle_chunk','payload':{}}))
        assert await asyncio.to_thread(started.wait,2)
        request.cancel()
        barrier=asyncio.create_task(feature.storage_capability({'method':'wait_subtitle_uploads','payload':{}}))
        await asyncio.sleep(.02)
        assert not barrier.done() and not request.done()
        release.set()
        assert await barrier == {'value':{'settled':True}}
        with pytest.raises(asyncio.CancelledError):await request
        assert not feature._subtitle_upload_tasks
    with patch('telepiplex_download.subtitle_upload.receive_chunk', side_effect=slow_upload):
        asyncio.run(scenario())
