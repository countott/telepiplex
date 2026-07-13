from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from pathlib import PurePosixPath

import requests


class Open115Error(RuntimeError):
    pass


class Open115Client:
    TOKEN_EXPIRED_CODES = {40140125, 40140126}

    def __init__(self, config: dict, *, session=None, on_tokens_changed=None):
        self.config = config
        self.base_url = str(config.get("base_url") or "https://proapi.115.com").rstrip("/")
        self.passport_url = str(config.get("passport_url") or "https://passportapi.115.com").rstrip("/")
        self.access_token = str(config.get("access_token") or "")
        self.refresh_token = str(config.get("refresh_token") or "")
        self.timeout = max(1, float(config.get("timeout") or 30))
        self.request_interval = max(0, float(config.get("request_interval") or 1))
        self.session = session or requests.Session()
        self.on_tokens_changed = on_tokens_changed
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._file_cache = {}

    def set_tokens(self, access_token: str, refresh_token: str):
        access_token = str(access_token or "").strip()
        refresh_token = str(refresh_token or "").strip()
        if not access_token or not refresh_token:
            raise Open115Error("115 access_token and refresh_token are required")
        self.access_token = access_token
        self.refresh_token = refresh_token

    def _headers(self):
        if not self.access_token:
            raise Open115Error("115 access_token is not configured")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "Telepiplex-Feature/1.0",
        }

    def _request(self, method: str, path: str, *, params=None, data=None, retry=True):
        with self._lock:
            remaining = self.request_interval - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
            self._last_request = time.monotonic()
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                params=params,
                data=data,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise Open115Error(f"115 request failed: {type(exc).__name__}") from exc
        if not isinstance(result, dict):
            raise Open115Error("115 returned a non-object response")
        if retry and result.get("code") in self.TOKEN_EXPIRED_CODES:
            self.refresh_access_token()
            return self._request(method, path, params=params, data=data, retry=False)
        return result

    def refresh_access_token(self):
        if not self.refresh_token:
            raise Open115Error("115 refresh_token is not configured")
        try:
            response = self.session.post(
                f"{self.passport_url}/open/refreshToken",
                headers={"User-Agent": "Telepiplex-Feature/1.0"},
                data={"refresh_token": self.refresh_token},
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise Open115Error(f"115 token refresh failed: {type(exc).__name__}") from exc
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or not data.get("access_token"):
            raise Open115Error("115 token refresh returned invalid data")
        self.access_token = str(data["access_token"])
        self.refresh_token = str(data.get("refresh_token") or self.refresh_token)
        if self.on_tokens_changed:
            self.on_tokens_changed(self.access_token, self.refresh_token)

    @staticmethod
    def _pkce_pair():
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return verifier, challenge

    def create_device_authorization(self, app_id: str):
        app_id = str(app_id or "").strip()
        if not app_id:
            raise Open115Error("115 app_id is not configured")
        verifier, challenge = self._pkce_pair()
        try:
            response = self.session.post(
                f"{self.passport_url}/open/authDeviceCode",
                headers={"User-Agent": "Telepiplex-Feature/1.0"},
                data={
                    "client_id": app_id,
                    "code_challenge": challenge,
                    "code_challenge_method": "sha256",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise Open115Error(
                f"115 device authorization failed: {type(exc).__name__}"
            ) from exc
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or not all(
            data.get(key) for key in ("uid", "time", "sign", "qrcode")
        ):
            raise Open115Error("115 device authorization returned invalid data")
        return {
            "uid": str(data["uid"]),
            "time": data["time"],
            "sign": str(data["sign"]),
            "qrcode": str(data["qrcode"]),
            "code_verifier": verifier,
        }

    def complete_device_authorization(
        self,
        authorization: dict,
        *,
        timeout: float = 300,
        poll_interval: float = 2,
    ):
        deadline = time.monotonic() + max(float(timeout), 1)
        params = {
            "uid": authorization["uid"],
            "time": authorization["time"],
            "sign": authorization["sign"],
        }
        while time.monotonic() < deadline:
            try:
                response = self.session.get(
                    "https://qrcodeapi.115.com/get/status/",
                    headers={"User-Agent": "Telepiplex-Feature/1.0"},
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                result = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise Open115Error(
                    f"115 device authorization polling failed: {type(exc).__name__}"
                ) from exc
            data = result.get("data") if isinstance(result, dict) else None
            status = str(data.get("status")) if isinstance(data, dict) else ""
            if status == "2":
                break
            if status == "0":
                raise Open115Error("115 device authorization expired")
            time.sleep(max(float(poll_interval), 0.1))
        else:
            raise Open115Error("115 device authorization timed out")

        try:
            response = self.session.post(
                f"{self.passport_url}/open/deviceCodeToToken",
                headers={"User-Agent": "Telepiplex-Feature/1.0"},
                data={
                    "uid": authorization["uid"],
                    "code_verifier": authorization["code_verifier"],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise Open115Error(
                f"115 device token exchange failed: {type(exc).__name__}"
            ) from exc
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or not data.get("access_token") or not data.get("refresh_token"):
            raise Open115Error("115 device token exchange returned invalid data")
        self.set_tokens(data["access_token"], data["refresh_token"])
        if self.on_tokens_changed:
            self.on_tokens_changed(self.access_token, self.refresh_token)
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
        }

    @staticmethod
    def _successful(result):
        return isinstance(result, dict) and (
            result.get("state") is True or result.get("code") == 0
        )

    def get_file_info(self, path: str):
        path = self._normalize(path)
        if path in self._file_cache:
            return self._file_cache[path]
        result = self._request("GET", "/open/folder/get_info", params={"path": path})
        if not self._successful(result) or not isinstance(result.get("data"), dict):
            return None
        self._file_cache[path] = result["data"]
        return result["data"]

    def get_file_info_by_id(self, file_id: str):
        result = self._request("GET", "/open/folder/get_info", params={"file_id": file_id})
        return result.get("data") if self._successful(result) else None

    def get_file_list(self, params: dict):
        result = self._request("GET", "/open/ufile/files", params=dict(params))
        return result.get("data") if self._successful(result) else None

    def create_directory(self, parent_id, name: str):
        result = self._request(
            "POST", "/open/folder/add", data={"pid": parent_id, "file_name": name}
        )
        if self._successful(result):
            return result.get("data") or True
        if result.get("code") == 20004:
            return True
        return None

    def create_dir_recursive(self, path: str):
        path = self._normalize(path)
        existing = self.get_file_info(path)
        if existing:
            return existing
        current_path = ""
        current_info = {"file_id": 0}
        for part in PurePosixPath(path).parts:
            if part == "/":
                continue
            current_path += "/" + part
            info = self.get_file_info(current_path)
            if info:
                current_info = info
                continue
            created = self.create_directory(
                current_info.get("file_id") or current_info.get("cid") or 0,
                part,
            )
            self._file_cache.pop(current_path, None)
            info = self.get_file_info(current_path)
            if not info and isinstance(created, dict):
                info = created
            if not info:
                raise Open115Error(f"cannot create 115 directory: {current_path}")
            current_info = info
        return current_info

    def add_offline_task(self, link: str, save_path: str):
        directory = self.create_dir_recursive(save_path)
        result = self._request(
            "POST",
            "/open/offline/add_task_urls",
            data={"urls": link, "wp_path_id": directory["file_id"]},
        )
        if not self._successful(result):
            raise Open115Error(str(result.get("message") or "cannot add offline task"))
        return True

    def get_offline_tasks(self):
        first = self._request("GET", "/open/offline/get_task_list", params={"page": 1})
        if not self._successful(first) or not isinstance(first.get("data"), dict):
            return []
        pages = max(1, int(first["data"].get("page_count") or 1))
        tasks = list(first["data"].get("tasks") or [])
        for page in range(2, pages + 1):
            result = self._request("GET", "/open/offline/get_task_list", params={"page": page})
            if self._successful(result) and isinstance(result.get("data"), dict):
                tasks.extend(result["data"].get("tasks") or [])
        return tasks

    def wait_for_download(self, link: str, *, timeout: float, poll_interval: float):
        deadline = time.monotonic() + float(timeout)
        last = {"name": "", "info_hash": "", "percentDone": 0}
        while time.monotonic() < deadline:
            for task in self.get_offline_tasks():
                if task.get("url") != link:
                    continue
                last = task
                progress = float(task.get("percentDone") or 0)
                if task.get("status") == 2 or progress >= 100:
                    return {
                        "resource_name": str(task.get("name") or ""),
                        "info_hash": str(task.get("info_hash") or ""),
                        "progress": 100,
                    }
                break
            time.sleep(float(poll_interval))
        raise Open115Error(
            f"115 download timed out at {float(last.get('percentDone') or 0):.1f}%"
        )

    def del_offline_task(self, info_hash: str, del_source_file=0):
        result = self._request(
            "POST",
            "/open/offline/del_task",
            data={"info_hash": info_hash, "del_source_file": int(del_source_file)},
        )
        return self._successful(result)

    def rename(self, source_path: str, new_name: str):
        info = self.get_file_info(source_path)
        if not info:
            return False
        result = self._request(
            "POST",
            "/open/ufile/update",
            data={"file_id": info["file_id"], "file_name": new_name},
        )
        if self._successful(result):
            self._file_cache.clear()
            return True
        return False

    def copy_file(self, source_path: str, target_path: str):
        source = self.get_file_info(source_path)
        target = self.get_file_info(target_path)
        if not source or not target:
            return False
        result = self._request(
            "POST",
            "/open/ufile/copy",
            data={
                "file_id": source["file_id"],
                "pid": target["file_id"],
                "nodupli": 1,
            },
        )
        return self._successful(result)

    def delete_single_file(self, path: str):
        info = self.get_file_info(path)
        if not info:
            return False
        result = self._request(
            "POST", "/open/ufile/delete", data={"file_ids": info["file_id"]}
        )
        if self._successful(result):
            self._file_cache.clear()
            return True
        return False

    def move_file(self, source_path: str, target_path: str):
        return self.move_file_detailed(source_path, target_path)["state"] == "moved"

    def move_file_detailed(self, source_path: str, target_path: str):
        self.create_dir_recursive(target_path)
        if not self.copy_file(source_path, target_path):
            return {"state": "copy_failed", "copied": False, "source_deleted": False,
                    "source_path": source_path, "target_path": f"{target_path.rstrip('/')}/{PurePosixPath(source_path).name}"}
        target = f"{target_path.rstrip('/')}/{PurePosixPath(source_path).name}"
        try:
            deleted = self.delete_single_file(source_path)
        except Exception as exc:
            deleted = False
            error = type(exc).__name__
        else:
            error = "" if deleted else "delete_failed"
        return {"state": "moved" if deleted else "copied_source_retained",
                "copied": True, "source_deleted": bool(deleted),
                "source_path": source_path, "target_path": target, "error": error}

    def is_directory(self, path: str):
        info = self.get_file_info(path)
        return bool(info and str(info.get("file_category")) == "0")

    def get_files_from_dir(self, path: str, file_type=4):
        info = self.get_file_info(path)
        if not info:
            return []
        data = self.get_file_list({"cid": info["file_id"], "type": file_type, "limit": 1000})
        return [item.get("fn") for item in (data or []) if item.get("fn")]

    @staticmethod
    def _list_items(value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = value.get("list")
            if isinstance(nested, list):
                return nested
            data = value.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("list"), list):
                return data["list"]
        return []

    @staticmethod
    def _item_name(item):
        return str(
            item.get("fn") or item.get("n") or item.get("file_name")
            or item.get("name") or ""
        ).strip()

    @staticmethod
    def _item_id(item):
        return str(
            item.get("fid") or item.get("cid") or item.get("file_id")
            or item.get("id") or ""
        ).strip()

    @staticmethod
    def _item_is_dir(item):
        if "is_dir" in item:
            return bool(item.get("is_dir"))
        if "file_category" in item:
            return str(item.get("file_category")) == "0"
        if "fc" in item:
            return str(item.get("fc")) != "1"
        return False

    def get_file_tree(self, root_path: str, *, max_depth=8, limit=1000):
        root_path = self._normalize(root_path)
        root = self.get_file_info(root_path)
        if not root:
            raise Open115Error("115 download root is unavailable")
        root_name = PurePosixPath(root_path).name
        if not self._item_is_dir(root):
            return [{
                "name": root_name,
                "relative_path": root_name,
                "path": root_path,
                "is_dir": False,
                "file_id": self._item_id(root),
                "size": root.get("fs") or root.get("size") or root.get("size_byte") or 0,
            }]

        root_id = self._item_id(root)
        if not root_id:
            raise Open115Error("115 download root has no file_id")
        tree = []

        def walk(parent_id, prefix="", depth=0):
            if depth > int(max_depth) or len(tree) >= int(limit):
                return
            response = self.get_file_list({
                "cid": parent_id,
                "limit": int(limit),
                "show_dir": 1,
            })
            for item in self._list_items(response):
                if not isinstance(item, dict) or len(tree) >= int(limit):
                    continue
                name = self._item_name(item)
                if not name:
                    continue
                relative = f"{prefix}/{name}".strip("/")
                is_dir = self._item_is_dir(item)
                node = {
                    "name": name,
                    "relative_path": relative,
                    "path": f"{root_path.rstrip('/')}/{relative}",
                    "is_dir": is_dir,
                    "file_id": self._item_id(item),
                    "size": item.get("fs") or item.get("size") or item.get("size_byte") or 0,
                }
                tree.append(node)
                if is_dir and node["file_id"]:
                    walk(node["file_id"], relative, depth + 1)

        walk(root_id)
        return tree

    @staticmethod
    def _normalize(path: str):
        return "/" + str(path or "").strip("/")
