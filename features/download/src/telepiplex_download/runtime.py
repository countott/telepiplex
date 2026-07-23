from __future__ import annotations

from telepiplex_plugin_sdk import FeatureRuntime, RuntimeContext

from .client import Open115Client
from .config_store import FeatureConfigStore
from .directories import normalize_save_directories
from .service import DownloadFeature
from .jobs import DownloadJobStore


def main(context: RuntimeContext) -> FeatureRuntime:
    config_store = FeatureConfigStore(context.config_path)
    config = config_store.read()
    configured_directories = config.get("save_directories") or []
    normalized_directories = normalize_save_directories(configured_directories)
    if configured_directories != normalized_directories:
        config = config_store.write_save_directories(normalized_directories)
    else:
        config["save_directories"] = normalized_directories

    def persist_refreshed_tokens(access_token, refresh_token):
        current = config_store.read()
        mode = str(current.get("auth_mode") or config.get("auth_mode") or "direct")
        config_store.write_tokens(
            access_token,
            refresh_token,
            auth_mode=mode if mode in {"direct", "scan"} else "direct",
        )

    client = Open115Client(config, on_tokens_changed=persist_refreshed_tokens)
    feature = DownloadFeature(
        config=config,
        host=context.host,
        client=client,
        jobs=DownloadJobStore(context.state_path / "downloads.db"),
        config_store=config_store,
    )
    runtime = FeatureRuntime(
        manifest=context.manifest,
        token=context.token,
        capabilities={
            "download.provider": feature.download_capability,
            "storage.provider": feature.storage_capability,
        },
        commands={
            "magnet": feature.command,
            "m": feature.command,
            "auth": feature.command,
            "config": feature.command,
            "q": feature.command,
        },
        callbacks={"download": feature.callback},
        messages=feature.message,
        operation_control=feature.operation_control,
        operation_snapshot=feature.operation_snapshot,
    )
    feature.bind_runtime(runtime)
    return runtime
