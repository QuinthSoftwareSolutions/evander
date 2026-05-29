from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from agent_sandbox.events.provider_models import AgentCreateEffectDefinition

from .agent_session import (
    allowed_tools_for_profile,
    as_bool_or_none,
    as_int_or_none,
    as_string_list,
    as_string_mapping,
    build_container_session_files,
    build_container_session_manifest,
    container_backend,
    effective_config_for_directory,
    normalize_existing_agent_behavior,
    normalize_optional_string,
    runtime_session_repository,
    selected_execution_backend,
)
from .base import EffectProviderBase
from .codex_skills import container_codex_skill_files, managed_codex_skill_names

logger = logging.getLogger(__name__)


class AgentCreateEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "provider": effect.provider,
            "execution_backend": effect.execution_backend,
            "vm_name": effect.vm_name,
            "session_id": effect.session_id,
            "existing_agent_behavior": effect.existing_agent_behavior,
            "labels": effect.labels,
            "model": effect.model,
            "vm_user": effect.vm_user,
            "ttl_hours": effect.ttl_hours,
            "capability_profile": effect.capability_profile,
            "allowed_tools": effect.allowed_tools,
            "tool_argument_policies": effect.tool_argument_policies,
            "allowed_script_names": effect.allowed_script_names,
            "discord_channels": effect.discord_channels,
            "teams_channel_allowlist": effect.teams_channel_allowlist,
            "teams_chat_allowlist": effect.teams_chat_allowlist,
            "teams_user_allowlist": effect.teams_user_allowlist,
            "email_mailboxes": effect.email_mailboxes,
            "email_folders": effect.email_folders,
            "communication_file_receive_enabled": effect.communication_file_receive_enabled,
            "communication_file_send_enabled": effect.communication_file_send_enabled,
            "communication_file_max_bytes": effect.communication_file_max_bytes,
            "communication_file_require_approval_for_send": (
                effect.communication_file_require_approval_for_send
            ),
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

        directory_path = normalize_optional_string(payload.get("directory_path")) or str(
            context.metadata.get("directory_path") or "root"
        )
        execution_backend, config = selected_execution_backend(
            context, payload, directory_path=directory_path
        )
        logger.info(
            "metric_agent_create_backend_path backend=%s session_id=%s vm_name=%s directory_path=%s",
            execution_backend,
            normalize_optional_string(payload.get("session_id")) or "",
            normalize_optional_string(payload.get("vm_name")) or "",
            directory_path,
        )
        if execution_backend == "libvirt_vm":
            return self._execute_libvirt(
                payload,
                config=config,
                directory_path=directory_path,
                execution_backend=execution_backend,
            )
        if execution_backend != "container_worker_vm":
            raise ValueError(f"Unsupported execution_backend: {execution_backend}")
        return self._execute_container(payload, config=config, directory_path=directory_path)

    def _execute_libvirt(
        self,
        payload: dict[str, Any],
        *,
        config: Any,
        directory_path: str,
        execution_backend: str,
    ) -> dict[str, Any]:
        context = self._context()
        if context.vm_creator is None:
            raise RuntimeError("effect:agent:create vm_creator is not available")
        vm_name = normalize_optional_string(payload.get("vm_name"))
        if not vm_name:
            raise ValueError("effect:agent:create with libvirt_vm requires vm_name")
        session_id = normalize_optional_string(payload.get("session_id"))
        existing_agent_behavior = normalize_existing_agent_behavior(
            payload.get("existing_agent_behavior")
        )
        if existing_agent_behavior == "noop":
            existing_session = _existing_session_for_create(
                runtime_session_repository(context, config),
                session_id=session_id,
                vm_name=vm_name,
            )
            if existing_session is not None:
                return _existing_agent_noop_result(
                    session_id=existing_session.session_id,
                    execution_backend=execution_backend,
                    vm_name=existing_session.vm_id or vm_name,
                )
        codex_skills = managed_codex_skill_names(
            context=context,
            config=context.resolved_config,
            skill_names=as_string_list(payload.get("codex_skills")),
        )
        result = context.vm_creator.create_vm(
            provider=normalize_optional_string(payload.get("provider")),
            vm_name=vm_name,
            session_id=session_id,
            labels=as_string_mapping(payload.get("labels")),
            model=normalize_optional_string(payload.get("model")),
            vm_user=normalize_optional_string(payload.get("vm_user")) or "agent",
            ttl_hours=as_int_or_none(payload.get("ttl_hours")),
            allowed_tools=as_string_list(payload.get("allowed_tools")),
            tool_argument_policies=payload.get("tool_argument_policies"),
            allowed_script_names=as_string_list(payload.get("allowed_script_names")),
            discord_channels=as_string_list(payload.get("discord_channels")),
            teams_channel_allowlist=as_string_list(payload.get("teams_channel_allowlist")),
            teams_chat_allowlist=as_string_list(payload.get("teams_chat_allowlist")),
            teams_user_allowlist=as_string_list(payload.get("teams_user_allowlist")),
            email_mailboxes=as_string_list(payload.get("email_mailboxes")),
            email_folders=as_string_list(payload.get("email_folders")),
            communication_file_receive_enabled=as_bool_or_none(
                payload.get("communication_file_receive_enabled")
            ),
            communication_file_send_enabled=as_bool_or_none(
                payload.get("communication_file_send_enabled")
            ),
            communication_file_max_bytes=as_int_or_none(
                payload.get("communication_file_max_bytes")
            ),
            communication_file_require_approval_for_send=as_bool_or_none(
                payload.get("communication_file_require_approval_for_send")
            ),
            dotnet=bool(payload.get("dotnet", False)),
            nuget=bool(payload.get("nuget", False)),
            mssql=bool(payload.get("mssql", False)),
            mssql_init_root=normalize_optional_string(payload.get("mssql_init_root")),
            playwright=bool(payload.get("playwright", False)),
            codex_skills=codex_skills,
            directory_path=normalize_optional_string(payload.get("directory_path")) or directory_path,
            metadata=_metadata_patch(payload.get("metadata")),
            allow_existing_session=existing_agent_behavior == "upsert",
        )
        return {
            "success": bool(result.success),
            "message": normalize_optional_string(getattr(result, "message", None)) or "",
            "session_id": session_id or f"session-{vm_name}",
            "execution_backend": execution_backend,
            "vm_name": result.vm_name or vm_name,
            "skipped": False,
        }

    def _execute_container(
        self,
        payload: dict[str, Any],
        *,
        config: Any,
        directory_path: str,
    ) -> dict[str, Any]:
        context = self._context()
        session_id = normalize_optional_string(payload.get("session_id"))
        if not session_id:
            raise ValueError("effect:agent:create with container_worker_vm requires session_id")
        session_repository = runtime_session_repository(context, config)
        existing_agent_behavior = normalize_existing_agent_behavior(
            payload.get("existing_agent_behavior")
        )
        if (
            existing_agent_behavior == "noop"
            and session_repository.get_session(session_id) is not None
        ):
            return _existing_agent_noop_result(
                session_id=session_id,
                execution_backend="container_worker_vm",
                vm_name=None,
            )
        if bool(payload.get("mssql", False)) or normalize_optional_string(
            payload.get("mssql_init_root")
        ):
            raise ValueError(
                "effect:agent:create with container_worker_vm does not support MSSQL yet"
            )
        codex_skill_files = container_codex_skill_files(
            context=context,
            config=config,
            skill_names=as_string_list(payload.get("codex_skills")),
        )
        labels = as_string_mapping(payload.get("labels")) or {}
        model = normalize_optional_string(payload.get("model"))
        metadata = {
            "execution_backend": "container_worker_vm",
            "labels": labels,
        }
        capability_profile = normalize_optional_string(payload.get("capability_profile"))
        if capability_profile:
            metadata["capability_profile"] = capability_profile
        if model:
            metadata["model"] = model
        requested_tools = as_string_list(payload.get("allowed_tools"))
        if requested_tools:
            resolved_allowed_tools = requested_tools
        else:
            resolved_allowed_tools = allowed_tools_for_profile(capability_profile)
        backend = container_backend(context, config)
        worker_url = normalize_optional_string(
            getattr(getattr(backend, "client", None), "base_url", None)
        ) or normalize_optional_string(
            getattr(getattr(backend, "config", None), "container_worker_base_url", None)
        )
        if worker_url:
            metadata["worker_url"] = worker_url
        metadata = _deep_merge_metadata(metadata, _metadata_patch(payload.get("metadata")))
        manifest = build_container_session_manifest(
            config=effective_config_for_directory(context, directory_path),
            session_id=session_id,
            directory_path=directory_path,
            model=model,
            ttl_hours=as_int_or_none(payload.get("ttl_hours")),
            allowed_tools=resolved_allowed_tools,
            tool_argument_policies=payload.get("tool_argument_policies"),
            allowed_script_names=as_string_list(payload.get("allowed_script_names")),
            discord_channels=as_string_list(payload.get("discord_channels")),
            teams_channel_allowlist=as_string_list(payload.get("teams_channel_allowlist")),
            teams_chat_allowlist=as_string_list(payload.get("teams_chat_allowlist")),
            teams_user_allowlist=as_string_list(payload.get("teams_user_allowlist")),
            email_mailboxes=as_string_list(payload.get("email_mailboxes")),
            email_folders=as_string_list(payload.get("email_folders")),
            communication_file_receive_enabled=as_bool_or_none(
                payload.get("communication_file_receive_enabled")
            ),
            communication_file_send_enabled=as_bool_or_none(
                payload.get("communication_file_send_enabled")
            ),
            communication_file_max_bytes=as_int_or_none(
                payload.get("communication_file_max_bytes")
            ),
            communication_file_require_approval_for_send=as_bool_or_none(
                payload.get("communication_file_require_approval_for_send")
            ),
            metadata=metadata,
        )
        session_record = backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": directory_path,
                "labels": labels,
                **({"worker_url": worker_url} if worker_url else {}),
            },
        )
        backend.write_files(
            session_id=session_id,
            files=[
                *build_container_session_files(
                    config=config,
                    session_id=session_id,
                    model=model,
                    dotnet=bool(payload.get("dotnet", False)),
                    nuget=bool(payload.get("nuget", False)),
                    playwright=bool(payload.get("playwright", False)),
                ),
                *codex_skill_files,
            ],
        )
        session_repository.upsert_session(manifest)
        return {
            "success": True,
            "message": f"Prepared container worker session {session_id} with agent bootstrap files",
            "session_id": session_id,
            "execution_backend": "container_worker_vm",
            "vm_name": None,
            "agent_home": session_record.agent_home_container_path,
            "repo_root": session_record.repo_root_container_path,
            "skipped": False,
        }

    def _effect(self) -> AgentCreateEffectDefinition:
        if not isinstance(self.effect_definition, AgentCreateEffectDefinition):
            raise RuntimeError(
                "AgentCreateEffectProvider initialized with a non-agent-create effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AgentCreateEffectProvider has no runtime context")
        return self.runtime_context


def _metadata_patch(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("effect:agent:create metadata must resolve to a JSON object")
    return dict(value)


def _deep_merge_metadata(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in update.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge_metadata(existing, value)
            continue
        merged[key] = value
    return merged


def _existing_session_for_create(
    repository: Any,
    *,
    session_id: str | None,
    vm_name: str | None,
) -> Any | None:
    if session_id:
        existing = repository.get_session(session_id)
        if existing is not None:
            return existing
    if vm_name:
        matches = repository.list_sessions_for_vm(vm_name)
        if matches:
            return matches[-1]
    return None


def _existing_agent_noop_result(
    *,
    session_id: str,
    execution_backend: str,
    vm_name: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": True,
        "message": f"Agent session already exists: {session_id}; skipped create",
        "session_id": session_id,
        "execution_backend": execution_backend,
        "vm_name": vm_name,
        "skipped": True,
    }
    if execution_backend == "container_worker_vm":
        result["agent_home"] = "/home/agent"
        result["repo_root"] = "/home/agent/repos"
    return result
