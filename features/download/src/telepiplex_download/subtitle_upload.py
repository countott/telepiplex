"""Bounded subtitle transfer and 115 OpenAPI/OSS upload.

The cloud protocol follows 115's /open/upload/init and get_token APIs. Upload
callbacks are issued by 115 and passed unchanged to OSS; bearer tokens never go
to OSS. Transfers contain only external subtitle bytes, never arbitrary files.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import PurePosixPath
import re
import sqlite3
import threading
import time
from urllib.parse import urlparse

from telepiplex_plugin_sdk import FeatureError

CHUNK_BYTES = 192 * 1024
MAX_BYTES = 8 * 1024 * 1024
_LOCK = threading.Lock()


def validate_target(path):
    path = str(path or "")
    value = PurePosixPath(path)
    if (not path.startswith("/") or ".." in value.parts
            or any(ord(char) < 32 for char in path)
            or "\\" in path or value.suffix.lower() not in {".srt", ".ass", ".vtt", ".ssa"}
            or not re.search(r"\.(?:chi|cht)\.(?:srt|ass|vtt|ssa)$", value.name)):
        raise FeatureError("invalid_request", "subtitle target must be an absolute chi/cht subtitle path")
    return str(value)


def receive_chunk(jobs, client, payload):
    if jobs is None or not getattr(jobs, "path", None):
        raise FeatureError("not_ready", "durable subtitle transfer store is required")
    path = validate_target(payload.get("path"))
    transfer_id = str(payload.get("transfer_id") or "")
    sha1 = str(payload.get("content_sha1") or "").lower()
    try:
        index = int(payload["chunk_index"])
        count = int(payload["chunk_count"])
        size = int(payload["size_bytes"])
        encoded = payload["chunk_base64"]
        if not isinstance(encoded, str) or len(encoded) > ((CHUNK_BYTES + 2) // 3) * 4:
            raise ValueError("chunk too large")
        chunk = base64.b64decode(encoded, validate=True)
    except (KeyError, ValueError, TypeError, binascii.Error) as exc:
        raise FeatureError("invalid_request", "invalid subtitle chunk") from exc
    if (not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", transfer_id)
            or not re.fullmatch(r"[0-9a-f]{40}", sha1)
            or not 0 < size <= MAX_BYTES or count != (size + CHUNK_BYTES - 1) // CHUNK_BYTES
            or not 0 <= index < count
            or len(chunk) != min(CHUNK_BYTES, size - index * CHUNK_BYTES)):
        raise FeatureError("invalid_request", "invalid subtitle transfer bounds")
    metadata = json.dumps([path, sha1, size, count], separators=(",", ":"))
    # The lock also prevents two last chunks from starting concurrent uploads.
    with _LOCK, sqlite3.connect(str(jobs.path) + ".subtitles.sqlite3") as db:
        db.execute("CREATE TABLE IF NOT EXISTS transfers (id TEXT PRIMARY KEY, metadata TEXT, result TEXT, updated REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS chunks (id TEXT, part INTEGER, body BLOB, PRIMARY KEY(id,part))")
        expired = time.time() - 86400
        db.execute("DELETE FROM chunks WHERE id IN (SELECT id FROM transfers WHERE updated < ?)", (expired,))
        db.execute("DELETE FROM transfers WHERE updated < ?", (expired,))
        row = db.execute("SELECT metadata,result FROM transfers WHERE id=?", (transfer_id,)).fetchone()
        if row and row[0] != metadata:
            raise FeatureError("transfer_conflict", "subtitle transfer identity changed")
        if row and row[1]:
            client._remove_cached_file(path)
            existing = client.get_file_info(path)
            if existing and client._item_sha1(existing).lower() == sha1:
                return {**json.loads(row[1]), "status": "exists"}
            if existing:
                raise FeatureError("target_conflict", "existing subtitle changed after upload; it was preserved")
            # A prior target can have moved or been deleted. Never claim a stale
            # receipt still exists; allow the caller to resend the byte stream.
            db.execute("UPDATE transfers SET result=NULL WHERE id=?", (transfer_id,))
        if not row:
            if db.execute("SELECT COUNT(*) FROM transfers WHERE result IS NULL").fetchone()[0] >= 32:
                raise FeatureError("capacity_exceeded", "too many active subtitle transfers")
            db.execute("INSERT INTO transfers VALUES (?,?,NULL,?)", (transfer_id, metadata, time.time()))
        previous = db.execute("SELECT body FROM chunks WHERE id=? AND part=?", (transfer_id, index)).fetchone()
        if previous and previous[0] != chunk:
            raise FeatureError("transfer_conflict", "subtitle chunk changed on retry")
        db.execute("INSERT OR IGNORE INTO chunks VALUES (?,?,?)", (transfer_id, index, chunk))
        db.execute("UPDATE transfers SET updated=? WHERE id=?", (time.time(), transfer_id))
        pieces = db.execute("SELECT part,body FROM chunks WHERE id=? ORDER BY part", (transfer_id,)).fetchall()
        db.commit()
        if len(pieces) < count:
            present = {part for part, _ in pieces}
            return {"status": "uploading", "next_chunk_index": next(i for i in range(count) if i not in present)}
        content = b"".join(body for _, body in pieces)
        if len(content) != size or hashlib.sha1(content).hexdigest() != sha1:
            raise FeatureError("content_mismatch", "subtitle transfer checksum failed")
        result = upload(client, path, content)
        db.execute("UPDATE transfers SET result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), transfer_id))
        db.execute("DELETE FROM chunks WHERE id=?", (transfer_id,))
        return result


def _data(client, method, endpoint, **kwargs):
    result = client._request(method, endpoint, **kwargs)
    if not client._successful(result) or not isinstance(result.get("data"), dict):
        raise FeatureError("upload_failed", "115 subtitle upload request failed")
    return result["data"]


def upload(client, path, content):
    sha1 = hashlib.sha1(content).hexdigest()
    client._remove_cached_file(path)
    existing = client.get_file_info(path)
    if existing:
        if client._item_sha1(existing).lower() == sha1:
            return {"status": "exists", "path": path, "sha1": sha1, "file_id": str(existing.get("file_id") or existing.get("fid") or "")}
        raise FeatureError("target_conflict", "existing subtitle differs; it was preserved")
    target = PurePosixPath(path)
    parent = client.create_dir_recursive(str(target.parent))
    parent_id = next((str(parent[key]).strip() for key in ("file_id", "cid", "id") if key in parent and parent[key] is not None and not isinstance(parent[key], bool) and str(parent[key]).strip()), "") if isinstance(parent, dict) else ""
    if not parent_id:
        raise FeatureError("upload_failed", "subtitle destination has no stable directory identity")
    params = {"file_name": target.name, "file_size": len(content), "target": f"U_1_{parent_id}", "fileid": sha1.upper(), "preid": hashlib.sha1(content[:131072]).hexdigest().upper()}
    initialized = _data(client, "POST", "/open/upload/init", data=params)
    if int(initialized.get("status") or 0) in {6, 7, 8}:
        match = re.fullmatch(r"(\d+)-(\d+)", str(initialized.get("sign_check") or ""))
        if not match or not initialized.get("sign_key"):
            raise FeatureError("upload_failed", "115 returned an invalid upload challenge")
        start, end = map(int, match.groups())
        if not 0 <= start <= end < len(content):
            raise FeatureError("upload_failed", "115 upload challenge exceeds subtitle bounds")
        params.update(sign_key=initialized["sign_key"], sign_val=hashlib.sha1(content[start:end + 1]).hexdigest().upper())
        initialized = _data(client, "POST", "/open/upload/init", data=params)
    if int(initialized.get("status") or 0) != 2:
        token = _data(client, "GET", "/open/upload/get_token")
        _put_oss(token, initialized, content, timeout=client.timeout)
    # OSS callback changes the tree outside _request; invalidate stale path reads.
    with client._cache_condition:
        client._cache_generation += 1
        client._file_cache.clear()
    verified = client.get_file_info(path)
    if not verified or client._item_sha1(verified).lower() != sha1:
        raise FeatureError("upload_unverified", "115 subtitle upload could not be verified by SHA1")
    return {"status": "placed", "path": path, "sha1": sha1, "file_id": str(verified.get("file_id") or verified.get("fid") or "")}


def _put_oss(token, initialized, content, *, timeout):
    endpoint = str(token.get("endpoint") or "")
    if endpoint.startswith("http://"):
        endpoint = "https://" + endpoint[7:]
    elif not endpoint.startswith("https://"):
        endpoint = "https://" + endpoint
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".aliyuncs.com") or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise FeatureError("upload_failed", "115 returned an invalid OSS endpoint")
    callback = initialized.get("callback")
    if not isinstance(callback, dict) or not callback.get("callback") or not initialized.get("bucket") or not initialized.get("object"):
        raise FeatureError("upload_failed", "115 upload callback is missing")
    try:
        import oss2
        headers = {"x-oss-forbid-overwrite": "true"}
        for source, header in (("callback", "x-oss-callback"), ("callback_var", "x-oss-callback-var")):
            value = callback.get(source)
            if value:
                text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
                headers[header] = base64.b64encode(text.encode()).decode()
        auth = oss2.StsAuth(token["AccessKeyId"], token["AccessKeySecret"], token["SecurityToken"])
        bucket = oss2.Bucket(auth, endpoint, initialized["bucket"], connect_timeout=timeout)
        result = bucket.put_object(initialized["object"], content, headers=headers)
        if not 200 <= result.status < 300:
            raise ValueError("OSS upload failed")
    except Exception as exc:
        # Upstream SDK exceptions may contain signed URLs or temporary credentials.
        raise FeatureError("upload_failed", "115 OSS subtitle upload failed") from None
