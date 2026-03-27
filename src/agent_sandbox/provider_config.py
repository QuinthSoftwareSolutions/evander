from __future__ import annotations

from pathlib import Path

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
        resolution = self.resolver.resolve_domain(
            normalized_path,
            domain="providers",
            base_config=get_domain_base_config(self.config, "providers"),
            validator=lambda candidate: validate_effective_domain_payload("providers", candidate),
        )
        payload = self.config.model_dump(mode="json")
        payload.update(resolution.effective_config)
        return GatewayConfig.model_validate(payload)

    def resolve_for_session_id(self, session_id: str) -> GatewayConfig:
        session = self.session_store.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return self.resolve_for_session_manifest(session)

    def resolve_for_session_manifest(self, session: SessionManifest) -> GatewayConfig:
        return self.resolve_for_directory(session.directory_path)

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        try:
            return self.directory_service.show(directory_path).directory.path
        except ValueError:
            return "root"
