"""Shared runtime construction and configuration lookup for CLI commands."""

from __future__ import annotations

from pathlib import Path

from evander.admin.composition import build_admin_runtime as build_admin_runtime
from evander.bootstrap import load_host_bootstrap_config, load_runtime_gateway_config
from evander.config import GatewayConfig
from evander.runtime import build_runtime as build_runtime


def _runtime_dir_for_config_option(config: Path) -> Path:
    return Path(config).expanduser().resolve()


def _workspace_root_for_config_option(config: Path) -> Path:
    return config.expanduser().resolve().parent


def _project_dir_for_config_option(config: Path) -> Path:
    return config.expanduser().resolve().parent


def _load_runtime_config_from_option(config: Path) -> GatewayConfig:
    return load_runtime_gateway_config(runtime_dir=_runtime_dir_for_config_option(config))


def _load_host_config_from_option(config: Path):
    return load_host_bootstrap_config(runtime_dir=_runtime_dir_for_config_option(config))
