from __future__ import annotations

from pathlib import Path

from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore, load_root_gateway_config
from evander.directories import DirectoryStore
from evander.host_config import DEFAULT_RUNTIME_DIR, HostConfig, ensure_host_config


def resolve_runtime_dir(
    *,
    runtime_dir: str | Path | None = None,
) -> Path:
    if runtime_dir is not None:
        return Path(runtime_dir)
    return DEFAULT_RUNTIME_DIR


def load_runtime_gateway_config(
    *,
    runtime_dir: str | Path | None = None,
) -> GatewayConfig:
    resolved_runtime_dir = resolve_runtime_dir(runtime_dir=runtime_dir)
    host_config = ensure_host_config(resolved_runtime_dir)
    directory_store = DirectoryStore(host_config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=host_config.runtime_dir)
    config_store.ensure_root()
    return load_root_gateway_config(
        directory_store,
        runtime_dir=host_config.runtime_dir,
        host_config=host_config,
        base_gateway_config=None,
    )


def load_host_bootstrap_config(
    *,
    runtime_dir: str | Path | None = None,
) -> HostConfig:
    resolved_runtime_dir = resolve_runtime_dir(runtime_dir=runtime_dir)
    return ensure_host_config(resolved_runtime_dir)
