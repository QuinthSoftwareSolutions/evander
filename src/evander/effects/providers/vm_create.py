from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.contact_allowlists import validate_allowed_contact_ids_for_directory
from evander.connectors.grants import resolve_and_validate_connector_grants
from evander.events.provider_models import VmCreateEffectDefinition
from evander.host_directories.grants import resolve_and_validate_host_directory_grants
from evander.provider_config import MODEL_PROVIDER_MODE_METADATA_KEY

from .agent_session import contacts_config_for_directory

from .base import EffectProviderBase


class VmCreateEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        if context.vm_creator is None:
            raise RuntimeError("effect:vm:create vm_creator is not available")

        payload = {
            "provider": effect.provider,
            "vm_name": effect.vm_name,
            "session_id": effect.session_id,
            "labels": effect.labels,
            "model": effect.model,
            MODEL_PROVIDER_MODE_METADATA_KEY: effect.model_provider_mode,
            "vm_user": effect.vm_user,
            "ttl_hours": effect.ttl_hours,
            "allowed_tools": effect.allowed_tools,
            "connector_grants": effect.connector_grants,
            "host_directory_grants": effect.host_directory_grants,
            "tool_argument_policies": effect.tool_argument_policies,
            "allowed_script_names": effect.allowed_script_names,
            "allowed_contact_ids": effect.allowed_contact_ids,
            "discord_channels": effect.discord_channels,
            "email_mailboxes": effect.email_mailboxes,
            "email_folders": effect.email_folders,
            "dotnet": effect.dotnet,
            "nuget": effect.nuget,
            "mssql": effect.mssql,
            "mssql_init_root": effect.mssql_init_root,
            "playwright": effect.playwright,
            "codex_skills": effect.codex_skills,
            "directory_path": effect.directory_path,
            "metadata": effect.metadata,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        vm_name = str(payload.get("vm_name") or "").strip()
        if not vm_name:
            raise ValueError("effect:vm:create requires vm_name")

        session_id = self._normalize_optional_string(payload.get("session_id"))
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        directory_path = self._normalize_optional_string(payload.get("directory_path")) or str(
            context.metadata.get("directory_path") or "root"
        )
        resolved_contact_ids = None
        if payload.get("allowed_contact_ids") is not None:
            resolved_contact_ids = validate_allowed_contact_ids_for_directory(
                contacts_config_for_directory(context, directory_path),
                payload.get("allowed_contact_ids"),
                session_id=session_id or f"session-{vm_name}",
                directory_path=directory_path,
            )
        codex_skills = self._as_string_list(payload.get("codex_skills"))
        skill_validator = getattr(context.admin_service, "validate_managed_codex_skills", None)
        if callable(skill_validator):
            codex_skills = skill_validator(codex_skills)
        connector_grants = _validated_connector_grants(
            context,
            directory_path=directory_path,
            session_id=session_id or f"session-{vm_name}",
            value=payload.get("connector_grants"),
        )
        host_directory_grants = _validated_host_directory_grants(
            context,
            directory_path=directory_path,
            session_id=session_id or f"session-{vm_name}",
            value=payload.get("host_directory_grants"),
        )

        result = context.vm_creator.create_vm(
            provider=self._normalize_optional_string(payload.get("provider")),
            vm_name=vm_name,
            session_id=session_id,
            labels=self._as_string_mapping(payload.get("labels")),
            model=self._normalize_optional_string(payload.get("model")),
            model_provider_mode=self._normalize_optional_string(
                payload.get(MODEL_PROVIDER_MODE_METADATA_KEY)
            ),
            vm_user=vm_user,
            ttl_hours=self._as_int_or_none(payload.get("ttl_hours")),
            allowed_tools=self._as_string_list(payload.get("allowed_tools")),
            connector_grants=connector_grants,
            host_directory_grants=host_directory_grants,
            tool_argument_policies=payload.get("tool_argument_policies"),
            allowed_script_names=self._as_string_list(payload.get("allowed_script_names")),
            allowed_contact_ids=resolved_contact_ids,
            discord_channels=self._as_string_list(payload.get("discord_channels")),
            email_mailboxes=self._as_string_list(payload.get("email_mailboxes")),
            email_folders=self._as_string_list(payload.get("email_folders")),
            dotnet=bool(payload.get("dotnet", False)),
            nuget=bool(payload.get("nuget", False)),
            mssql=bool(payload.get("mssql", False)),
            mssql_init_root=self._normalize_optional_string(payload.get("mssql_init_root")),
            playwright=bool(payload.get("playwright", False)),
            codex_skills=codex_skills,
            directory_path=directory_path,
            metadata=self._metadata_patch(payload.get("metadata")),
        )

        resolved_session_id = session_id or f"session-{vm_name}"
        return {
            "provider": self._normalize_optional_string(getattr(result, "provider", None))
            or self._normalize_optional_string(payload.get("provider"))
            or "libvirt",
            "session_id": resolved_session_id,
            "vm_name": result.vm_name or vm_name,
            "labels": self._as_string_mapping(payload.get("labels")) or {},
            "success": bool(result.success),
            "message": self._normalize_optional_string(getattr(result, "message", None)),
        }

    def _effect(self) -> VmCreateEffectDefinition:
        if not isinstance(self.effect_definition, VmCreateEffectDefinition):
            raise RuntimeError(
                "VmCreateEffectProvider initialized with a non-vm-create effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("VmCreateEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _as_int_or_none(value: Any) -> int | None:
        if value is None or value == "":
            return None
        return int(value)

    @staticmethod
    def _as_string_list(value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return None
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized or None

    @staticmethod
    def _as_string_mapping(value: Any) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            return None
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key or raw_value is None:
                continue
            mapped_value = str(raw_value).strip()
            if mapped_value:
                normalized[key] = mapped_value
        return normalized or None

    @staticmethod
    def _metadata_patch(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("effect:vm:create metadata must resolve to a JSON object")
        return dict(value)


def _validated_connector_grants(
    context: Any,
    *,
    directory_path: str,
    session_id: str,
    value: object,
) -> dict[str, list[str]] | None:
    if value is None:
        return None
    resolver = getattr(context, "provider_config_resolver", None)
    return resolve_and_validate_connector_grants(
        resolver,
        directory_path=directory_path,
        session_id=session_id,
        value=value,
    )


def _validated_host_directory_grants(
    context: Any,
    *,
    directory_path: str,
    session_id: str,
    value: object,
) -> dict[str, list[str]] | None:
    if value is None:
        return None
    return resolve_and_validate_host_directory_grants(
        provider_config=getattr(context, "provider_config_resolver", None),
        directory_path=directory_path,
        session_id=session_id,
        value=value,
    )
