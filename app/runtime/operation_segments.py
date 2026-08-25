from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


SEGMENT_ROLES = {"identity", "search", "download", "rename", "legacy"}
PRESENTATION_KINDS = {"text", "photo"}
SEGMENT_STATES = {
    "creating",
    "open",
    "sealing",
    "sealed",
    "delivery_uncertain",
    "failed",
}
SEGMENT_DELIVERY_STATES = {"reserved", "delivering", "delivered", "uncertain"}
ACTIVE_SEGMENT_STATES = {"creating", "open", "sealing"}


@dataclass(frozen=True)
class OperationMessageSegment:
    segment_id: str
    operation_id: str
    sequence: int
    owner_plugin_id: str
    role: str
    generation: int
    presentation_kind: str
    state: str
    message_id: int | None
    message_kind: str
    business_revision: int
    rendered_revision: int
    projection_hash: str
    rendered_projection_hash: str
    projection: Mapping[str, Any]
    callback_generation: int
    callback_state: str
    callback_token: str
    callback_busy_text: str
    delivery_state: str
    created_at: float
    updated_at: float
    sealed_at: float | None


def validate_segment_declaration(value: object) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError("segment declaration must be an object")
    role = str(value.get("role") or "").strip()
    presentation_kind = str(
        value.get("presentation_kind") or ""
    ).strip().casefold()
    if role not in SEGMENT_ROLES or role == "legacy":
        raise ValueError("segment role is invalid")
    if presentation_kind not in PRESENTATION_KINDS:
        raise ValueError("segment presentation kind is invalid")
    return role, presentation_kind


def projection_hash(projection: dict) -> str:
    if not isinstance(projection, dict):
        raise ValueError("segment projection must be an object")
    encoded = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def freeze_projection(value: dict) -> Mapping[str, Any]:
    return MappingProxyType(json.loads(json.dumps(value, ensure_ascii=False)))
