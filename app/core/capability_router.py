from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType

from app.core.plugin_manifest import PluginManifest


class RoutingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class PluginRoute:
    plugin_id: str
    manifest: PluginManifest
    client: object


@dataclass(frozen=True)
class RouteSnapshot:
    plugin_ids: tuple[str, ...]
    capabilities: object
    commands: object
    callbacks: object
    blocked: object


_EMPTY_SNAPSHOT = RouteSnapshot(
    plugin_ids=(),
    capabilities=MappingProxyType({}),
    commands=MappingProxyType({}),
    callbacks=MappingProxyType({}),
    blocked=MappingProxyType({}),
)


class CapabilityRouter:
    def __init__(self):
        self._lock = RLock()
        self._registrations: dict[str, PluginRoute] = {}
        self._snapshot = _EMPTY_SNAPSHOT

    @property
    def snapshot(self) -> RouteSnapshot:
        return self._snapshot

    def activate(self, plugin_id: str, manifest: PluginManifest, client):
        plugin_id = str(plugin_id)
        if plugin_id != manifest.plugin_id:
            raise RoutingError("identity_mismatch", "plugin route identity does not match manifest")
        with self._lock:
            candidate = dict(self._registrations)
            candidate[plugin_id] = PluginRoute(plugin_id, manifest, client)
            snapshot = self._build_snapshot(candidate)
            missing = list(snapshot.blocked.get(plugin_id, ()))
            if missing:
                raise RoutingError(
                    "missing_capability",
                    f"missing required capabilities: {', '.join(missing)}",
                )
            self._registrations = candidate
            self._snapshot = snapshot

    def deactivate(self, plugin_id: str):
        with self._lock:
            candidate = dict(self._registrations)
            candidate.pop(str(plugin_id), None)
            self._registrations = candidate
            self._snapshot = self._build_snapshot(candidate)

    def _build_snapshot(self, registrations: dict[str, PluginRoute]) -> RouteSnapshot:
        capability_declarations: dict[str, PluginRoute] = {}
        command_declarations: dict[str, PluginRoute] = {}
        callback_declarations: dict[str, PluginRoute] = {}
        for route in registrations.values():
            for declaration in route.manifest.provides:
                existing = capability_declarations.get(declaration.name)
                if existing and existing.plugin_id != route.plugin_id:
                    raise RoutingError(
                        "capability_conflict",
                        f"capability {declaration.name} is already provided by {existing.plugin_id}",
                    )
                capability_declarations[declaration.name] = route
            for declaration in route.manifest.commands:
                existing = command_declarations.get(declaration.name)
                if existing and existing.plugin_id != route.plugin_id:
                    raise RoutingError(
                        "command_conflict",
                        f"command {declaration.name} is already owned by {existing.plugin_id}",
                    )
                command_declarations[declaration.name] = route
            for namespace in route.manifest.callbacks:
                existing = callback_declarations.get(namespace)
                if existing and existing.plugin_id != route.plugin_id:
                    raise RoutingError(
                        "callback_conflict",
                        f"callback {namespace} is already owned by {existing.plugin_id}",
                    )
                callback_declarations[namespace] = route

        blocked: dict[str, tuple[str, ...]] = {}
        unblocked = set(registrations)
        changed = True
        while changed:
            changed = False
            available = {
                declaration.name
                for plugin_id in unblocked
                for declaration in registrations[plugin_id].manifest.provides
            }
            for plugin_id in tuple(unblocked):
                missing = tuple(sorted(
                    requirement
                    for requirement in registrations[plugin_id].manifest.requires
                    if requirement not in available
                ))
                if missing:
                    blocked[plugin_id] = missing
                    unblocked.remove(plugin_id)
                    changed = True

        capabilities = {
            name: route
            for name, route in capability_declarations.items()
            if route.plugin_id in unblocked
        }
        commands = {
            name: route
            for name, route in command_declarations.items()
            if route.plugin_id in unblocked
        }
        callbacks = {
            name: route
            for name, route in callback_declarations.items()
            if route.plugin_id in unblocked
        }
        return RouteSnapshot(
            plugin_ids=tuple(sorted(registrations)),
            capabilities=MappingProxyType(capabilities),
            commands=MappingProxyType(commands),
            callbacks=MappingProxyType(callbacks),
            blocked=MappingProxyType(blocked),
        )

    async def call(
        self,
        capability: str,
        method: str,
        payload: dict,
        context: dict | None = None,
    ) -> dict:
        snapshot = self._snapshot
        route = snapshot.capabilities.get(str(capability))
        if route is None:
            raise RoutingError(
                "capability_unavailable",
                f"capability is unavailable: {capability}",
            )
        context = dict(context or {})
        deadline = float(context.get("deadline") or 30)
        idempotency_key = str(context.get("idempotency_key") or "")
        return await route.client.request(
            "capability.call",
            {
                "capability": str(capability),
                "method": str(method),
                "payload": payload,
                "context": context,
            },
            deadline=deadline,
            idempotency_key=idempotency_key,
        )

    def command_route(self, command: str) -> PluginRoute | None:
        return self._snapshot.commands.get(str(command))

    def callback_route(self, namespace: str) -> PluginRoute | None:
        return self._snapshot.callbacks.get(str(namespace))

    def plugin_status(self, plugin_id: str) -> dict:
        plugin_id = str(plugin_id)
        if plugin_id not in self._snapshot.plugin_ids:
            return {"plugin_id": plugin_id, "state": "absent", "missing_capabilities": []}
        missing = list(self._snapshot.blocked.get(plugin_id, ()))
        return {
            "plugin_id": plugin_id,
            "state": "blocked" if missing else "active",
            "missing_capabilities": missing,
        }

