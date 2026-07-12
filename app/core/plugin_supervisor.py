from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.plugin_contract import ContractError
from app.core.plugin_rpc import RpcClient
from app.core.plugin_store import ActiveRelease


class SupervisorError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class PluginHealth:
    plugin_id: str
    state: str
    active_tasks: int
    restart_count: int
    last_error: str = ""


@dataclass(frozen=True)
class DrainResult:
    plugin_id: str
    state: str
    active_tasks: int
    interrupted_task_ids: tuple[str, ...] = ()


@dataclass
class PluginProcess:
    instance_id: str
    plugin_id: str
    release: ActiveRelease
    shadow: bool
    socket_path: Path
    argv: tuple[str, ...]
    startup_token: str = ""
    state: str = "starting"
    restart_count: int = 0
    last_error: str = ""
    logs: list[str] = field(default_factory=list)
    child: object = None
    client: RpcClient | None = None
    monitor_task: asyncio.Task | None = None
    log_tasks: list[asyncio.Task] = field(default_factory=list)
    desired_stop: bool = False

    @property
    def pid(self) -> int | None:
        return self.child.pid if self.child is not None else None


class PluginSupervisor:
    def __init__(
        self,
        *,
        startup_timeout: float = 30,
        restart_limit: int = 3,
        restart_backoff: float = 1,
        max_log_lines: int = 200,
        runtime_root: Path = Path("/tmp/telepiplex"),
    ):
        self.startup_timeout = float(startup_timeout)
        self.restart_limit = max(0, int(restart_limit))
        self.restart_backoff = max(0, float(restart_backoff))
        self.max_log_lines = max(1, int(max_log_lines))
        self.runtime_root = Path(runtime_root).resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._active: dict[str, PluginProcess] = {}
        self._instances: dict[str, PluginProcess] = {}

    async def start(self, release: ActiveRelease, *, shadow: bool = False) -> PluginProcess:
        if not shadow and release.plugin_id in self._active:
            raise SupervisorError("already_running", f"Feature is already running: {release.plugin_id}")
        instance_id = (
            f"{release.plugin_id}@{release.version}-{uuid.uuid4().hex[:8]}"
            if shadow else release.plugin_id
        )
        socket_name = hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:16]
        socket_path = self.runtime_root / f"{socket_name}.sock"
        executable = release.path / "venv/bin/python"
        argv = (str(executable), "-m", "telepiplex_plugin_sdk.runner")
        process = PluginProcess(
            instance_id=instance_id,
            plugin_id=release.plugin_id,
            release=release,
            shadow=bool(shadow),
            socket_path=socket_path,
            argv=argv,
        )
        self._instances[instance_id] = process
        try:
            await self._launch_once(process)
        except Exception as exc:
            await self._terminate_child(process)
            self._instances.pop(instance_id, None)
            raise SupervisorError("startup_failed", self._safe_error(exc)) from None
        if not shadow:
            self._active[release.plugin_id] = process
        process.monitor_task = asyncio.create_task(self._monitor(process, process.child))
        return process

    def promote(self, process: PluginProcess):
        if process.state != "healthy":
            raise SupervisorError("not_healthy", "only a healthy shadow process can be promoted")
        process.shadow = False
        self._active[process.plugin_id] = process

    def process(self, plugin_id: str) -> PluginProcess | None:
        return self._active.get(str(plugin_id))

    def _resolve(self, target: str | PluginProcess) -> PluginProcess:
        if isinstance(target, PluginProcess):
            return target
        process = self.process(str(target))
        if process is None:
            raise SupervisorError("not_running", f"Feature is not running: {target}")
        return process

    async def _launch_once(self, process: PluginProcess):
        executable = Path(process.argv[0])
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise SupervisorError("invalid_runtime", f"Feature Python is not executable: {executable}")
        process.socket_path.unlink(missing_ok=True)
        process.startup_token = secrets.token_urlsafe(32)
        process.state = "starting"
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "TZ", "HOME"}
        }
        environment.update({
            "PYTHONUNBUFFERED": "1",
            "TPX_PLUGIN_ID": process.plugin_id,
            "TPX_PLUGIN_VERSION": process.release.version,
            "TPX_ENTRY_POINT": process.release.manifest.entry_point,
            "TPX_SOCKET_PATH": str(process.socket_path),
            "TPX_CONFIG_PATH": str(process.release.path.parent.parent / "config.yaml"),
            "TPX_STATE_PATH": str(process.release.path.parent.parent / "state"),
            "TPX_STARTUP_TOKEN": process.startup_token,
        })
        process.child = await asyncio.create_subprocess_exec(
            *process.argv,
            cwd=str(process.release.path),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        process.log_tasks = [
            asyncio.create_task(self._capture_logs(process, process.child.stdout, "stdout")),
            asyncio.create_task(self._capture_logs(process, process.child.stderr, "stderr")),
        ]
        process.client = RpcClient(process.socket_path, process.startup_token)

        loop = asyncio.get_running_loop()
        deadline_at = loop.time() + self.startup_timeout
        last_error = "socket not ready"
        while loop.time() < deadline_at:
            if process.child.returncode is not None:
                raise SupervisorError(
                    "process_exited",
                    f"Feature exited during startup with code {process.child.returncode}",
                )
            if process.socket_path.exists():
                remaining = max(0.01, deadline_at - loop.time())
                try:
                    handshake = await process.client.request(
                        "handshake",
                        {},
                        deadline=min(0.25, remaining),
                    )
                    if (
                        handshake.get("plugin_id") != process.plugin_id
                        or handshake.get("version") != process.release.version
                    ):
                        raise SupervisorError("identity_mismatch", "Feature handshake identity mismatch")
                    process.state = "healthy"
                    process.last_error = ""
                    return
                except ContractError as exc:
                    last_error = f"{exc.code}: {exc}"
            await asyncio.sleep(0.01)
        raise SupervisorError("startup_timeout", last_error)

    async def _capture_logs(self, process: PluginProcess, stream, label: str):
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            text = text.replace(process.startup_token, "***redacted***")
            text = re.sub(
                r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+",
                r"\1=***redacted***",
                text,
            )
            process.logs.append(f"{label}: {text}")
            if len(process.logs) > self.max_log_lines:
                del process.logs[:-self.max_log_lines]

    async def _monitor(self, process: PluginProcess, child):
        return_code = await child.wait()
        await asyncio.gather(*process.log_tasks, return_exceptions=True)
        if process.desired_stop or process.child is not child:
            return
        process.last_error = f"Feature exited unexpectedly with code {return_code}"
        process.state = "failed"
        await self._restart(process)

    async def _restart(self, process: PluginProcess):
        while not process.desired_stop and process.restart_count < self.restart_limit:
            process.restart_count += 1
            await asyncio.sleep(self.restart_backoff * (2 ** (process.restart_count - 1)))
            try:
                await self._launch_once(process)
            except Exception as exc:
                process.last_error = self._safe_error(exc)
                await self._terminate_child(process)
                continue
            process.monitor_task = asyncio.create_task(self._monitor(process, process.child))
            return
        process.state = "quarantined"

    async def health(self, target: str | PluginProcess) -> PluginHealth:
        process = self._resolve(target)
        if process.state in {"quarantined", "failed", "stopped"} or process.client is None:
            return PluginHealth(
                process.plugin_id,
                process.state,
                0,
                process.restart_count,
                process.last_error,
            )
        try:
            result = await process.client.request("health", {}, deadline=1)
        except ContractError as exc:
            process.last_error = self._safe_error(exc)
            return PluginHealth(
                process.plugin_id,
                "failed",
                0,
                process.restart_count,
                process.last_error,
            )
        return PluginHealth(
            process.plugin_id,
            str(result.get("state") or process.state),
            int(result.get("active_tasks") or 0),
            process.restart_count,
            process.last_error,
        )

    async def drain(self, target: str | PluginProcess, timeout: float) -> DrainResult:
        process = self._resolve(target)
        if process.client is None:
            raise SupervisorError("unavailable", "Feature RPC client is unavailable")
        try:
            result = await process.client.request("drain", {}, deadline=float(timeout))
        except ContractError as exc:
            raise SupervisorError(exc.code, self._safe_error(exc)) from None
        process.state = "draining"
        return DrainResult(
            plugin_id=process.plugin_id,
            state=str(result.get("state") or "draining"),
            active_tasks=int(result.get("active_tasks") or 0),
            interrupted_task_ids=tuple(
                str(value) for value in result.get("interrupted_task_ids") or []
            ),
        )

    async def stop(self, target: str | PluginProcess, timeout: float = 10):
        process = self._resolve(target)
        process.desired_stop = True
        if process.client is not None and process.child is not None and process.child.returncode is None:
            try:
                await process.client.request("shutdown", {}, deadline=min(float(timeout), 2))
            except ContractError:
                pass
        if process.child is not None and process.child.returncode is None:
            try:
                await asyncio.wait_for(process.child.wait(), timeout=float(timeout))
            except TimeoutError:
                process.child.terminate()
                try:
                    await asyncio.wait_for(process.child.wait(), timeout=1)
                except TimeoutError:
                    process.child.kill()
                    await process.child.wait()
        await asyncio.gather(*process.log_tasks, return_exceptions=True)
        process.socket_path.unlink(missing_ok=True)
        process.state = "stopped"
        if self._active.get(process.plugin_id) is process:
            self._active.pop(process.plugin_id, None)
        self._instances.pop(process.instance_id, None)
        monitor = process.monitor_task
        if monitor is not None and monitor is not asyncio.current_task() and not monitor.done():
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

    async def _terminate_child(self, process: PluginProcess):
        child = process.child
        if child is not None and child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), timeout=1)
            except TimeoutError:
                child.kill()
                await child.wait()
        await asyncio.gather(*process.log_tasks, return_exceptions=True)
        process.socket_path.unlink(missing_ok=True)

    async def close_all(self):
        for process in list(self._instances.values()):
            try:
                await self.stop(process)
            except SupervisorError:
                await self._terminate_child(process)
        self._active.clear()
        self._instances.clear()

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, (SupervisorError, ContractError)):
            return f"{exc.code}: {exc}"
        return type(exc).__name__
