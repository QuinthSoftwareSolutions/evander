from __future__ import annotations

import shlex
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from agent_sandbox.bootstrap import load_runtime_gateway_config
from agent_sandbox.config import GatewayConfig, load_config
from agent_sandbox.directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.models import SessionManifest
from agent_sandbox.repositories.session_manifests import (
    JsonSessionManifestRepository,
    SessionManifestRepository,
    as_session_manifest_repository,
)


class AzureVmProviderConfig(BaseModel):
    subscription: str | None = None
    resource_group: str | None = None
    region: str | None = None
    virtual_network: str | None = None
    subnet: str | None = None
    private_relay_access: str = "required"
    vm_size: str = "Standard_D4s_v5"
    image_reference: str = "Canonical:ubuntu-24_04-lts:server:latest"
    ssh_username: str = "agent"
    ssh_public_key_path: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class EffectiveVmDefaultsConfig(BaseModel):
    provider: str = "libvirt"
    provider_defaults: dict[str, str] = Field(default_factory=dict)
    azure: AzureVmProviderConfig = Field(default_factory=AzureVmProviderConfig)
    bind_network_cidr: str = "192.168.250.0/24"
    gateway_url: str = "http://192.168.250.1:8000/mcp"
    http_target_relay_host: str = "0.0.0.0"
    http_target_relay_port: int = 11777
    nuget_relay_host: str = "0.0.0.0"
    nuget_relay_port: int = 11666
    openai_relay_host: str = "0.0.0.0"
    openai_relay_port: int = 11434

    def guest_openai_base_url(self) -> str:
        return _service_url_from_gateway_url(
            self.gateway_url, port=self.openai_relay_port, path="/v1"
        )

    def guest_nuget_relay_base_url(self) -> str:
        return _service_url_from_gateway_url(self.gateway_url, port=self.nuget_relay_port, path="")

    def guest_http_target_relay_base_url(self) -> str:
        return _service_url_from_gateway_url(
            self.gateway_url, port=self.http_target_relay_port, path=""
        )

    def guest_environment(self) -> dict[str, str]:
        return {
            "GATEWAY_URL": self.gateway_url,
            "HTTP_TARGET_RELAY_BASE_URL": self.guest_http_target_relay_base_url(),
            "LLM_BASE_URL": self.guest_openai_base_url(),
            "NUGET_RELAY_BASE_URL": self.guest_nuget_relay_base_url(),
        }


class VmDefaultsConfigResolver:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        directory_store: DirectoryStore | None = None,
        session_store: SessionManifestRepository | None = None,
    ) -> None:
        self.config = config
        self.directory_store = directory_store or DirectoryStore(config.directories_store_path)
        self.directory_store.ensure_root()
        self.directory_service = DirectoryService(self.directory_store)
        self.config_store = DirectoryConfigStore(
            self.directory_store, runtime_dir=config.runtime_dir
        )
        raw_session_store = session_store or JsonSessionManifestRepository(
            config.session_store_path
        )
        self.session_store = as_session_manifest_repository(raw_session_store)
        self.resolver = DirectoryConfigResolver(
            self.directory_service,
            base_gateway_config=config,
            config_store=self.config_store,
        )

    @classmethod
    def from_config_path(cls, path: str | Path) -> "VmDefaultsConfigResolver":
        return cls(load_config(path))

    def resolve_for_directory(self, directory_path: str | None) -> EffectiveVmDefaultsConfig:
        normalized_path = self._normalize_directory_path(directory_path)
        resolution = self.resolver.resolve_domain(
            normalized_path,
            domain="vm",
            base_config=get_domain_base_config(self.config, "vm"),
            validator=lambda candidate: validate_effective_domain_payload("vm", candidate),
        )
        payload = resolution.effective_config
        payload.setdefault("openai_relay_host", self.config.openai_relay_host)
        payload.setdefault("openai_relay_port", self.config.openai_relay_port)
        payload.setdefault("http_target_relay_host", self.config.http_target_relay_host)
        payload.setdefault("http_target_relay_port", self.config.http_target_relay_port)
        payload.setdefault("nuget_relay_host", self.config.nuget_relay_host)
        payload.setdefault("nuget_relay_port", self.config.nuget_relay_port)
        return EffectiveVmDefaultsConfig.model_validate(payload)

    def resolve_for_session_id(self, session_id: str) -> EffectiveVmDefaultsConfig:
        session = self.session_store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return self.resolve_for_session_manifest(session)

    def resolve_for_session_manifest(self, session: SessionManifest) -> EffectiveVmDefaultsConfig:
        return self.resolve_for_directory(session.directory_path)

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        try:
            return self.directory_service.show(directory_path).directory.path
        except ValueError:
            return "root"


def render_vm_defaults_exports(
    *,
    runtime_dir: str | Path,
    session_id: str | None = None,
    fallback_directory: str = "root",
) -> str:
    resolver = VmDefaultsConfigResolver(load_runtime_gateway_config(runtime_dir=runtime_dir))
    if session_id:
        try:
            vm_defaults = resolver.resolve_for_session_id(session_id)
        except ValueError:
            vm_defaults = resolver.resolve_for_directory(fallback_directory)
    else:
        vm_defaults = resolver.resolve_for_directory(fallback_directory)
    return "\n".join(
        f"export {key}={shlex.quote(value)}"
        for key, value in sorted(vm_defaults.guest_environment().items())
    )


def _service_url_from_gateway_url(gateway_url: str, *, port: int, path: str) -> str:
    parts = urlsplit(gateway_url)
    host = parts.hostname or "127.0.0.1"
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if parts.username:
        credentials = parts.username
        if parts.password:
            credentials = f"{credentials}:{parts.password}"
        netloc = f"{credentials}@{netloc}"
    netloc = f"{netloc}:{port}"
    normalized = SplitResult(
        scheme=parts.scheme or "http",
        netloc=netloc,
        path=path,
        query="",
        fragment="",
    )
    return urlunsplit(normalized)
