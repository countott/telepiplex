from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import hashlib
import math
import re
import secrets
import threading
import time
from pathlib import PurePosixPath

import requests

from .pacing import EndpointPacer
from telepiplex_plugin_sdk.storage_tree import TreeIntegrityError, collect_complete_tree


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

    _ENDPOINT_CLASSES = {
        ("GET", "/open/offline/get_task_list"): "offline.poll",
        ("POST", "/open/offline/add_task_urls"): "offline.mutation",
        ("POST", "/open/offline/del_task"): "offline.mutation",
        ("GET", "/open/folder/get_info"): "storage.read",
        ("GET", "/open/ufile/files"): "storage.read",
        ("POST", "/open/folder/add"): "storage.mutation",
        ("POST", "/open/ufile/update"): "storage.mutation",
        ("POST", "/open/ufile/copy"): "storage.mutation",
        ("POST", "/open/ufile/delete"): "storage.mutation",
        ("POST", "/open/ufile/move"): "storage.mutation",
    }
    _MUTATION_CLASSES = {
        "offline.mutation",
        "storage.mutation",
        "token.refresh",
    }
    _OPERATIONS = {
        ("GET", "/open/offline/get_task_list"): "get_offline_tasks",
        ("POST", "/open/offline/add_task_urls"): "add_offline_task",
        ("POST", "/open/offline/del_task"): "delete_offline_task",
        ("GET", "/open/folder/get_info"): "get_file_info",
        ("GET", "/open/ufile/files"): "get_file_list",
        ("POST", "/open/folder/add"): "create_directory",
        ("POST", "/open/ufile/update"): "rename_file",
        ("POST", "/open/ufile/copy"): "move_files",
        ("POST", "/open/ufile/delete"): "delete_file",
        ("POST", "/open/ufile/move"): "move_files",
    }

    def __init__(
        self,
        config: dict,
        *,
        session=None,
        on_tokens_changed=None,
        pacer=None,
        on_observation=None,
    ):
        self.config = config
        self.base_url = str(config.get("base_url") or "https://proapi.115.com").rstrip("/")
        self.passport_url = str(config.get("passport_url") or "https://passportapi.115.com").rstrip("/")
        self.access_token = str(config.get("access_token") or "")
        self.refresh_token = str(config.get("refresh_token") or "")
        self.timeout = max(1, float(config.get("timeout") or 30))
        self.request_interval = max(0, float(config.get("request_interval") or 1))
        self.session = session or requests.Session()
        self.on_tokens_changed = on_tokens_changed
        self.on_observation = on_observation
        endpoint_intervals = config.get("endpoint_intervals")
        if not isinstance(endpoint_intervals, dict):
            legacy_interval = config.get("request_interval")
            if legacy_interval is None:
                endpoint_intervals = {}
            else:
                endpoint_intervals = {
                    "offline_poll": legacy_interval,
                    "offline_mutation": legacy_interval,
                    "storage_read": legacy_interval,
                    "storage_mutation": legacy_interval,
                    "token_refresh": legacy_interval,
                }
        self._pacer = pacer or EndpointPacer(endpoint_intervals)
        self._mutation_guard = threading.Lock()
        try:
            configured_workers = int(config.get("storage_read_workers", 4))
        except (TypeError, ValueError):
            configured_workers = 4
        self.storage_read_workers = min(4, max(1, configured_workers))
        self._storage_read_slots = threading.BoundedSemaphore(
            self.storage_read_workers
        )
        self._cache_lock = threading.Lock()
        self._cache_condition = threading.Condition(self._cache_lock)
        self._cache_generation = 0
        self._storage_mutation_active = False
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

    def _request(
        self,
        method: str,
        path: str,
        *,
        params=None,
        data=None,
        files=None,
        retry=True,
    ):
        endpoint_class = self._classify_endpoint(method, path)
        operation = self._operation_for_endpoint(method, path)
        pacer_wait_ms = 0
        http_started = None
        status_class = "unknown"
        try:
            if endpoint_class in self._MUTATION_CLASSES:
                guard = self._mutation_guard
            elif endpoint_class == "storage.read":
                guard = self._storage_read_slots
            else:
                guard = nullcontext()
            with guard:
                pacer_wait_ms = round(
                    max(0.0, float(self._pacer.acquire(endpoint_class))) * 1000
                )
                if pacer_wait_ms >= 50:
                    self._emit_observation(
                        "download.pacing.waited",
                        endpoint_class=endpoint_class,
                        operation=operation,
                        pacer_wait_ms=pacer_wait_ms,
                    )
                storage_mutation = endpoint_class == "storage.mutation"
                if storage_mutation:
                    self._begin_storage_mutation()
                result = None
                request_completed = False
                try:
                    http_started = time.monotonic()
                    response = self.session.request(
                        method,
                        f"{self.base_url}{path}",
                        headers=self._headers(),
                        params=params,
                        data=data,
                        files=files,
                        timeout=self.timeout,
                    )
                    status_class = self._status_class(response)
                    self._observe_http_throttle(
                        endpoint_class,
                        response,
                        operation=operation,
                    )
                    response.raise_for_status()
                    result = response.json()
                    request_completed = True
                finally:
                    if storage_mutation:
                        token_expired = (
                            isinstance(result, dict)
                            and result.get("code") in self.TOKEN_EXPIRED_CODES
                        )
                        self._finish_storage_mutation(
                            request_completed
                            and not token_expired
                            and self._successful(result)
                        )
        except (requests.RequestException, ValueError) as exc:
            self._emit_observation(
                "download.request.failed",
                endpoint_class=endpoint_class,
                operation=operation,
                pacer_wait_ms=pacer_wait_ms,
                http_elapsed_ms=self._elapsed_ms(http_started),
                status_class=status_class,
                retryable=self._status_is_retryable(status_class),
            )
            raise Open115Error(
                f"115 request failed: {type(exc).__name__}",
                operation=path,
            ) from exc
        if not isinstance(result, dict):
            self._emit_observation(
                "download.request.failed",
                endpoint_class=endpoint_class,
                operation=operation,
                pacer_wait_ms=pacer_wait_ms,
                http_elapsed_ms=self._elapsed_ms(http_started),
                status_class=status_class,
                retryable=False,
            )
            raise Open115Error(
                "115 returned a non-object response",
                code="invalid_response",
                operation=path,
            )
        self._emit_observation(
            "download.request.completed",
            endpoint_class=endpoint_class,
            operation=operation,
            pacer_wait_ms=pacer_wait_ms,
            http_elapsed_ms=self._elapsed_ms(http_started),
            status_class=status_class,
            retryable=False,
        )
        if retry and result.get("code") in self.TOKEN_EXPIRED_CODES:
            self.refresh_access_token()
            return self._request(
                method,
                path,
                params=params,
                data=data,
                files=files,
                retry=False,
            )
        return result

    @classmethod
    def _operation_for_endpoint(cls, method: str, path: str) -> str:
        return cls._OPERATIONS[(str(method or "").upper(), str(path or ""))]

    def _emit_observation(self, event_name: str, **facts) -> None:
        if not callable(self.on_observation):
            return
        try:
            self.on_observation(event_name, dict(facts))
        except Exception:
            pass

    @staticmethod
    def _elapsed_ms(started: float | None) -> int:
        if started is None:
            return 0
        return max(0, round((time.monotonic() - started) * 1000))

    @staticmethod
    def _status_class(response) -> str:
        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status_code = 0
        if 200 <= status_code < 300:
            return "2xx"
        if 400 <= status_code < 500:
            return "4xx"
        if 500 <= status_code < 600:
            return "5xx"
        return "unknown"

    @staticmethod
    def _status_is_retryable(status_class: str) -> bool:
        return status_class in {"4xx", "5xx"}

    def _classify_endpoint(self, method: str, path: str) -> str:
        key = (str(method or "").upper(), str(path or ""))
        endpoint_class = self._ENDPOINT_CLASSES.get(key)
        if endpoint_class is None:
            raise Open115Error(
                f"115 endpoint is not classified: {key[0]} {key[1]}",
                code="unclassified_endpoint",
                operation=key[1],
            )
        return endpoint_class

    def _observe_http_throttle(self, endpoint_class: str, response, *, operation="") -> None:
        if int(getattr(response, "status_code", 0) or 0) != 429:
            return 0.0
        headers = getattr(response, "headers", {})
        retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
        cooldown = self._pacer.observe_throttle(endpoint_class, retry_after)
        self._emit_observation(
            "download.pacing.throttled",
            endpoint_class=endpoint_class,
            operation=operation,
            status_class="4xx",
            retryable=True,
            cooldown_ms=max(0, round(float(cooldown) * 1000)),
        )
        return cooldown

    def _passport_post(self, path: str, *, data: dict):
        with self._mutation_guard:
            pacer_wait_ms = round(
                max(0.0, float(self._pacer.acquire("token.refresh"))) * 1000
            )
            if pacer_wait_ms >= 50:
                self._emit_observation(
                    "download.pacing.waited",
                    endpoint_class="token.refresh",
                    operation="refresh_access_token",
                    pacer_wait_ms=pacer_wait_ms,
                )
            started = time.monotonic()
            status_class = "unknown"
            try:
                response = self.session.post(
                    f"{self.passport_url}{path}",
                    headers={"User-Agent": "telepiplex-Feature/1.0"},
                    data=data,
                    timeout=self.timeout,
                )
                status_class = self._status_class(response)
                self._observe_http_throttle(
                    "token.refresh",
                    response,
                    operation="refresh_access_token",
                )
                response.raise_for_status()
                result = response.json()
            except (requests.RequestException, ValueError):
                self._emit_observation(
                    "download.request.failed",
                    endpoint_class="token.refresh",
                    operation="refresh_access_token",
                    pacer_wait_ms=pacer_wait_ms,
                    http_elapsed_ms=self._elapsed_ms(started),
                    status_class=status_class,
                    retryable=self._status_is_retryable(status_class),
                )
                raise
            self._emit_observation(
                "download.request.completed",
                endpoint_class="token.refresh",
                operation="refresh_access_token",
                pacer_wait_ms=pacer_wait_ms,
                http_elapsed_ms=self._elapsed_ms(started),
                status_class=status_class,
                retryable=False,
            )
            return result

    def refresh_access_token(self):
        if not self.refresh_token:
            raise Open115Error(
                "115 refresh_token is not configured",
                code="missing_refresh_token",
                operation="refresh_access_token",
            )
        try:
            result = self._passport_post(
                "/open/refreshToken",
                data={"refresh_token": self.refresh_token},
            )
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
            result = self._passport_post(
                "/open/authDeviceCode",
                data={
                    "client_id": app_id,
                    "code_challenge": challenge,
                    "code_challenge_method": "sha256",
                },
            )
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
            result = self._passport_post(
                "/open/deviceCodeToToken",
                data={
                    "uid": authorization["uid"],
                    "code_verifier": authorization["code_verifier"],
                },
            )
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

    def _begin_storage_mutation(self) -> None:
        with self._cache_condition:
            while self._storage_mutation_active:
                self._cache_condition.wait()
            self._storage_mutation_active = True

    def _finish_storage_mutation(self, successful: bool) -> None:
        with self._cache_condition:
            if successful:
                self._cache_generation += 1
                self._file_cache.clear()
            self._storage_mutation_active = False
            self._cache_condition.notify_all()

    def _wait_for_storage_mutation(self) -> None:
        while self._storage_mutation_active:
            self._cache_condition.wait()

    def _remove_cached_file(self, path: str) -> None:
        with self._cache_condition:
            self._wait_for_storage_mutation()
            self._file_cache.pop(path, None)

    @staticmethod
    def _has_stable_file_identity(value) -> bool:
        if not isinstance(value, dict):
            return False
        for key in ("file_id", "fid", "cid", "id"):
            identity = value.get(key)
            if isinstance(identity, bool):
                continue
            if isinstance(identity, (str, int)) and str(identity).strip():
                return True
        return False

    def get_file_info(self, path: str):
        path = self._normalize(path)
        with self._cache_condition:
            self._wait_for_storage_mutation()
            if path in self._file_cache:
                return self._file_cache[path]
            generation = self._cache_generation
        result = self._request("GET", "/open/folder/get_info", params={"path": path})
        value = result.get("data") if self._successful(result) else None
        if not self._has_stable_file_identity(value):
            return None
        with self._cache_condition:
            self._wait_for_storage_mutation()
            if generation == self._cache_generation:
                self._file_cache[path] = value
        return value

    def get_file_info_batch(self, paths: list[str]):
        if not isinstance(paths, list) or len(paths) > 32:
            raise ValueError("file info batch exceeds 32 paths")
        normalized = []
        seen = set()
        for path in paths:
            value = self._normalize(path)
            if value and value not in seen:
                seen.add(value)
                normalized.append(value)
        values = {}
        with self._cache_condition:
            self._wait_for_storage_mutation()
            for path in normalized:
                if path in self._file_cache:
                    values[path] = self._file_cache[path]
        missing = [path for path in normalized if path not in values]

        def fetch(path):
            try:
                return self.get_file_info(path)
            except Exception:
                return None

        if missing:
            worker_count = min(self.storage_read_workers, len(missing))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                fetched = executor.map(fetch, missing)
                values.update(zip(missing, fetched))
        return {path: values.get(path) for path in normalized}

    def get_file_info_by_id(self, file_id: str):
        result = self._request("GET", "/open/folder/get_info", params={"file_id": file_id})
        value = result.get("data") if self._successful(result) else None
        return value if self._has_stable_file_identity(value) else None

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
            self._remove_cached_file(current_path)
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
        poll_interval: float | None = None,
        poll_initial_interval: float | None = None,
        poll_max_interval: float = 30,
        poll_backoff_factor: float = 1.7,
        cancel_event=None,
        progress_callback=None,
    ):
        if poll_initial_interval is None:
            poll_initial_interval = (
                poll_interval if poll_interval is not None else 2.0
            )
        try:
            initial_interval = float(poll_initial_interval)
        except (TypeError, ValueError):
            initial_interval = 2.0
        if not math.isfinite(initial_interval) or initial_interval <= 0:
            initial_interval = 2.0
        try:
            maximum_interval = float(poll_max_interval)
        except (TypeError, ValueError):
            maximum_interval = 30.0
        if not math.isfinite(maximum_interval):
            maximum_interval = 30.0
        maximum_interval = max(initial_interval, maximum_interval)
        try:
            backoff_factor = float(poll_backoff_factor)
        except (TypeError, ValueError):
            backoff_factor = 1.7
        if not math.isfinite(backoff_factor):
            backoff_factor = 1.7
        backoff_factor = max(1.0, backoff_factor)

        deadline = time.monotonic() + float(timeout)
        last = {"name": "", "info_hash": "", "percentDone": 0}
        unobserved = object()
        previous_snapshot = unobserved
        next_delay = initial_interval
        bound_hash = ""
        if isinstance(existing_task, dict):
            bound_hash = (
                self._normalize_info_hash(existing_task.get("info_hash"))
                or self._magnet_info_hash(existing_task.get("url"))
            )
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise Open115Error("115 download cancelled")
            current_snapshot = None
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
                try:
                    progress = float(task.get("percentDone") or 0)
                except (TypeError, ValueError):
                    progress = 0.0
                if not math.isfinite(progress):
                    progress = 0.0
                try:
                    task_status = int(task.get("status"))
                except (TypeError, ValueError):
                    task_status = str(task.get("status") or "")
                raw_info_hash = str(task.get("info_hash") or "").strip()
                current_snapshot = (
                    self._normalize_info_hash(raw_info_hash)
                    or raw_info_hash.lower(),
                    str(task.get("name") or "").strip(),
                    task_status,
                    progress,
                )
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
            if previous_snapshot is unobserved or current_snapshot != previous_snapshot:
                next_delay = initial_interval
            else:
                prior_delay = next_delay
                next_delay = min(
                    next_delay * backoff_factor,
                    maximum_interval,
                )
                if next_delay != prior_delay:
                    self._emit_observation(
                        "download.poll.backoff_changed",
                        operation="wait_for_download",
                        previous_delay_ms=round(prior_delay * 1000),
                        next_delay_ms=round(next_delay * 1000),
                    )
            previous_snapshot = current_snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            delay = min(next_delay, remaining)
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
        if self._successful(result):
            return True
        return False

    def delete_single_file(self, path: str):
        info = self.get_file_info(path)
        if not info:
            return False
        result = self._request(
            "POST", "/open/ufile/delete", data={"file_ids": info["file_id"]}
        )
        if self._successful(result):
            return True
        return False

    def move_file(self, source_path: str, target_path: str):
        return self.move_file_detailed(source_path, target_path)["state"] in {
            "moved",
            "no_op",
        }

    def move_files_by_id(self, file_ids: list[str], target_dir_id: str):
        unique_ids = []
        seen = set()
        for file_id in file_ids if isinstance(file_ids, list) else ():
            value = str(file_id or "").strip()
            if value and value not in seen:
                seen.add(value)
                unique_ids.append(value)
        if not 1 <= len(unique_ids) <= 100:
            raise ValueError("native move requires 1 through 100 unique file IDs")
        target_dir_id = str(target_dir_id or "").strip()
        if not target_dir_id:
            raise ValueError("native move target directory ID is required")
        result = self._request(
            "POST",
            "/open/ufile/move",
            files={
                "file_ids": (None, ",".join(unique_ids)),
                "to_cid": (None, target_dir_id),
            },
        )
        submitted = self._successful(result)
        return {
            "state": "submitted" if submitted else "provider_rejected",
            "submitted": submitted,
            "file_ids": unique_ids,
            "target_dir_id": target_dir_id,
            "provider_code": str(
                result.get("code") if result.get("code") is not None else ""
            ),
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

    @staticmethod
    def _item_sha1(item):
        return str(
            item.get("sha1") or item.get("sha") or item.get("file_sha1") or ""
        ).strip()

    def get_file_tree(self, root_path: str, *, max_depth=8, limit=1000):
        try:
            return collect_complete_tree(self, root_path, max_depth=max_depth, limit=limit)
        except TreeIntegrityError as exc:
            raise Open115Error(str(exc), code="file_tree_incomplete", operation="get_file_tree") from exc

    @staticmethod
    def _normalize(path: str):
        return "/" + str(path or "").strip("/")
