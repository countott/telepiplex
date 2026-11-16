"""Strict, non-secret diagnostics for metadata connection settings."""
import math


def provider_settings(config, provider, error):
    metadata = config.get("metadata", {})
    if not isinstance(metadata, dict):
        raise error("metadata 配置无效", "invalid_config")
    settings = metadata.get(provider, {})
    if not isinstance(settings, dict):
        raise error(f"metadata.{provider} 配置无效", "invalid_config")
    return settings


def timeout_seconds(settings, provider, error):
    value = settings.get("timeout", 15)
    if type(value) not in (int, float) or not 1 <= value <= 300 or not math.isfinite(value):
        raise error(f"metadata.{provider}.timeout 配置无效", "invalid_config")
    return value
