from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from pathlib import PurePosixPath

import requests


class Open115Error(RuntimeError):
    def __init__(self, message: str, *, code="", operation=""):
        super().__init__(message)
        self.code = str(code or "")
        self.operation = str(operation or "")


class Open115Client:
    TOKEN_EXPIRED_CODES = {40140125, 40140126}
    _MAGNET_HASH = re.compile(
        r"(?:^|[?&])xt=urn:btih:([^&]+)",
        re.IGNORECASE,
    )

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
            raise Open115Error(
                "115 access_token and refresh_token are required",
                code="missing_token",
                operation="set_tokens",
            )
        self.access_token = access_token
        self.refresh_token = refresh_token

    def _headers(self):
        if not self.access_token:
            raise Open115Error(
                "115 access_token is not configured",
                code="missing_access_token",
                operation="request",
            )
        return {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "telepiplex-Feature/1.0",
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
            raise Open115Error(
                f"115 request failed: {type(exc).__name__}",
                operation=path,
            ) from exc
        if not isinstance(result, dict):
            raise Open115Error(
                "115 returned a non-object response",
                code="invalid_response",
                operation=path,
            )
        if retry and result.get("code") in self.TOKEN_EXPIRED_CODES:
            self.refresh_access_token()
            return self._request(method, path, params=params, data=data, retry=False)
        return result

    def refresh_access_token(self):
        if not self.refresh_token:
            raise Open115Error(
                "115 refresh_token is not configured",
                code="missing_refresh_token",
                operation="refresh_access_token",
            )
        try:
            response = self.session.post(
                f"{self.passport_url}/open/refreshToken",
                headers={"User-Agent": "telepiplex-Feature/1.0"},
                data={"refresh_token": self.refresh_token},
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise Open115Error(
                f"115 token refresh failed: {type(exc).__name__}",
                operation="refresh_access_token",
            ) from exc
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict) or not data.get("access_token"):
            raise Open115Error(
                "115 token refresh returned invalid data",
                code=str(result.get("code") or "") if isinstance(result, dict) else "",
                operation="refresh_access_token",
            )
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
                headers={"User-Agent": "telepiplex-Feature/1.0"},
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
        cancel_event=None,
    ):
        deadline = time.monotonic() + max(float(timeout), 1)
        params = {
            "uid": authorization["uid"],
            "time": authorization["time"],
            "sign": authorization["sign"],
        }
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise Open115Error("115 device authorization cancelled")
            try:
                response = self.session.get(
                    "https://qrcodeapi.115.com/get/status/",
                    headers={"User-Agent": "telepiplex-Feature/1.0"},
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
            delay = max(float(poll_interval), 0.1)
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    raise Open115Error("115 device authorization cancelled")
            else:
                time.sleep(delay)
        else:
            raise Open115Error("115 device authorization timed out")

        try:
            response = self.session.post(
                f"{self.passport_url}/open/deviceCodeToToken",
                headers={"User-Agent": "telepiplex-Feature/1.0"},
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
                raise Open115Error(
                    f"cannot create 115 directory: {current_path}",
                    operation="create_directory",
                )
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
            raise Open115Error(
                str(result.get("message") or "cannot add offline task"),
                code=str(result.get("code") or ""),
                operation="add_offline_task",
            )
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

    @staticmethod
    def _normalize_info_hash(value):
        value = str(value or "").strip()
        if re.fullmatch(r"[A-Fa-f0-9]{40}", value):
            return value.lower()
        if re.fullmatch(r"[A-Za-z2-7]{32}", value):
            try:
                return base64.b32decode(value.upper()).hex()
            except ValueError:
                return ""
        return ""

    @classmethod
    def _magnet_info_hash(cls, link):
        matched = cls._MAGNET_HASH.search(str(link or "").strip())
        if not matched:
            return ""
        return cls._normalize_info_hash(matched.group(1))

    @classmethod
    def _offline_task_matches(cls, task, link):
        if not isinstance(task, dict):
            return False
        requested_hash = cls._magnet_info_hash(link)
        if not requested_hash:
            return str(task.get("url") or "").strip() == str(link or "").strip()
        task_hashes = {
            cls._normalize_info_hash(task.get("info_hash")),
            cls._magnet_info_hash(task.get("url")),
        }
        task_hashes.discard("")
        return requested_hash in task_hashes

    def _offline_task_output_available(self, task, save_path):
        save_path = str(save_path or "").rstrip("/")
        if not save_path:
            return True
        try:
            status = int(task.get("status"))
        except (TypeError, ValueError):
            status = task.get("status")
        try:
            progress = float(task.get("percentDone") or 0)
        except (TypeError, ValueError):
            progress = 0
        if status != 2 and progress < 100:
            return True
        resource_name = str(task.get("name") or "").strip("/")
        if not resource_name:
            return False
        return self.get_file_info(f"{save_path}/{resource_name}") is not None

    def find_offline_task(self, link: str, save_path=""):
        tasks = self.get_offline_tasks()
        exact_link = str(link or "").strip()
        for task in tasks:
            if (
                isinstance(task, dict)
                and str(task.get("url") or "").strip() == exact_link
                and self._offline_task_output_available(task, save_path)
            ):
                return task
        for task in tasks:
            if (
                self._offline_task_matches(task, link)
                and self._offline_task_output_available(task, save_path)
            ):
                return task
        return None

    def wait_for_download(
        self,
        link: str,
        *,
        existing_task=None,
        timeout: float,
        poll_interval: float,
        cancel_event=None,
        progress_callback=None,
    ):
        deadline = time.monotonic() + float(timeout)
        last = {"name": "", "info_hash": "", "percentDone": 0}
        bound_hash = ""
        if isinstance(existing_task, dict):
            bound_hash = (
                self._normalize_info_hash(existing_task.get("info_hash"))
                or self._magnet_info_hash(existing_task.get("url"))
            )
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise Open115Error("115 download cancelled")
            for task in self.get_offline_tasks():
                if bound_hash:
                    task_hashes = {
                        self._normalize_info_hash(task.get("info_hash")),
                        self._magnet_info_hash(task.get("url")),
                    }
                    task_hashes.discard("")
                    matched = bound_hash in task_hashes
                else:
                    matched = self._offline_task_matches(task, link)
                if not matched:
                    continue
                last = task
                progress = float(task.get("percentDone") or 0)
                try:
                    task_status = int(task.get("status"))
                except (TypeError, ValueError):
                    task_status = task.get("status")
                if progress_callback is not None:
                    progress_callback({
                        "resource_name": str(task.get("name") or ""),
                        "info_hash": str(task.get("info_hash") or ""),
                        "progress": progress,
                        "task_status": task_status,
                    })
                if task_status == 2 or progress >= 100:
                    return {
                        "resource_name": str(task.get("name") or ""),
                        "info_hash": str(task.get("info_hash") or ""),
                        "progress": 100,
                    }
                break
            delay = max(float(poll_interval), 0.01)
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    raise Open115Error("115 download cancelled")
            else:
                time.sleep(delay)
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
        return self.move_file_detailed(source_path, target_path)["state"] in {
            "moved",
            "no_op",
        }

    def move_file_detailed(self, source_path: str, target_path: str):
        normalized_source = str(PurePosixPath(str(source_path)))
        normalized_target_dir = str(PurePosixPath(str(target_path)))
        target = str(
            PurePosixPath(normalized_target_dir) / PurePosixPath(normalized_source).name
        )
        if normalized_source == target:
            return {
                "state": "no_op",
                "copied": False,
                "source_deleted": False,
                "source_path": source_path,
                "target_path": target,
            }
        self.create_dir_recursive(target_path)
        if not self.copy_file(source_path, target_path):
            return {"state": "copy_failed", "copied": False, "source_deleted": False,
                    "source_path": source_path, "target_path": target}
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
