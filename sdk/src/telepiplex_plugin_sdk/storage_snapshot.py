"""Versioned immutable storage tree snapshots and durable local copies.

Sidecar databases deliberately have no expiry or destructive migration: replay
and rollback must preserve referenced snapshots even after acknowledgments.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

VERSION = 1
TRANSPORT = 'snapshot_ref_v1'
PAGE_BYTES = 262_144
PAGE_NODES = 500
MAX_NODES = 20_000
MAX_PAGES = MAX_NODES


class SnapshotError(RuntimeError):
    pass


def encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SnapshotError('snapshot cannot be encoded') from exc


def validate_reference(ref, *, job_id=None, root_path=None):
    keys = {'version','snapshot_id','job_id','root_path','root_id','node_count',
            'file_count','directory_count','digest','page_count'}
    if not isinstance(ref,dict) or set(ref)!=keys or type(ref['version']) is not int or ref['version']!=VERSION:
        raise SnapshotError('unsupported snapshot reference')
    for key in ('snapshot_id','job_id','root_path','root_id','digest'):
        if not isinstance(ref[key],str) or not ref[key] or len(ref[key])>4096:
            raise SnapshotError('invalid snapshot identity')
    try:
        if str(uuid.UUID(ref['snapshot_id']))!=ref['snapshot_id']:raise ValueError()
        if len(ref['digest'])!=64 or any(c not in '0123456789abcdef' for c in ref['digest']):raise ValueError()
    except ValueError as exc:raise SnapshotError('invalid snapshot identity') from exc
    if not ref['root_path'].startswith('/') or (ref['root_path']!='/' and ref['root_path'].endswith('/')):
        raise SnapshotError('invalid snapshot root')
    for key in ('node_count','file_count','directory_count','page_count'):
        if type(ref[key]) is not int or not 0<=ref[key]<=MAX_NODES:
            raise SnapshotError('invalid snapshot count')
    if not 1<=ref['page_count']<=MAX_PAGES or ref['node_count']!=ref['file_count']+ref['directory_count']:
        raise SnapshotError('inconsistent snapshot count')
    if job_id is not None and ref['job_id']!=str(job_id):raise SnapshotError('snapshot job mismatch')
    if root_path is not None and ref['root_path']!=str(root_path):raise SnapshotError('snapshot root mismatch')
    return ref


def cursor_for(ref,index):
    return f"{ref['snapshot_id']}:{index}"


def validate_page(ref,page,index,start):
    validate_reference(ref)
    if not isinstance(page,dict) or set(page)!={'reference','index','start','entries','next_cursor'}:
        raise SnapshotError('missing or malformed snapshot page')
    if encoded(page['reference'])!=encoded(ref) or type(page['index']) is not int or page['index']!=index or type(page['start']) is not int or page['start']!=start:
        raise SnapshotError('snapshot page is not contiguous or belongs to another snapshot')
    rows=page['entries']
    if not isinstance(rows,list) or len(rows)>PAGE_NODES or len(encoded(rows))>PAGE_BYTES:
        raise SnapshotError('snapshot page exceeds capacity')
    if not rows and not (ref['node_count']==0 and ref['page_count']==1):raise SnapshotError('empty snapshot page')
    expected=cursor_for(ref,index+1) if index+1<ref['page_count'] else None
    if page['next_cursor']!=expected:raise SnapshotError('invalid next snapshot cursor')
    # Measure actual complete response, with reserve for transport context.
    frame={'type':'response','id':'f'*32,'ok':True,'result':{'value':page}}
    if len(encoded(frame))+16384>=1_048_576:raise SnapshotError('snapshot response exceeds RPC capacity')
    return rows


def validate_nodes(rows,ref):
    if len(rows)!=ref['node_count']:raise SnapshotError('snapshot node count mismatch')
    ids,paths,dirs=set(),set(),set()
    for row in rows:
        if not isinstance(row,dict):raise SnapshotError('invalid snapshot node')
        fid=row.get('file_id');rel=row.get('relative_path');name=row.get('name');path=row.get('path')
        if not isinstance(fid,str) or not fid or fid in ids or not isinstance(rel,str) or rel in paths or not rel:
            raise SnapshotError('invalid snapshot node identity')
        parts=rel.split('/')
        if any(p in ('','.','..') or '\x00' in p for p in parts) or len(parts)>9 or type(row.get('is_dir')) is not bool or (row['is_dir'] and len(parts)>8):
            raise SnapshotError('invalid snapshot topology')
        root=ref['root_path'].rstrip('/')
        single=(len(rows)==1 and not row['is_dir'] and rel==root.rsplit('/',1)[-1] and path==root)
        if name!=parts[-1] or (path!=f'{root}/{rel}' and not single):raise SnapshotError('snapshot path mismatch')
        if type(row.get('size')) is not int or row['size']<0 or not isinstance(row.get('sha1',''),str):raise SnapshotError('invalid snapshot file facts')
        if fid==ref['root_id'] and not single:raise SnapshotError('snapshot contains root cycle')
        ids.add(fid);paths.add(rel)
        if row['is_dir']:dirs.add(rel)
    if any('/'.join(rel.split('/')[:i]) not in dirs for rel in paths for i in range(1,len(rel.split('/')))):
        raise SnapshotError('snapshot parent directory missing')
    if len(dirs)!=ref['directory_count'] or len(rows)-len(dirs)!=ref['file_count']:
        raise SnapshotError('snapshot file count mismatch')
    if hashlib.sha256(encoded(rows)).hexdigest()!=ref['digest']:raise SnapshotError('snapshot digest mismatch')
    return rows


def verify_snapshot(ref,pages):
    validate_reference(ref)
    if not isinstance(pages,list) or len(pages)!=ref['page_count']:raise SnapshotError('snapshot pages missing')
    rows=[]
    for index,page in enumerate(pages):rows.extend(validate_page(ref,page,index,len(rows)))
    return validate_nodes(rows,ref)


def build_snapshot(rows,*,job_id,root_path,root_id):
    # Own the input so later mutations of a caller's list cannot alter a snapshot.
    rows=json.loads(encoded(rows))
    batches=[];batch=[];size=2
    for row in rows:
        node_size=len(encoded(row))
        if node_size+2>PAGE_BYTES:raise SnapshotError('single snapshot node exceeds page capacity')
        if batch and (len(batch)>=PAGE_NODES or size+node_size+1>PAGE_BYTES):
            batches.append(batch);batch=[];size=2
        size+=node_size+(1 if batch else 0);batch.append(row)
    if batch or not batches:batches.append(batch)
    ref={'version':VERSION,'snapshot_id':str(uuid.uuid4()),'job_id':str(job_id),
         'root_path':str(root_path),'root_id':str(root_id),'node_count':len(rows),
         'file_count':sum(row.get('is_dir') is False for row in rows),
         'directory_count':sum(row.get('is_dir') is True for row in rows),
         'digest':hashlib.sha256(encoded(rows)).hexdigest(),'page_count':len(batches)}
    pages=[];start=0
    for index,batch in enumerate(batches):
        pages.append({'reference':ref,'index':index,'start':start,'entries':batch,
                      'next_cursor':cursor_for(ref,index+1) if index+1<len(batches) else None})
        start+=len(batch)
    verify_snapshot(ref,pages)
    return ref,pages


class SnapshotStore:
    """Immutable v1 sidecar. No public deletion: acknowledgment is only a receipt."""
    def __init__(self,path):
        self.path=str(path);Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS snapshots_v1 (snapshot_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, reference_json TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS snapshot_pages_v1 (snapshot_id TEXT NOT NULL, page_index INTEGER NOT NULL, page_json TEXT NOT NULL, PRIMARY KEY(snapshot_id,page_index))')
    def _connect(self):
        db=sqlite3.connect(self.path);db.execute('PRAGMA synchronous=FULL');return db
    def put(self,ref,pages):
        verify_snapshot(ref,pages)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT reference_json FROM snapshots_v1 WHERE snapshot_id=?',(ref['snapshot_id'],)).fetchone()
            if row:
                stored=[json.loads(r[0]) for r in db.execute('SELECT page_json FROM snapshot_pages_v1 WHERE snapshot_id=? ORDER BY page_index',(ref['snapshot_id'],))]
                if json.loads(row[0])!=ref or stored!=pages:raise SnapshotError('snapshot is immutable')
                return
            bound=db.execute('SELECT snapshot_id FROM snapshots_v1 WHERE job_id=?',(ref['job_id'],)).fetchone()
            if bound:raise SnapshotError('job already bound to another immutable snapshot')
            db.execute('INSERT INTO snapshots_v1(snapshot_id,job_id,reference_json) VALUES (?,?,?)',(ref['snapshot_id'],ref['job_id'],encoded(ref).decode()))
            db.executemany('INSERT INTO snapshot_pages_v1 VALUES (?,?,?)',[(ref['snapshot_id'],i,encoded(p).decode()) for i,p in enumerate(pages)])
    def _reference(self,db,ref):
        validate_reference(ref)
        row=db.execute('SELECT reference_json FROM snapshots_v1 WHERE snapshot_id=?',(ref['snapshot_id'],)).fetchone()
        if not row or encoded(json.loads(row[0]))!=encoded(ref):raise SnapshotError('snapshot reference unavailable or mismatched')
    def get(self,ref):
        with self._connect() as db:
            self._reference(db,ref)
            pages=[json.loads(r[0]) for r in db.execute('SELECT page_json FROM snapshot_pages_v1 WHERE snapshot_id=? ORDER BY page_index',(ref['snapshot_id'],))]
        verify_snapshot(ref,pages)
        return pages
    def contains(self,ref):
        validate_reference(ref)
        with self._connect() as db:
            bound=db.execute('SELECT snapshot_id FROM snapshots_v1 WHERE job_id=?',(ref['job_id'],)).fetchone()
            if bound and bound[0]!=ref['snapshot_id']:raise SnapshotError('job already bound to another immutable snapshot')
            return db.execute('SELECT 1 FROM snapshots_v1 WHERE snapshot_id=?',(ref['snapshot_id'],)).fetchone() is not None
    def page(self,ref,cursor=None):
        validate_reference(ref)
        index=0
        if cursor is not None:
            if not isinstance(cursor,str) or not cursor.startswith(ref['snapshot_id']+':'):raise SnapshotError('snapshot cursor mismatch')
            suffix=cursor[len(ref['snapshot_id'])+1:]
            if not suffix.isascii() or not suffix.isdigit():raise SnapshotError('snapshot cursor invalid')
            index=int(suffix)
            if index<1 or cursor!=cursor_for(ref,index):raise SnapshotError('snapshot cursor invalid')
        if index>=ref['page_count']:raise SnapshotError('snapshot cursor out of range')
        with self._connect() as db:
            self._reference(db,ref)
            row=db.execute('SELECT page_json FROM snapshot_pages_v1 WHERE snapshot_id=? AND page_index=?',(ref['snapshot_id'],index)).fetchone()
        if not row:raise SnapshotError('snapshot page missing')
        page=json.loads(row[0]);validate_page(ref,page,index,page.get('start'))
        return page
    def acknowledge(self,ref):
        with self._connect() as db:
            self._reference(db,ref)
            db.execute('UPDATE snapshots_v1 SET acknowledged=1 WHERE snapshot_id=?',(ref['snapshot_id'],))
        return {'snapshot_id':ref['snapshot_id'],'retained':True}
