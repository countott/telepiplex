from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.plugin_contract import ContractError


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_COMMAND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ENTRY_POINT = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$"
)
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_CORE_RANGE = re.compile(
    r"^>=(0|[1-9][0-9]*)\.(0|[1-9][0-9]*),<(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
_CORE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MANIFEST_KEYS = {
    "plugin_id",
    "name",
    "version",
    "core_api",
    "entry_point",
    "provides",
    "requires",
    "subscribes",
    "publishes",
    "commands",
    "callbacks",
    "config_schema_version",
    "state_schema_version",
    "source",
}


def _invalid(message: str):
    raise ContractError("invalid_manifest", message)


def _text(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        _invalid(f"{field} is required")
    return text


def _identifier(value, field: str) -> str:
    text = _text(value, field)
    if not _IDENTIFIER.fullmatch(text):
        _invalid(f"{field} has invalid identifier: {text}")
    return text


def _positive_int(value, field: str) -> int:
    if isinstance(value, bool):
        _invalid(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        _invalid(f"{field} must be a positive integer")
    if parsed < 1:
        _invalid(f"{field} must be a positive integer")
    return parsed


def _unique_identifiers(values, field: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        _invalid(f"{field} must be a list")
    parsed = tuple(_identifier(value, f"{field}[]") for value in values)
    if len(set(parsed)) != len(parsed):
        _invalid(f"{field} contains duplicates")
    return parsed


@dataclass(frozen=True)
class CapabilityDeclaration:
    name: str
    exclusive: bool = False


@dataclass(frozen=True)
class CommandDeclaration:
    name: str
    description: str
    menu_visible: bool | None = None


@dataclass(frozen=True)
class SourceDeclaration:
    repository: str
    branch: str
    commit: str


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    core_api: str
    entry_point: str
    provides: tuple[CapabilityDeclaration, ...]
    requires: tuple[str, ...]
    subscribes: tuple[str, ...]
    publishes: tuple[str, ...]
    commands: tuple[CommandDeclaration, ...]
    callbacks: tuple[str, ...]
    config_schema_version: int
    state_schema_version: int
    source: SourceDeclaration

    @classmethod
    def from_mapping(cls, value: dict) -> "PluginManifest":
        if not isinstance(value, dict):
            _invalid("manifest must be a mapping")
        unknown = set(value) - _MANIFEST_KEYS
        if unknown:
            _invalid(f"unknown manifest keys: {sorted(unknown)}")

        plugin_id = _identifier(value.get("plugin_id"), "plugin_id")
        name = _text(value.get("name"), "name")
        version = _text(value.get("version"), "version")
        if not _SEMVER.fullmatch(version):
            _invalid(f"version must be semantic MAJOR.MINOR.PATCH: {version}")
        core_api = _text(value.get("core_api"), "core_api")
        if not _CORE_RANGE.fullmatch(core_api):
            _invalid(f"core_api has invalid range: {core_api}")
        entry_point = _text(value.get("entry_point"), "entry_point")
        if not _ENTRY_POINT.fullmatch(entry_point):
            _invalid(f"entry_point is unsafe or invalid: {entry_point}")

        raw_provides = value.get("provides", [])
        if not isinstance(raw_provides, list):
            _invalid("provides must be a list")
        provides = []
        for item in raw_provides:
            if not isinstance(item, dict) or set(item) - {"name", "exclusive"}:
                _invalid("provides entries require name and optional exclusive")
            exclusive = item.get("exclusive", False)
            if not isinstance(exclusive, bool):
                _invalid("provides[].exclusive must be boolean")
            provides.append(CapabilityDeclaration(
                _identifier(item.get("name"), "provides[].name"),
                exclusive,
            ))
        if len({item.name for item in provides}) != len(provides):
            _invalid("provides contains duplicates")

        raw_commands = value.get("commands", [])
        if not isinstance(raw_commands, list):
            _invalid("commands must be a list")
        commands = []
        for item in raw_commands:
            if not isinstance(item, dict) or set(item) - {
                "name", "description", "menu_visible",
            }:
                _invalid(
                    "commands entries require name, description, and optional menu_visible"
                )
            command_name = _text(item.get("name"), "commands[].name")
            if not _COMMAND.fullmatch(command_name):
                _invalid(f"commands[].name is invalid: {command_name}")
            menu_visible = item.get("menu_visible")
            if "menu_visible" in item and not isinstance(menu_visible, bool):
                _invalid("commands[].menu_visible must be boolean")
            commands.append(CommandDeclaration(
                command_name,
                _text(item.get("description"), "commands[].description"),
                menu_visible,
            ))
        if len({item.name for item in commands}) != len(commands):
            _invalid("commands contains duplicates")

        source = value.get("source")
        if not isinstance(source, dict) or set(source) != {"repository", "branch", "commit"}:
            _invalid("source requires repository, branch, and commit")
        repository = _text(source.get("repository"), "source.repository")
        branch = _text(source.get("branch"), "source.branch")
        if any(token in repository + branch for token in ("\x00", "\n", "\r")):
            _invalid("source contains control characters")
        commit = _text(source.get("commit"), "source.commit").lower()
        if not _COMMIT.fullmatch(commit):
            _invalid("source.commit must be a 40-character hexadecimal SHA")

        return cls(
            plugin_id=plugin_id,
            name=name,
            version=version,
            core_api=core_api,
            entry_point=entry_point,
            provides=tuple(provides),
            requires=_unique_identifiers(value.get("requires", []), "requires"),
            subscribes=_unique_identifiers(value.get("subscribes", []), "subscribes"),
            publishes=_unique_identifiers(value.get("publishes", []), "publishes"),
            commands=tuple(commands),
            callbacks=_unique_identifiers(value.get("callbacks", []), "callbacks"),
            config_schema_version=_positive_int(
                value.get("config_schema_version", 1),
                "config_schema_version",
            ),
            state_schema_version=_positive_int(
                value.get("state_schema_version", 1),
                "state_schema_version",
            ),
            source=SourceDeclaration(repository, branch, commit),
        )

    def supports_core(self, version: str) -> bool:
        version_match = _CORE_VERSION.fullmatch(str(version or "").strip())
        range_match = _CORE_RANGE.fullmatch(self.core_api)
        if not version_match or not range_match:
            return False
        candidate = (int(version_match.group(1)), int(version_match.group(2)))
        minimum = (int(range_match.group(1)), int(range_match.group(2)))
        maximum = (int(range_match.group(3)), int(range_match.group(4)))
        return minimum <= candidate < maximum
