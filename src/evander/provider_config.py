from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

from evander.config import GatewayConfig
from evander.classification.models import ClassificationSettings
from evander.container_workers import ContainerWorkerProfile
from evander.contact_allowlists import resolve_effective_contact_directory
from evander.connectors.models import ConnectorDirectoryConfig
from evander.directory_config import (
    ContactDirectoryConfig,
    DirectoryConfigResolver,
    DirectoryConfigStore,
    DirectoryScopeResolver,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from evander.host_directories.models import HostDirectoryDirectoryConfig
from evander.directories import DirectoryService, DirectoryStore
from evander.models import SessionManifest
from evander.policy_config import EffectivePolicyConfig
from evander.repositories.session_manifests import (
    JsonSessionManifestRepository,
    SessionManifestRepository,
    as_session_manifest_repository,
)
from evander.storage import JsonStore

MODEL_PROVIDER_MODE_METADATA_KEY = "model_provider_mode"
MODEL_PROVIDER_MODE_VALUES = frozenset(
    {"openai_public", "azure_openai", "anthropic", "local_openai"}
)


def normalize_model_provider_mode_override(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text not in MODEL_PROVIDER_MODE_VALUES:
        allowed = ", ".join(sorted(MODEL_PROVIDER_MODE_VALUES))
        raise ValueError(f"{MODEL_PROVIDER_MODE_METADATA_KEY} must be one of: {allowed}")
    return text


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
    session_store_path: Path
    default_session_ttl_seconds: int
    agent_prompt_queue_max_retries: int = Field(default=3, ge=1)
    agent_context_retry_enabled: bool = True
    agent_context_retry_max_attempts: int = Field(default=1, ge=0)
    agent_context_retry_target_chars: int = Field(default=16_000, ge=4_000)
    agent_consultation_token_secret_env: str = "AGENT_PROMPT_TOKEN_SECRET"
    execution_backend: str = "libvirt_vm"
    container_worker_base_url: str | None = None
    container_workers: dict[str, ContainerWorkerProfile] = Field(default_factory=dict)
    container_worker_token_env: str = "EVANDER_WORKER_TOKEN"
    container_worker_default_image: str = "evander-runner:latest"
    container_worker_images: dict[str, str] = Field(default_factory=dict)
    container_worker_active_tool_timeout_seconds: float = 300.0
    container_worker_job_timeout_seconds: float = 1800.0
    container_worker_request_timeout_seconds: float = 30.0
    container_worker_workspace_persistence: str = "session"
    container_worker_codex_auth_mode: Literal["metered_api", "chatgpt_subscription"] = "metered_api"
    container_worker_guest_openai_base_url: str | None = None
    container_worker_guest_anthropic_base_url: str | None = None
    container_worker_guest_gateway_url: str | None = None
    container_worker_guest_http_target_relay_base_url: str | None = None
    container_worker_guest_http_target_browser_proxy_url: str | None = None
    container_worker_guest_nuget_relay_base_url: str | None = None
    container_worker_guest_pypi_index_url: str | None = None
    container_worker_guest_npm_registry_url: str | None = None
    container_worker_mssql_enabled: bool = False
    container_worker_mssql_host: str | None = None
    container_worker_mssql_port: int = 1433
    semantic_store_provider: str = "qdrant"
    qdrant_base_url: str = "http://127.0.0.1:6333"
    qdrant_api_key_env: str | None = "QDRANT_API_KEY"
    semantic_embedding_provider: str = "openai"
    semantic_embedding_model: str = "text-embedding-3-small"
    semantic_embedding_dimensions: int = 1536
    semantic_vector_distance: str = "Cosine"
    model_provider_mode: str = Field(
        default="openai_public",
        validation_alias=AliasChoices("model_provider_mode", "openai_provider_mode"),
    )
    openai_api_base_url: str = "https://api.openai.com/v1"
    openai_api_key_env: str = "OPENAI_API_KEY"
    openai_relay_timeout_seconds: float = 300.0
    openai_image_generation_max_request_bytes: int = Field(default=1_048_576, ge=1)
    openai_image_edit_max_request_bytes: int = Field(default=268_435_456, ge=1)
    local_openai_api_base_url: str = "http://127.0.0.1:11434/v1"
    local_openai_api_key: str = "ollama"
    local_openai_provider_name: str = "local_openai"
    local_openai_max_concurrency: int | None = Field(default=None, ge=1)
    anthropic_api_base_url: str = "https://api.anthropic.com"
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    anthropic_api_version: str = "2023-06-01"
    anthropic_relay_timeout_seconds: float = 300.0

    @property
    def openai_provider_mode(self) -> str:
        return self.model_provider_mode


class QueryProviderConfig(BaseModel):
    runtime_dir: Path
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)
    semantic_store_provider: str = "qdrant"
    qdrant_base_url: str = "http://127.0.0.1:6333"
    qdrant_api_key_env: str | None = "QDRANT_API_KEY"
    semantic_embedding_provider: str = "openai"
    semantic_embedding_model: str = "text-embedding-3-small"
    semantic_embedding_dimensions: int = 1536
    semantic_vector_distance: str = "Cosine"
    model_provider_mode: str = Field(
        default="openai_public",
        validation_alias=AliasChoices("model_provider_mode", "openai_provider_mode"),
    )
    openai_api_base_url: str = "https://api.openai.com/v1"
    openai_api_key_env: str = "OPENAI_API_KEY"
    openai_relay_timeout_seconds: float = 300.0
    openai_image_generation_max_request_bytes: int = Field(default=1_048_576, ge=1)
    openai_image_edit_max_request_bytes: int = Field(default=268_435_456, ge=1)
    local_openai_api_base_url: str = "http://127.0.0.1:11434/v1"
    local_openai_api_key: str = "ollama"
    local_openai_provider_name: str = "local_openai"
    anthropic_api_base_url: str = "https://api.anthropic.com"
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    anthropic_api_version: str = "2023-06-01"
    anthropic_relay_timeout_seconds: float = 300.0

    @property
    def openai_provider_mode(self) -> str:
        return self.model_provider_mode


class GateProviderConfig(BaseModel):
    runtime_dir: Path
    classification: ClassificationSettings = Field(default_factory=ClassificationSettings)


class ProviderSessionManifest(BaseModel):
    session_id: str
    directory_path: str = "root"
    vm_provider: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    allowed_contact_ids: list[str] | None = None
    web_search_enabled: bool = False
    image_generation_enabled: bool = False
    expires_at: datetime | None = None


class ProviderConfigResolver:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        directory_store: DirectoryStore | None = None,
        session_store: SessionManifestRepository | None = None,
        provider_session_store: JsonStore[ProviderSessionManifest] | None = None,
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
        self.provider_session_store = provider_session_store or JsonStore(
            config.session_store_path, ProviderSessionManifest
        )
        self.directory_scope_resolver = DirectoryScopeResolver(self.directory_service)
        self.resolver = DirectoryConfigResolver(
            self.directory_service,
            base_gateway_config=config,
            config_store=self.config_store,
        )

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        normalized_path = self.directory_scope_resolver.resolve_directory_path(directory_path)
        payload = self.config.model_dump(mode="json")
        for domain in ("providers", "email", "privacy"):
            resolution = self.resolver.resolve_domain(
                normalized_path,
                domain=domain,
                base_config=get_domain_base_config(self.config, domain),
                validator=lambda candidate, domain=domain: validate_effective_domain_payload(
                    domain, candidate
                ),
            )
            payload.update(resolution.effective_config)
        return GatewayConfig.model_validate(payload)

    def resolve_policy_for_directory(self, directory_path: str | None) -> EffectivePolicyConfig:
        """Resolve authorization policy without GatewayConfig's provider defaults."""
        normalized_path = self.directory_scope_resolver.resolve_directory_path(directory_path)
        resolution = self.resolver.resolve_domain(
            normalized_path,
            domain="policy",
            base_config=get_domain_base_config(self.config, "policy"),
            validator=lambda candidate: validate_effective_domain_payload("policy", candidate),
        )
        return EffectivePolicyConfig.model_validate(resolution.effective_config)

    def resolve_contacts_for_directory(self, directory_path: str | None) -> ContactDirectoryConfig:
        normalized_path = self.directory_scope_resolver.resolve_directory_path(directory_path)
        resolution = self.resolver.resolve_domain(
            normalized_path,
            domain="contacts",
            base_config=get_domain_base_config(self.config, "contacts"),
            validator=lambda candidate: validate_effective_domain_payload("contacts", candidate),
        )
        return ContactDirectoryConfig.model_validate(resolution.effective_config)

    def resolve_connectors_for_directory(
        self, directory_path: str | None
    ) -> ConnectorDirectoryConfig:
        normalized_path = self.directory_scope_resolver.resolve_directory_path(directory_path)
        resolution = self.resolver.resolve_domain(
            normalized_path,
            domain="connectors",
            base_config=get_domain_base_config(self.config, "connectors"),
            validator=lambda candidate: validate_effective_domain_payload("connectors", candidate),
        )
        return ConnectorDirectoryConfig.model_validate(resolution.effective_config)

    def resolve_host_directories_for_directory(
        self, directory_path: str | None
    ) -> HostDirectoryDirectoryConfig:
        normalized_path = self.directory_scope_resolver.resolve_directory_path(directory_path)
        resolution = self.resolver.resolve_domain(
            normalized_path,
            domain="host_directories",
            base_config=get_domain_base_config(self.config, "host_directories"),
            validator=lambda candidate: validate_effective_domain_payload(
                "host_directories", candidate
            ),
        )
        if resolution.validation_errors:
            raise ValueError("; ".join(resolution.validation_errors))
        return HostDirectoryDirectoryConfig.model_validate(resolution.effective_config)

    def resolve_for_session_id(self, session_id: str) -> GatewayConfig:
        session = self.session_store.get_session(session_id)
        if session is None:
            session = self.provider_session_store.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return self.resolve_for_session_manifest(session)

    def resolve_contacts_for_session_id(self, session_id: str) -> ContactDirectoryConfig:
        session = self.session_store.get_session(session_id)
        if session is None:
            session = self.provider_session_store.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        normalized_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="contacts config",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        return self.resolve_contacts_for_directory(normalized_path)

    def resolve_effective_contacts_for_session_id(self, session_id: str) -> ContactDirectoryConfig:
        session = self.session_store.get_session(session_id)
        if session is None:
            session = self.provider_session_store.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        normalized_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="contacts config",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        contacts = self.resolve_contacts_for_directory(normalized_path)
        return resolve_effective_contact_directory(
            contacts,
            session.allowed_contact_ids,
            session_id=session.session_id,
            directory_path=normalized_path,
        )

    def get_session_manifest(self, session_id: str) -> SessionManifest:
        session = self.session_store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return session

    def resolve_for_session_manifest(
        self, session: SessionManifest | ProviderSessionManifest
    ) -> GatewayConfig:
        normalized_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="provider config",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        resolved = self.resolve_for_directory(normalized_path)
        if (
            session.vm_provider == "container_worker_vm"
            or session.metadata.get("execution_backend") == "container_worker_vm"
        ):
            from evander.admin.services.container_worker_assignment_service import (
                ContainerWorkerAssignmentService,
            )

            resolved = ContainerWorkerAssignmentService(resolved).resolve(
                session.session_id, session=session, directory_path=normalized_path
            )
        if isinstance(session, SessionManifest):
            metadata = session.metadata if isinstance(session.metadata, dict) else {}
            requested_mode = normalize_model_provider_mode_override(
                metadata.get(MODEL_PROVIDER_MODE_METADATA_KEY)
            )
            if requested_mode is not None:
                payload = resolved.model_dump(mode="json")
                payload["model_provider_mode"] = requested_mode
                return GatewayConfig.model_validate(payload)
            agent_provider = str(metadata.get("agent_provider") or "").strip().lower()
            if agent_provider == "claude":
                payload = resolved.model_dump(mode="json")
                payload[MODEL_PROVIDER_MODE_METADATA_KEY] = "anthropic"
                return GatewayConfig.model_validate(payload)
        return resolved

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
