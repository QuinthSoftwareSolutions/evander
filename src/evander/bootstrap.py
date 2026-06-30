from __future__ import annotations

from pathlib import Path

from evander.config import GatewayConfig, load_config
from evander.directory_config import DirectoryConfigStore, load_root_gateway_config
from evander.directories import DirectoryStore
from evander.host_config import (
    DEFAULT_RUNTIME_DIR,
    HostConfig,
    build_runtime_gateway_config,
    ensure_host_config,
)
from evander.migrations import (
    migrate_global_config_to_root,
    migrate_local_issues_to_unified_schema,
)


def resolve_project_dir(*, legacy_config_path: str | Path | None = None) -> Path:
    if legacy_config_path is None:
        return Path.cwd()
    config_path = Path(legacy_config_path)
    if config_path.parent.name == "config" and config_path.parent.parent != config_path.parent:
        return config_path.parent.parent
    return config_path.parent


def resolve_runtime_dir(
    *,
    runtime_dir: str | Path | None = None,
    legacy_config_path: str | Path | None = None,
) -> Path:
    if runtime_dir is not None:
        return Path(runtime_dir)
    if legacy_config_path is not None:
        legacy_path = Path(legacy_config_path)
        if legacy_path.exists():
            return load_config(legacy_path).runtime_dir
    return DEFAULT_RUNTIME_DIR


def load_runtime_gateway_config(
    *,
    runtime_dir: str | Path | None = None,
    legacy_config_path: str | Path | None = None,
) -> GatewayConfig:
    resolved_runtime_dir = resolve_runtime_dir(
        runtime_dir=runtime_dir, legacy_config_path=legacy_config_path
    )
    host_config = ensure_host_config(resolved_runtime_dir, legacy_config_path=legacy_config_path)
    directory_store = DirectoryStore(host_config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=host_config.runtime_dir)
    config_store.ensure_root()
    if legacy_config_path is not None:
        legacy_path = Path(legacy_config_path)
        if legacy_path.exists() and not config_store.list_domains("root"):
            migrate_global_config_to_root(load_config(legacy_path), overwrite=False)
    migrate_local_issues_to_unified_schema(build_runtime_gateway_config(host_config))
    return load_root_gateway_config(
        directory_store,
        runtime_dir=host_config.runtime_dir,
        host_config=host_config,
        base_gateway_config=None,
    )


def load_host_bootstrap_config(
    *,
    runtime_dir: str | Path | None = None,
    legacy_config_path: str | Path | None = None,
) -> HostConfig:
    resolved_runtime_dir = resolve_runtime_dir(
        runtime_dir=runtime_dir, legacy_config_path=legacy_config_path
    )
    return ensure_host_config(resolved_runtime_dir, legacy_config_path=legacy_config_path)
