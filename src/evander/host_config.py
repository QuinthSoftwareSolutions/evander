from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from evander.addons import AddonLoaderConfig
from evander.config import GatewayConfig, load_config

DEFAULT_RUNTIME_DIR = Path("/var/lib/evander")
HOST_CONFIG_FILENAME = "host-config.json"


class HostConfig(BaseModel):
    runtime_dir: Path = DEFAULT_RUNTIME_DIR
    log_level: str = "INFO"
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    openai_relay_host: str = "0.0.0.0"
    openai_relay_port: int = 11434
    http_target_relay_host: str = "0.0.0.0"
    http_target_relay_port: int = 11777
    nuget_relay_host: str = "0.0.0.0"
    nuget_relay_port: int = 11666
    npm_audit_facade_host: str = "0.0.0.0"
    npm_audit_facade_port: int = 11881
    audit_log_path: Path = Path("/var/log/evander/audit.jsonl")
    session_store_path: Path = Path("/var/lib/evander/sessions.json")
    approval_store_path: Path = Path("/var/lib/evander/approvals.json")
    draft_store_path: Path = Path("/var/lib/evander/drafts.json")
    email_store_path: Path = Path("/var/lib/evander/emails.json")
    email_cursor_store_path: Path = Path("/var/lib/evander/email_cursors.json")
    discord_command_store_path: Path = Path("/var/lib/evander/discord_commands.json")
    directories_store_path: Path = Path("/var/lib/evander/directories.json")
    pr_map_path: Path = Path("/var/lib/evander/pr-map.json")
    host_agent_control_state_path: Path = Path(
        "/var/lib/evander/host-agent-control-state.json"
    )
    addon_loader: AddonLoaderConfig = Field(default_factory=AddonLoaderConfig)
    enable_directory_scoped_config: bool = True

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> "HostConfig":
        if not self.runtime_dir.is_absolute():
            self.runtime_dir = self.runtime_dir.expanduser().resolve()
        default_runtime_dir = DEFAULT_RUNTIME_DIR
        default_paths = {
            "session_store_path": default_runtime_dir / "sessions.json",
            "approval_store_path": default_runtime_dir / "approvals.json",
            "draft_store_path": default_runtime_dir / "drafts.json",
            "email_store_path": default_runtime_dir / "emails.json",
            "email_cursor_store_path": default_runtime_dir / "email_cursors.json",
            "discord_command_store_path": default_runtime_dir / "discord_commands.json",
            "directories_store_path": default_runtime_dir / "directories.json",
            "pr_map_path": default_runtime_dir / "pr-map.json",
            "host_agent_control_state_path": default_runtime_dir / "host-agent-control-state.json",
        }
        for field_name, default_path in default_paths.items():
            if getattr(self, field_name) == default_path:
                setattr(self, field_name, self.runtime_dir / default_path.name)
            elif not getattr(self, field_name).is_absolute():
                setattr(self, field_name, getattr(self, field_name).expanduser().resolve())
        if self.audit_log_path == Path("/var/log/evander/audit.jsonl"):
            self.audit_log_path = self.runtime_dir / "audit.jsonl"
        elif not self.audit_log_path.is_absolute():
            self.audit_log_path = self.audit_log_path.expanduser().resolve()
        return self

    @classmethod
    def from_legacy_gateway_config(cls, config: GatewayConfig) -> "HostConfig":
        return cls.model_validate(
            {
                "runtime_dir": config.runtime_dir,
                "log_level": config.log_level,
                "gateway_host": config.host,
                "gateway_port": config.port,
                "openai_relay_host": config.openai_relay_host,
                "openai_relay_port": config.openai_relay_port,
                "http_target_relay_host": config.http_target_relay_host,
                "http_target_relay_port": config.http_target_relay_port,
                "nuget_relay_host": config.nuget_relay_host,
                "nuget_relay_port": config.nuget_relay_port,
                "npm_audit_facade_host": config.npm_audit_facade_host,
                "npm_audit_facade_port": config.npm_audit_facade_port,
                "audit_log_path": config.audit_log_path,
                "session_store_path": config.session_store_path,
                "approval_store_path": config.approval_store_path,
                "draft_store_path": config.draft_store_path,
                "email_store_path": config.email_store_path,
                "email_cursor_store_path": config.email_cursor_store_path,
                "discord_command_store_path": config.discord_command_store_path,
                "directories_store_path": config.directories_store_path,
                "pr_map_path": config.pr_map_path,
                "host_agent_control_state_path": config.host_agent_control_state_path,
                "addon_loader": config.addon_loader,
                "enable_directory_scoped_config": config.enable_directory_scoped_config,
            }
        )


def host_config_path(runtime_dir: str | Path) -> Path:
    return Path(runtime_dir) / HOST_CONFIG_FILENAME


def save_host_config(config: HostConfig) -> Path:
    path = host_config_path(config.runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8"
    )
    return path


def load_host_config(runtime_dir: str | Path) -> HostConfig:
    path = host_config_path(runtime_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return HostConfig.model_validate(payload)


def ensure_host_config(
    runtime_dir: str | Path,
    *,
    legacy_config_path: str | Path | None = None,
) -> HostConfig:
    runtime_root = Path(runtime_dir).expanduser().resolve()
    path = host_config_path(runtime_root)
    if path.exists():
        return load_host_config(runtime_root)
    if legacy_config_path is not None:
        legacy_path = Path(legacy_config_path)
        if legacy_path.exists():
            config = HostConfig.from_legacy_gateway_config(load_config(legacy_path))
            save_host_config(config)
            return config
    config = HostConfig(runtime_dir=runtime_root)
    save_host_config(config)
    return config


def runtime_dir_from_legacy_config(path: str | Path) -> Path:
    return load_config(path).runtime_dir


def build_runtime_gateway_config(host_config: HostConfig) -> GatewayConfig:
    payload = GatewayConfig().model_dump(mode="json")
    payload.update(
        {
            "runtime_dir": host_config.runtime_dir,
            "log_level": host_config.log_level,
            "host": host_config.gateway_host,
            "port": host_config.gateway_port,
            "openai_relay_host": host_config.openai_relay_host,
            "openai_relay_port": host_config.openai_relay_port,
            "http_target_relay_host": host_config.http_target_relay_host,
            "http_target_relay_port": host_config.http_target_relay_port,
            "nuget_relay_host": host_config.nuget_relay_host,
            "nuget_relay_port": host_config.nuget_relay_port,
            "npm_audit_facade_host": host_config.npm_audit_facade_host,
            "npm_audit_facade_port": host_config.npm_audit_facade_port,
            "audit_log_path": host_config.audit_log_path,
            "session_store_path": host_config.session_store_path,
            "approval_store_path": host_config.approval_store_path,
            "draft_store_path": host_config.draft_store_path,
            "email_store_path": host_config.email_store_path,
            "email_cursor_store_path": host_config.email_cursor_store_path,
            "discord_command_store_path": host_config.discord_command_store_path,
            "directories_store_path": host_config.directories_store_path,
            "pr_map_path": host_config.pr_map_path,
            "host_agent_control_state_path": host_config.host_agent_control_state_path,
            "addon_loader": host_config.addon_loader,
            "enable_directory_scoped_config": host_config.enable_directory_scoped_config,
        }
    )
    return GatewayConfig.model_validate(payload)
