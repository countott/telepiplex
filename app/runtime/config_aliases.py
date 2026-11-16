"""Resolve explicit legacy fields before package defaults obscure their intent."""
from copy import deepcopy


def normalize_explicit_aliases(plugin_id: str, raw: dict) -> dict:
    """Return a copy with canonical aliases resolved, without adding defaults."""
    value = deepcopy(raw)
    if plugin_id == "download":
        if "endpoint_intervals" not in value and "request_interval" in value:
            value["endpoint_intervals"] = {
                key: value["request_interval"] for key in (
                    "offline_poll", "offline_mutation", "storage_read",
                    "storage_mutation", "token_refresh",
                )
            }
        if "poll_initial_interval" not in value and "poll_interval" in value:
            value["poll_initial_interval"] = value["poll_interval"]
    elif plugin_id == "rename" and isinstance(value.get("ai"), dict):
        ai = value["ai"]
        if "base_url" in ai:
            if "api_url" not in ai:
                ai["api_url"] = ai["base_url"]
            del ai["base_url"]
    return value
