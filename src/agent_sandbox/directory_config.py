from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from .config import GatewayConfig
from .directories import DirectoryService, DirectoryStore
from .host_config import HostConfig, build_runtime_gateway_config
from .models import Directory, DirectoryInfo, DirectoryPath

DIRECTORY_CONFIG_OVERLAYS_KEY = "config_overlays"
DIRECTORY_CONFIG_METADATA_KEY = "directory_config"
DIRECTORY_CONFIG_METADATA_DOMAINS_KEY = "domains"
DIRECTORY_CONFIG_ROOT_DIRNAME = "directory-config"

DIRECTORY_CONFIG_DOMAIN_FILENAMES = {
    "policy": "policy.json",
    "providers": "providers.json",
    "email": "email.json",
    "heartbeat": "heartbeat.json",
    "ado-worker": "ado-worker.json",
    "vm": "vm.json",
}

LEGACY_DIRECTORY_CONFIG_DOMAIN_FILENAMES = {
    "policy": "policy.json",
    "providers": "providers.json",
    "workers": "workers.json",
    "vm-defaults": "vm-defaults.json",
}

LEGACY_DIRECTORY_DOMAIN_ALIASES = {
    "workers": "heartbeat",
    "vm-defaults": "vm",
}

DIRECTORY_CONFIG_DOMAIN_FIELDS: dict[str, frozenset[str]] = {
    "policy": frozenset(
        {
            "approval_ttl_seconds",
            "default_session_ttl_seconds",
            "discord_operator_channel_allowlist",
            "discord_operator_user_allowlist",
            "discord_poll_operator_dms",
            "email_draft_domain_allowlist",
            "email_draft_recipient_allowlist",
            "email_folder_allowlist_by_mailbox",
            "email_mailbox_allowlist",
            "email_send_enabled",
            "pr_reply_require_approval",
            "redacted_keys",
        }
    ),
    "providers": frozenset(
        {
            "azure_devops_organization_url",
            "azure_devops_pat_env",
            "azure_devops_project",
            "discord_api_base_url",
            "discord_backend",
            "discord_bot_token_env",
            "discord_poll_limit",
            "discord_retry_base_backoff_seconds",
            "discord_retry_count",
            "discord_retry_max_backoff_seconds",
            "discord_timeout_seconds",
            "github_token_env",
            "nuget_azure_feed_base_url",
            "nuget_azure_pat_env",
            "nuget_public_feed_base_url",
            "nuget_relay_timeout_seconds",
            "openai_api_base_url",
            "openai_api_key_env",
            "openai_relay_timeout_seconds",
        }
    ),
    "email": frozenset(
        {
            "email_mailboxes",
            "email_provider_timeout_seconds",
        }
    ),
    "heartbeat": frozenset(
        {
            "host_busy_check_script_path",
            "host_heartbeat_cooldown_seconds",
            "host_heartbeat_http_timeout_seconds",
            "host_heartbeat_jitter_seconds",
            "host_heartbeat_lock_path",
            "host_heartbeat_poll_interval_seconds",
            "host_heartbeat_resume_prompt",
            "host_heartbeat_resume_timeout_seconds",
            "host_heartbeat_session_cooldown_path",
            "host_heartbeat_stream_conflict_policy",
            "host_heartbeat_vm_user",
            "host_heartbeat_wake_state_path",
            "host_resume_script_path",
        }
    ),
    "vm": frozenset(
        {
            "bind_network_cidr",
            "gateway_url",
            "nuget_relay_host",
            "nuget_relay_port",
            "openai_relay_host",
            "openai_relay_port",
        }
    ),
    "ado-worker": frozenset(
        {
            "ado_area_filters",
            "ado_team_filters",
            "ado_project_or_solution",
            "ado_prewarm_target",
            "ado_bootstrap_dotnet",
            "ado_bootstrap_nuget",
            "ado_bootstrap_mssql",
            "ado_mssql_init_root",
            "ado_bootstrap_playwright",
            "ado_bootstrap_frontend_deps",
            "ado_frontend_host_checkout",
            "ado_frontend_install_roots",
            "ado_mssql_sa_password_env",
        }
    ),
}


class AdoWorkerDirectoryConfig(BaseModel):
    ado_area_filters: list[str] = Field(default_factory=list)
    ado_team_filters: list[str] = Field(default_factory=list)
    ado_project_or_solution: str = ""
    ado_prewarm_target: str | None = None
    ado_bootstrap_dotnet: bool = True
    ado_bootstrap_nuget: bool = True
    ado_bootstrap_mssql: bool = True
    ado_mssql_init_root: str | None = None
    ado_bootstrap_playwright: bool = False
    ado_bootstrap_frontend_deps: bool = False
    ado_frontend_host_checkout: str | None = None
    ado_frontend_install_roots: list[str] = Field(default_factory=list)
    ado_mssql_sa_password_env: str | None = None

LEGACY_DOMAIN_FIELD_SUBSETS: dict[str, dict[str, frozenset[str]]] = {
    "providers": {
        "providers": DIRECTORY_CONFIG_DOMAIN_FIELDS["providers"],
        "email": DIRECTORY_CONFIG_DOMAIN_FIELDS["email"],
    },
    "workers": {
        "heartbeat": DIRECTORY_CONFIG_DOMAIN_FIELDS["heartbeat"],
    },
    "vm-defaults": {
        "vm": DIRECTORY_CONFIG_DOMAIN_FIELDS["vm"],
    },
}


def get_domain_base_config(config: GatewayConfig, domain: str) -> dict[str, Any]:
    if domain == "gateway":
        return config.model_dump(mode="json")
    DirectoryConfigStore._validate_domain_name(domain)
    payload = config.model_dump(mode="json")
    allowed_fields = DIRECTORY_CONFIG_DOMAIN_FIELDS[domain]
    return {key: payload[key] for key in sorted(allowed_fields) if key in payload}


def validate_effective_domain_payload(domain: str, payload: dict[str, Any]) -> dict[str, Any]:
    if domain == "gateway":
        validated = GatewayConfig.model_validate(payload)
        return validated.model_dump(mode="json")
    return DirectoryConfigStore.validate_domain_payload(domain, payload)


class DirectoryConfigValueSource(BaseModel):
    source_type: str
    directory_id: str | None = None
    directory_path: str | None = None


class DirectoryConfigLayer(BaseModel):
    directory_id: str
    directory_path: str
    domain: str
    overlay: dict[str, Any]


class DirectoryConfigResolution(BaseModel):
    domain: str
    selected_directory: DirectoryInfo
    effective_config: dict[str, Any]
    provenance: dict[str, DirectoryConfigValueSource] = Field(default_factory=dict)
    applied_layers: list[DirectoryConfigLayer] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class DirectoryConfigStore:
    def __init__(self, directory_store: DirectoryStore, *, runtime_dir: Path) -> None:
        self._directory_store = directory_store
        self._service = DirectoryService(directory_store)
        self._root_path = runtime_dir / DIRECTORY_CONFIG_ROOT_DIRNAME

    @property
    def root_path(self) -> Path:
        return self._root_path

    def ensure_root(self) -> None:
        self._root_path.mkdir(parents=True, exist_ok=True)

    def list_domains(self, reference: str) -> list[str]:
        directory = self._service.show(reference).directory
        return self._domains_for_directory_info(directory, root_path=self._root_path)

    def read(self, reference: str, *, domain: str) -> dict[str, Any] | None:
        domain = self._normalize_domain_name(domain)
        directory = self._service.show(reference).directory
        if domain not in self._domains_for_directory_info(directory, root_path=self._root_path):
            return None
        path = self._domain_file_path(directory.path, domain=domain)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return self.validate_domain_payload(domain, payload)
        legacy_payload = self._legacy_domain_payload(directory.path, domain=domain)
        if legacy_payload is not None:
            return self.validate_domain_payload(domain, legacy_payload)
        raise ValueError(f"directory config file is missing for {directory.path!r} domain {domain!r}")

    def write(self, reference: str, *, domain: str, payload: dict[str, Any]) -> dict[str, Any]:
        original_domain = domain
        domain = self._normalize_domain_name(domain)
        if original_domain == "providers" and isinstance(payload, dict):
            provider_payload = {
                key: value for key, value in payload.items() if key in DIRECTORY_CONFIG_DOMAIN_FIELDS["providers"]
            }
            email_payload = {
                key: value for key, value in payload.items() if key in DIRECTORY_CONFIG_DOMAIN_FIELDS["email"]
            }
            unknown_fields = sorted(
                set(payload) - DIRECTORY_CONFIG_DOMAIN_FIELDS["providers"] - DIRECTORY_CONFIG_DOMAIN_FIELDS["email"]
            )
            if unknown_fields:
                raise ValueError(
                    f"directory config domain 'providers' does not allow fields: {', '.join(unknown_fields)}"
                )
            if email_payload:
                self.write(reference, domain="email", payload=email_payload)
            payload = provider_payload
        normalized = self.validate_domain_payload(domain, payload)
        directory = self._service.show(reference).directory
        path = self._domain_file_path(directory.path, domain=domain)
        if not normalized:
            if path.exists():
                path.unlink()
            legacy_path = self._legacy_domain_file_path(directory.path, domain=domain)
            if legacy_path is not None and legacy_path.exists():
                legacy_path.unlink()
            self._prune_empty_parent_dirs(path.parent)
            self._remove_domain(directory.id, domain=domain)
            return normalized
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
        self._record_domain(directory.id, domain=domain)
        return normalized

    def delete_directory_tree(self, canonical_path: str) -> None:
        path = self._root_path.joinpath(*DirectoryPath(segments=canonical_path).segments)
        if path.exists():
            shutil.rmtree(path)

    @classmethod
    def validate_domain_payload(cls, domain: str, payload: dict[str, Any]) -> dict[str, Any]:
        domain = cls._normalize_domain_name(domain)
        if not isinstance(payload, dict):
            raise ValueError(f"directory config payload for domain {domain!r} must be a JSON object")
        allowed_fields = DIRECTORY_CONFIG_DOMAIN_FIELDS[domain]
        if domain == "providers":
            compatible_fields = allowed_fields | DIRECTORY_CONFIG_DOMAIN_FIELDS["email"]
            unknown_fields = sorted(set(payload) - compatible_fields)
            if unknown_fields:
                raise ValueError(
                    f"directory config domain {domain!r} does not allow fields: {', '.join(unknown_fields)}"
                )
            payload = {key: value for key, value in payload.items() if key in allowed_fields}
            validated = GatewayConfig.model_validate(payload)
            return validated.model_dump(mode="json", include=set(payload))
        if domain == "ado-worker":
            validated = AdoWorkerDirectoryConfig.model_validate(payload)
            return validated.model_dump(mode="json", include=set(payload))
        unknown_fields = sorted(set(payload) - allowed_fields)
        if unknown_fields:
            raise ValueError(
                f"directory config domain {domain!r} does not allow fields: {', '.join(unknown_fields)}"
            )
        validated = GatewayConfig.model_validate(payload)
        return validated.model_dump(mode="json", include=set(payload))

    @classmethod
    def _validate_domain_name(cls, domain: str) -> None:
        normalized_domain = cls._normalize_domain_name(domain)
        if normalized_domain not in DIRECTORY_CONFIG_DOMAIN_FILENAMES:
            raise ValueError(f"unsupported directory config domain: {domain!r}")

    @staticmethod
    def _normalize_domain_name(domain: str) -> str:
        return LEGACY_DIRECTORY_DOMAIN_ALIASES.get(domain, domain)

    def _domain_file_path(self, canonical_path: str, *, domain: str) -> Path:
        domain = self._normalize_domain_name(domain)
        self._validate_domain_name(domain)
        segments = DirectoryPath(segments=canonical_path).segments
        return self._root_path.joinpath(*segments, DIRECTORY_CONFIG_DOMAIN_FILENAMES[domain])

    def _legacy_domain_file_path(self, canonical_path: str, *, domain: str) -> Path | None:
        legacy_domain = None
        if domain == "heartbeat":
            legacy_domain = "workers"
        elif domain == "vm":
            legacy_domain = "vm-defaults"
        elif domain in {"policy", "providers", "email"}:
            legacy_domain = "providers" if domain in {"providers", "email"} else "policy"
        if legacy_domain is None:
            return None
        segments = DirectoryPath(segments=canonical_path).segments
        return self._root_path.joinpath(*segments, LEGACY_DIRECTORY_CONFIG_DOMAIN_FILENAMES[legacy_domain])

    def _legacy_domain_payload(self, canonical_path: str, *, domain: str) -> dict[str, Any] | None:
        legacy_path = self._legacy_domain_file_path(canonical_path, domain=domain)
        if legacy_path is None or not legacy_path.exists():
            return None
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if domain == "policy":
            return payload
        if domain in {"providers", "email"}:
            source_domain = "providers"
        elif domain == "heartbeat":
            source_domain = "workers"
        elif domain == "vm":
            source_domain = "vm-defaults"
        else:
            return None
        allowed_fields = LEGACY_DOMAIN_FIELD_SUBSETS[source_domain][domain]
        extracted = {key: payload[key] for key in sorted(allowed_fields) if key in payload}
        return extracted or None

    def _record_domain(self, directory_id: str, *, domain: str) -> None:
        directory = self._directory_store.get(directory_id)
        if directory is None:
            raise ValueError(f"directory {directory_id!r} does not exist")
        metadata = deepcopy(directory.metadata)
        config_metadata = metadata.get(DIRECTORY_CONFIG_METADATA_KEY)
        if not isinstance(config_metadata, dict):
            config_metadata = {}
        raw_domains = config_metadata.get(DIRECTORY_CONFIG_METADATA_DOMAINS_KEY, [])
        domains = {
            str(value).strip()
            for value in raw_domains
            if str(value).strip() in DIRECTORY_CONFIG_DOMAIN_FILENAMES
        }
        domains.add(domain)
        config_metadata[DIRECTORY_CONFIG_METADATA_DOMAINS_KEY] = sorted(domains)
        metadata[DIRECTORY_CONFIG_METADATA_KEY] = config_metadata
        self._directory_store.put(directory.model_copy(update={"metadata": metadata}))

    def _remove_domain(self, directory_id: str, *, domain: str) -> None:
        directory = self._directory_store.get(directory_id)
        if directory is None:
            raise ValueError(f"directory {directory_id!r} does not exist")
        metadata = deepcopy(directory.metadata)
        config_metadata = metadata.get(DIRECTORY_CONFIG_METADATA_KEY)
        if not isinstance(config_metadata, dict):
            return
        raw_domains = config_metadata.get(DIRECTORY_CONFIG_METADATA_DOMAINS_KEY, [])
        domains = {
            str(value).strip()
            for value in raw_domains
            if str(value).strip() in DIRECTORY_CONFIG_DOMAIN_FILENAMES
        }
        domains.discard(domain)
        if domains:
            config_metadata[DIRECTORY_CONFIG_METADATA_DOMAINS_KEY] = sorted(domains)
            metadata[DIRECTORY_CONFIG_METADATA_KEY] = config_metadata
        else:
            metadata.pop(DIRECTORY_CONFIG_METADATA_KEY, None)
        self._directory_store.put(directory.model_copy(update={"metadata": metadata}))

    def _prune_empty_parent_dirs(self, path: Path) -> None:
        current = path
        while current != self._root_path and current.exists():
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    @staticmethod
    def _domains_for_directory_info(directory: DirectoryInfo, *, root_path: Path) -> list[str]:
        config_metadata = directory.metadata.get(DIRECTORY_CONFIG_METADATA_KEY)
        raw_domains = config_metadata.get(DIRECTORY_CONFIG_METADATA_DOMAINS_KEY, []) if isinstance(config_metadata, dict) else []
        normalized_domains: set[str] = set()
        if isinstance(raw_domains, list):
            for raw_domain in raw_domains:
                value = str(raw_domain).strip()
                if value in DIRECTORY_CONFIG_DOMAIN_FILENAMES:
                    normalized_domains.add(value)
                    continue
                alias = LEGACY_DIRECTORY_DOMAIN_ALIASES.get(value)
                if alias is not None:
                    normalized_domains.add(alias)
                    continue
                if value == "providers":
                    normalized_domains.add("providers")
                    legacy_path = root_path.joinpath(*DirectoryPath(segments=directory.path).segments, "providers.json")
                    if legacy_path.exists():
                        try:
                            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            payload = {}
                        if isinstance(payload, dict) and any(
                            field in payload for field in DIRECTORY_CONFIG_DOMAIN_FIELDS["email"]
                        ):
                            normalized_domains.add("email")
        return sorted(normalized_domains)


class DirectoryConfigResolver:
    def __init__(
        self,
        service: DirectoryService,
        *,
        base_gateway_config: GatewayConfig | None = None,
        config_store: DirectoryConfigStore | None = None,
    ) -> None:
        self._service = service
        self._base_gateway_config = base_gateway_config
        self._config_store = config_store

    def resolve_gateway(self, reference: str) -> DirectoryConfigResolution:
        return self.resolve_domain(
            reference,
            domain="gateway",
            base_config=(self._base_gateway_config or GatewayConfig()).model_dump(mode="json"),
            validator=GatewayConfig.model_validate,
        )

    def resolve_root_gateway_config(
        self,
        host_config: HostConfig,
        *,
        base_gateway_config: GatewayConfig | None = None,
    ) -> GatewayConfig:
        root_bootstrap = base_gateway_config or build_runtime_gateway_config(host_config)
        payload = root_bootstrap.model_dump(mode="json")
        for domain in ("policy", "providers", "email", "heartbeat", "vm"):
            resolution = self.resolve_domain(
                "root",
                domain=domain,
                base_config=get_domain_base_config(root_bootstrap, domain),
                validator=lambda candidate, domain=domain: validate_effective_domain_payload(domain, candidate),
            )
            payload.update(resolution.effective_config)
        return GatewayConfig.model_validate(payload)

    def resolve_domain(
        self,
        reference: str,
        *,
        domain: str,
        base_config: dict[str, Any] | None = None,
        validator: Callable[[Any], Any] | None = None,
    ) -> DirectoryConfigResolution:
        details = self._service.show(reference)
        effective = deepcopy(base_config) if base_config is not None else {}
        provenance: dict[str, DirectoryConfigValueSource] = {}
        if base_config is not None:
            self._mark_provenance(
                effective,
                (),
                provenance,
                DirectoryConfigValueSource(source_type="base"),
            )

        applied_layers: list[DirectoryConfigLayer] = []
        for directory in details.ancestry:
            overlay = self._overlay_for_directory(directory, domain=domain)
            if overlay is None:
                continue
            applied_layers.append(
                DirectoryConfigLayer(
                    directory_id=directory.id,
                    directory_path=directory.path,
                    domain=domain,
                    overlay=deepcopy(overlay),
                )
            )
            effective = self._merge_values(
                effective,
                overlay,
                (),
                provenance,
                DirectoryConfigValueSource(
                    source_type="directory",
                    directory_id=directory.id,
                    directory_path=directory.path,
                ),
            )

        validation_errors: list[str] = []
        if validator is not None:
            try:
                validator(effective)
            except ValidationError as exc:
                validation_errors = [error["msg"] for error in exc.errors()]
            except ValueError as exc:
                validation_errors = [str(exc)]

        return DirectoryConfigResolution(
            domain=domain,
            selected_directory=details.directory,
            effective_config=effective,
            provenance=dict(sorted(provenance.items())),
            applied_layers=applied_layers,
            validation_errors=validation_errors,
        )

    def _overlay_for_directory(self, directory: DirectoryInfo, *, domain: str) -> dict[str, Any] | None:
        if self._config_store is not None and domain in self._config_store.list_domains(directory.path):
            return self._config_store.read(directory.path, domain=domain)
        overlays = directory.metadata.get(DIRECTORY_CONFIG_OVERLAYS_KEY)
        if not isinstance(overlays, dict):
            return None
        overlay = overlays.get(domain)
        if overlay is None:
            return None
        if not isinstance(overlay, dict):
            raise ValueError(
                f"directory {directory.path!r} metadata.{DIRECTORY_CONFIG_OVERLAYS_KEY}.{domain} must be an object"
            )
        return overlay

    def _merge_values(
        self,
        current: Any,
        overlay: Any,
        path: tuple[str, ...],
        provenance: dict[str, DirectoryConfigValueSource],
        source: DirectoryConfigValueSource,
    ) -> Any:
        if overlay is None:
            self._mark_provenance(None, path, provenance, source)
            return None

        if isinstance(current, dict) and isinstance(overlay, dict):
            merged = deepcopy(current)
            for key in sorted(overlay):
                merged[key] = self._merge_values(merged.get(key), overlay[key], (*path, key), provenance, source)
            return merged

        replacement = deepcopy(overlay)
        self._mark_provenance(replacement, path, provenance, source)
        return replacement

    def _mark_provenance(
        self,
        value: Any,
        path: tuple[str, ...],
        provenance: dict[str, DirectoryConfigValueSource],
        source: DirectoryConfigValueSource,
    ) -> None:
        key = ".".join(path)
        if isinstance(value, dict):
            if not value and key:
                provenance[key] = source
            for child_key in sorted(value):
                self._mark_provenance(value[child_key], (*path, child_key), provenance, source)
            return
        provenance[key] = source


def load_root_gateway_config(
    directory_store: DirectoryStore,
    *,
    runtime_dir: Path,
    host_config: HostConfig,
    base_gateway_config: GatewayConfig | None = None,
) -> GatewayConfig:
    service = DirectoryService(directory_store)
    resolver = DirectoryConfigResolver(
        service,
        config_store=DirectoryConfigStore(directory_store, runtime_dir=runtime_dir),
    )
    return resolver.resolve_root_gateway_config(host_config, base_gateway_config=base_gateway_config)
