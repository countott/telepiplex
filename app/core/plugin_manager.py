from __future__ import annotations

import asyncio
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.core.capability_router import CapabilityRouter, RoutingError
from app.core.event_journal import EventJournal
from app.core.plugin_artifact import ArtifactError, verify_tpx
from app.core.plugin_catalog import CatalogError, ResolvedArtifact
from app.core.plugin_contract import CORE_API_VERSION
from app.core.plugin_store import ActiveRelease, PluginStore, StagedRelease, StoreError
from app.core.plugin_supervisor import (
    PluginProcess,
    PluginSupervisor,
    RoutedPluginClient,
    SupervisorError,
)


class PluginOperationError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})


@dataclass(frozen=True)
class PluginOperationResult:
    state: str
    plugin_id: str
    version: str
    message: str
    details: dict = field(default_factory=dict)


class PluginManager:
    def __init__(
        self,
        *,
        store: PluginStore,
        supervisor: PluginSupervisor,
        router: CapabilityRouter,
        journal: EventJournal,
        venv_installer=None,
        artifact_resolver=None,
        broker=None,
        core_api_version: str = CORE_API_VERSION,
        install_timeout: float = 300,
        drain_timeout: float = 120,
        stabilize_seconds: float = 10,
    ):
        self.store = store
        self.supervisor = supervisor
        self.router = router
        self.journal = journal
        self.broker = broker
        self.core_api_version = str(core_api_version)
        self.install_timeout = float(install_timeout)
        self.drain_timeout = float(drain_timeout)
        self.stabilize_seconds = max(0, float(stabilize_seconds))
        self._venv_installer = venv_installer or self._install_private_venv
        self._artifact_resolver = artifact_resolver
        self._lifecycle_lock = asyncio.Lock()

    async def install(self, reference: str | Path, expected_sha256: str = "") -> PluginOperationResult:
        async with self._lifecycle_lock:
            artifact_path, pinned_sha256 = await self._resolve_artifact(reference)
            verified = await asyncio.to_thread(
                self._verify, artifact_path, expected_sha256 or pinned_sha256
            )
            if self.store.active(verified.manifest.plugin_id) is not None:
                raise PluginOperationError(
                    "already_installed",
                    f"Feature is already installed: {verified.manifest.plugin_id}",
                )
            release = await self._prepare_release(verified)
            active = await self._activate_release(release, None, None)
            return self._result("active", active, "Feature installed and active")

    async def update(self, reference: str | Path, expected_sha256: str = "") -> PluginOperationResult:
        async with self._lifecycle_lock:
            artifact_path, pinned_sha256 = await self._resolve_artifact(reference)
            verified = await asyncio.to_thread(
                self._verify, artifact_path, expected_sha256 or pinned_sha256
            )
            old_release = self.store.active(verified.manifest.plugin_id)
            if old_release is None:
                raise PluginOperationError("not_installed", "Feature is not installed")
            if old_release.version == verified.manifest.version:
                raise PluginOperationError("same_version", "Feature version is already active")
            release = await self._prepare_release(verified)
            old_process = self.supervisor.process(old_release.plugin_id)
            active = await self._activate_release(release, old_release, old_process)
            return self._result("active", active, "Feature updated")

    async def rollback(self, plugin_id: str) -> PluginOperationResult:
        async with self._lifecycle_lock:
            current = self.store.active(plugin_id)
            if current is None or not current.previous_version:
                raise PluginOperationError("rollback_unavailable", "No rollback release is recorded")
            target = self.store.release(plugin_id, current.previous_version)
            if target is None:
                raise PluginOperationError("rollback_unavailable", "Rollback release is missing")
            old_process = self.supervisor.process(plugin_id)
            active = await self._activate_release(target, current, old_process)
            return self._result("active", active, "Feature rolled back")

    async def disable(self, plugin_id: str) -> PluginOperationResult:
        async with self._lifecycle_lock:
            release = self.store.active(plugin_id)
            if release is None:
                raise PluginOperationError("not_installed", "Feature is not installed")
            process = self.supervisor.process(plugin_id)
            if process is not None:
                drained = await self.supervisor.drain(process, timeout=self.drain_timeout)
                if drained.active_tasks:
                    await self.supervisor.resume(process)
                    raise PluginOperationError(
                        "drain_timeout",
                        "Feature still has active work; disable was cancelled",
                        {"active_task_ids": list(drained.interrupted_task_ids)},
                    )
            self.router.deactivate(plugin_id)
            self.journal.set_subscriptions(plugin_id, [])
            disabled = self.store.set_enabled(plugin_id, False)
            if process is not None:
                await self.supervisor.stop(process)
            return self._result("disabled", disabled, "Feature disabled")

    async def enable(self, plugin_id: str) -> PluginOperationResult:
        async with self._lifecycle_lock:
            release = self.store.active(plugin_id)
            if release is None:
                raise PluginOperationError("not_installed", "Feature is not installed")
            if release.enabled and self.supervisor.process(plugin_id) is not None:
                return self._result("active", release, "Feature is already active")
            process = None
            try:
                process = await self.supervisor.start(release, shadow=True)
                prepared = self.router.prepare_activation(
                    plugin_id,
                    release.manifest,
                    self._route_client(process),
                )
                enabled = self.store.set_active(
                    release,
                    previous_version=release.previous_version,
                    enabled=True,
                )
                self.router.commit(prepared)
                self.supervisor.promote(process)
                await self._verify_stable(process)
                self.journal.set_subscriptions(plugin_id, release.manifest.subscribes)
                return self._result("active", enabled, "Feature enabled")
            except Exception as exc:
                self.router.deactivate(plugin_id)
                self.store.set_enabled(plugin_id, False)
                if process is not None:
                    await self._safe_stop(process)
                raise self._operation_error(exc, "enable_failed") from None

    async def remove(self, plugin_id: str) -> PluginOperationResult:
        async with self._lifecycle_lock:
            release = self.store.active(plugin_id)
            if release is None:
                raise PluginOperationError("not_installed", "Feature is not installed")
            dependents = self.router.dependents(plugin_id)
            if dependents:
                raise PluginOperationError(
                    "required_by_plugin",
                    f"Feature is required by: {', '.join(dependents)}",
                    {"dependents": list(dependents)},
                )
            process = self.supervisor.process(plugin_id)
            if process is not None:
                drained = await self.supervisor.drain(process, timeout=self.drain_timeout)
                if drained.active_tasks:
                    await self.supervisor.resume(process)
                    raise PluginOperationError(
                        "drain_timeout",
                        "Feature still has active work; removal was cancelled",
                        {"active_task_ids": list(drained.interrupted_task_ids)},
                    )
            self.router.deactivate(plugin_id)
            self.journal.set_subscriptions(plugin_id, [])
            if process is not None:
                await self.supervisor.stop(process)
            await asyncio.to_thread(self.store.remove_plugin, plugin_id)
            return PluginOperationResult(
                state="removed",
                plugin_id=plugin_id,
                version=release.version,
                message="Feature removed",
            )

    async def restore_active(self) -> list[PluginOperationResult]:
        results = []
        pending = {
            item.plugin_id: self.store.active(item.plugin_id)
            for item in self.store.list_installed()
            if item.active
        }
        pending = {
            plugin_id: release
            for plugin_id, release in pending.items()
            if release is not None and release.enabled
        }
        while pending:
            available = set(self.router.snapshot.capabilities)
            ready = [
                plugin_id for plugin_id, release in pending.items()
                if set(release.manifest.requires).issubset(available)
            ]
            if not ready:
                for plugin_id, release in sorted(pending.items()):
                    missing = sorted(set(release.manifest.requires) - available)
                    results.append(PluginOperationResult(
                        state="quarantined", plugin_id=plugin_id,
                        version=release.version,
                        message=f"missing required capabilities: {', '.join(missing)}",
                        details={"code": "missing_capability", "missing": missing},
                    ))
                break
            for plugin_id in sorted(ready):
                release = pending.pop(plugin_id)
                try:
                    results.append(await self.enable(plugin_id))
                except PluginOperationError as exc:
                    self.store.set_enabled(plugin_id, True)
                    results.append(PluginOperationResult(
                        state="quarantined", plugin_id=plugin_id,
                        version=release.version, message=str(exc),
                        details={"code": exc.code},
                    ))
        return results

    async def start(self) -> list[PluginOperationResult]:
        if self.broker is not None:
            await self.broker.start()
        return await self.restore_active()

    def status(self, plugin_id: str) -> dict:
        release = self.store.active(plugin_id)
        if release is None:
            return {"plugin_id": plugin_id, "state": "absent"}
        process = self.supervisor.process(plugin_id)
        route_status = self.router.plugin_status(plugin_id)
        return {
            "plugin_id": plugin_id,
            "version": release.version,
            "previous_version": release.previous_version,
            "enabled": release.enabled,
            "state": process.state if process is not None else (
                route_status["state"] if release.enabled else "disabled"
            ),
            "source_commit": release.manifest.source.commit,
            "provides": [item.name for item in release.manifest.provides],
            "requires": list(release.manifest.requires),
            "missing_capabilities": route_status.get("missing_capabilities", []),
            "pending_events": len(self.journal.pending(plugin_id)),
            "dead_letter_events": len(self.journal.dead_letters(plugin_id)),
        }

    def doctor(self) -> list[dict]:
        return [self.status(plugin_id) for plugin_id in sorted({
            item.plugin_id for item in self.store.list_installed()
        })]

    async def close(self):
        await self.supervisor.close_all()
        if self.broker is not None:
            await self.broker.close()
        self.journal.close()

    async def _resolve_artifact(self, reference: str | Path) -> tuple[Path, str]:
        if self._artifact_resolver is None:
            return Path(reference), ""
        try:
            resolved = await self._artifact_resolver.resolve(reference)
        except CatalogError as exc:
            raise PluginOperationError(exc.code, self._sanitize(str(exc))) from None
        if not isinstance(resolved, ResolvedArtifact):
            raise PluginOperationError("resolver_failed", "Artifact resolver returned an invalid result")
        return resolved.path, resolved.expected_sha256

    def _verify(self, artifact_path: Path, expected_sha256: str):
        try:
            verified = verify_tpx(artifact_path, expected_sha256)
        except ArtifactError as exc:
            raise PluginOperationError(exc.code, self._sanitize(str(exc))) from None
        if not verified.manifest.supports_core(self.core_api_version):
            raise PluginOperationError(
                "incompatible_core",
                f"Feature requires core API {verified.manifest.core_api}; current is {self.core_api_version}",
            )
        return verified

    async def _prepare_release(self, verified) -> ActiveRelease:
        staged = None
        try:
            staged = await asyncio.to_thread(self.store.stage, verified)
            await self._venv_installer(staged)
            return await asyncio.to_thread(self.store.commit, staged)
        except Exception as exc:
            if staged is not None and staged.path.exists():
                await asyncio.to_thread(self.store.discard, staged)
            raise self._operation_error(exc, "install_failed") from None

    async def _activate_release(
        self,
        release: ActiveRelease,
        old_release: ActiveRelease | None,
        old_process: PluginProcess | None,
    ) -> ActiveRelease:
        new_process = None
        route_committed = False
        old_drained = False
        try:
            new_process = await self.supervisor.start(release, shadow=True)
            prepared = self.router.prepare_activation(
                release.plugin_id,
                release.manifest,
                self._route_client(new_process),
            )
            if old_process is not None:
                drained = await self.supervisor.drain(old_process, timeout=self.drain_timeout)
                old_drained = True
                if drained.active_tasks:
                    raise PluginOperationError(
                        "drain_timeout",
                        "Feature still has active work; update was cancelled",
                        {"active_task_ids": list(drained.interrupted_task_ids)},
                    )
            active = self.store.set_active(
                release,
                previous_version=old_release.version if old_release else None,
                enabled=True,
            )
            self.router.commit(prepared)
            route_committed = True
            self.supervisor.promote(new_process)
            await self._verify_stable(new_process)
            self.journal.set_subscriptions(release.plugin_id, release.manifest.subscribes)
            if old_process is not None:
                await self.supervisor.stop(old_process)
            return active
        except Exception as exc:
            if route_committed:
                if old_release is not None and old_process is not None:
                    self.router.activate(
                        old_release.plugin_id,
                        old_release.manifest,
                        self._route_client(old_process),
                    )
                else:
                    self.router.deactivate(release.plugin_id)
            if old_release is not None:
                self.store.set_active(
                    old_release,
                    previous_version=old_release.previous_version,
                    enabled=old_release.enabled,
                )
                if old_process is not None:
                    self.supervisor.promote(old_process)
                    if old_drained:
                        await self.supervisor.resume(old_process)
            else:
                self.store.clear_active(release.plugin_id)
            if new_process is not None:
                await self._safe_stop(new_process)
            raise self._operation_error(exc, "activation_failed") from None

    async def _verify_stable(self, process: PluginProcess):
        if self.stabilize_seconds:
            await asyncio.sleep(self.stabilize_seconds)
        health = await self.supervisor.health(process)
        if health.state != "healthy":
            raise PluginOperationError(
                "stabilization_failed",
                self._sanitize(health.last_error or f"Feature health is {health.state}"),
            )

    @staticmethod
    def _route_client(process):
        return (
            RoutedPluginClient(process)
            if isinstance(process, PluginProcess)
            else process.client
        )

    async def _safe_stop(self, process):
        try:
            await self.supervisor.stop(process)
        except Exception:
            pass

    async def _install_private_venv(self, staged: StagedRelease):
        venv_path = staged.path / "venv"
        await self._run_install_command(
            [sys.executable, "-m", "venv", str(venv_path)],
            staged.path,
        )
        plugin_wheel = staged.path / "plugin.whl"
        install_wheel = staged.path / (
            f"telepiplex_{staged.plugin_id.replace('-', '_')}-{staged.version}-py3-none-any.whl"
        )
        shutil.copy2(plugin_wheel, install_wheel)
        try:
            await self._run_install_command([
                str(venv_path / "bin/pip"),
                "install",
                "--no-index",
                "--find-links",
                str(staged.path / "wheelhouse"),
                str(install_wheel),
            ], staged.path)
        finally:
            install_wheel.unlink(missing_ok=True)

    async def _run_install_command(self, argv: list[str], cwd: Path):
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.install_timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise PluginOperationError("install_timeout", "Feature installation timed out")
        if process.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace")[-1000:]
            raise PluginOperationError("install_failed", self._sanitize(detail))

    @staticmethod
    def _result(state: str, release: ActiveRelease, message: str) -> PluginOperationResult:
        return PluginOperationResult(
            state=state,
            plugin_id=release.plugin_id,
            version=release.version,
            message=message,
            details={
                "source_commit": release.manifest.source.commit,
                "previous_version": release.previous_version,
            },
        )

    def _operation_error(self, exc: Exception, fallback_code: str) -> PluginOperationError:
        if isinstance(exc, PluginOperationError):
            return exc
        if isinstance(exc, (ArtifactError, StoreError, RoutingError, SupervisorError)):
            return PluginOperationError(exc.code, self._sanitize(str(exc)))
        return PluginOperationError(fallback_code, type(exc).__name__)

    @staticmethod
    def _sanitize(value: str) -> str:
        return re.sub(
            r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+",
            r"\1=***redacted***",
            str(value),
        )[:1000]
