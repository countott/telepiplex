from __future__ import annotations

import os
import threading
import time
from pathlib import PurePosixPath

import requests


class Open115Error(RuntimeError):
    pass


class Open115Client:
    TOKEN_EXPIRED_CODES = {40140125, 40140126}

    def __init__(self, config: dict, *, session=None):
        self.config = config
        self.base_url = str(config.get("base_url") or "https://proapi.115.com").rstrip("/")
        self.passport_url = str(config.get("passport_url") or "https://passportapi.115.com").rstrip("/")
        self.access_token = str(config.get("access_token") or "")
        self.refresh_token = str(config.get("refresh_token") or "")
        self.timeout = max(1, float(config.get("timeout") or 30))
        self.request_interval = max(0, float(config.get("request_interval") or 1))
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._file_cache = {}

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
    def _normalize(path: str):
        return "/" + str(path or "").strip("/")
