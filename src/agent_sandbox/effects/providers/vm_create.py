from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.events.models import VmCreateEffectDefinition

from .base import EffectProviderBase


class VmCreateEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        if context.vm_creator is None:
            raise RuntimeError("effect:vm:create vm_creator is not available")

        payload = {
            "vm_name": effect.vm_name,
            "session_id": effect.session_id,
            "labels": effect.labels,
            "model": effect.model,
            "repo_source": effect.repo_source,
            "vm_user": effect.vm_user,
            "ttl_hours": effect.ttl_hours,
            "allowed_tools": effect.allowed_tools,
            "allowed_script_names": effect.allowed_script_names,
            "discord_channels": effect.discord_channels,
            "email_mailboxes": effect.email_mailboxes,
            "email_folders": effect.email_folders,
            "python": effect.python,
            "dotnet": effect.dotnet,
            "nuget": effect.nuget,
            "mssql": effect.mssql,
            "mssql_init_root": effect.mssql_init_root,
            "playwright": effect.playwright,
            "codex_skills": effect.codex_skills,
            "frontend_deps": effect.frontend_deps,
            "frontend_host_checkout": effect.frontend_host_checkout,
            "frontend_install_roots": effect.frontend_install_roots,
            "directory_path": effect.directory_path,
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

        result = context.vm_creator.create_vm(
            vm_name=vm_name,
            session_id=session_id,
            labels=self._as_string_mapping(payload.get("labels")),
            model=self._normalize_optional_string(payload.get("model")),
            repo_source=self._normalize_optional_string(payload.get("repo_source")),
            vm_user=vm_user,
            ttl_hours=self._as_int_or_none(payload.get("ttl_hours")),
            allowed_tools=self._as_string_list(payload.get("allowed_tools")),
            allowed_script_names=self._as_string_list(payload.get("allowed_script_names")),
            discord_channels=self._as_string_list(payload.get("discord_channels")),
            email_mailboxes=self._as_string_list(payload.get("email_mailboxes")),
            email_folders=self._as_string_list(payload.get("email_folders")),
            python=bool(payload.get("python", False)),
            dotnet=bool(payload.get("dotnet", False)),
            nuget=bool(payload.get("nuget", False)),
            mssql=bool(payload.get("mssql", False)),
            mssql_init_root=self._normalize_optional_string(payload.get("mssql_init_root")),
            playwright=bool(payload.get("playwright", False)),
            codex_skills=self._as_string_list(payload.get("codex_skills")),
            frontend_deps=bool(payload.get("frontend_deps", False)),
            frontend_host_checkout=self._normalize_optional_string(payload.get("frontend_host_checkout")),
            frontend_install_roots=self._as_string_list(payload.get("frontend_install_roots")),
            directory_path=directory_path,
        )

        resolved_session_id = session_id or f"session-{vm_name}"
        return {
            "session_id": resolved_session_id,
            "vm_name": result.vm_name or vm_name,
            "labels": self._as_string_mapping(payload.get("labels")) or {},
            "success": bool(result.success),
            "message": self._normalize_optional_string(getattr(result, "message", None)),
            "target_dir": self._normalize_optional_string(getattr(result, "target_dir", None)),
            "guest_repo_name": self._normalize_optional_string(getattr(result, "guest_repo_name", None)),
            "remote_url": self._normalize_optional_string(getattr(result, "remote_url", None)),
        }

    def _effect(self) -> VmCreateEffectDefinition:
        if not isinstance(self.effect_definition, VmCreateEffectDefinition):
            raise RuntimeError("VmCreateEffectProvider initialized with a non-vm-create effect definition")
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
