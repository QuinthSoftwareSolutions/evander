from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from .config import GatewayConfig, load_config
from .directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from .directories import DirectoryService, DirectoryStore
from .models import SessionManifest
from .storage import JsonStore


class TriggerProviderConfig(BaseModel):
    runtime_dir: Path
    session_store_path: Path
    pr_map_path: Path
    github_token_env: str
    azure_devops_pr_pat_env: str
    azure_devops_organization_url: str
    azure_devops_project: str
    email_provider_timeout_seconds: float
    email_store_path: Path
    draft_store_path: Path


class EffectProviderConfig(BaseModel):
    runtime_dir: Path


class QueryProviderConfig(BaseModel):
    runtime_dir: Path


class GateProviderConfig(BaseModel):
    runtime_dir: Path


class ProviderConfigResolver:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        directory_store: DirectoryStore | None = None,
        session_store: JsonStore[SessionManifest] | None = None,
    ) -> None:
        self.config = config
        self.directory_store = directory_store or DirectoryStore(config.directories_store_path)
        self.directory_store.ensure_root()
        self.directory_service = DirectoryService(self.directory_store)
        self.config_store = DirectoryConfigStore(self.directory_store, runtime_dir=config.runtime_dir)
        self.session_store = session_store or JsonStore(config.session_store_path, SessionManifest)
        self.resolver = DirectoryConfigResolver(
            self.directory_service,
            base_gateway_config=config,
            config_store=self.config_store,
        )

    @classmethod
    def from_config_path(cls, path: str | Path) -> "ProviderConfigResolver":
        return cls(load_config(path))

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        normalized_path = self._normalize_directory_path(directory_path)
        payload = self.config.model_dump(mode="json")
        for domain in ("providers", "email"):
            resolution = self.resolver.resolve_domain(
                normalized_path,
                domain=domain,
                base_config=get_domain_base_config(self.config, domain),
                validator=lambda candidate, domain=domain: validate_effective_domain_payload(domain, candidate),
            )
            payload.update(resolution.effective_config)
        return GatewayConfig.model_validate(payload)

    def resolve_for_session_id(self, session_id: str) -> GatewayConfig:
        return self.resolve_for_session_manifest(self.get_session_manifest(session_id))

    def get_session_manifest(self, session_id: str) -> SessionManifest:
        session = self.session_store.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return session

    def resolve_for_session_manifest(self, session: SessionManifest) -> GatewayConfig:
        return self.resolve_for_directory(session.directory_path)

    def resolve_trigger_provider_config(self, directory_path: str | None) -> TriggerProviderConfig:
        resolved = self.resolve_for_directory(directory_path)
        return TriggerProviderConfig.model_validate(resolved.model_dump(mode="json"))

    def resolve_effect_provider_config(self, directory_path: str | None) -> EffectProviderConfig:
        resolved = self.resolve_for_directory(directory_path)
        return EffectProviderConfig.model_validate(resolved.model_dump(mode="json"))

    def resolve_query_provider_config(self, directory_path: str | None) -> QueryProviderConfig:
        resolved = self.resolve_for_directory(directory_path)
        return QueryProviderConfig.model_validate(resolved.model_dump(mode="json"))

    def resolve_gate_provider_config(self, directory_path: str | None) -> GateProviderConfig:
        resolved = self.resolve_for_directory(directory_path)
        return GateProviderConfig.model_validate(resolved.model_dump(mode="json"))

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        try:
            return self.directory_service.show(directory_path).directory.path
        except ValueError:
            return "root"
