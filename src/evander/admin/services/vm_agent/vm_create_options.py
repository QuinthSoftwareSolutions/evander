from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    Any,
    NormalizedVmCreationOptions,
    ToolName,
    VmActionResult,
    _deep_merge_mapping,
    normalize_model_provider_mode_override,
    validate_allowed_contact_ids_for_directory,
)
from evander.fast_mode import FastModeValue, FAST_MODE_UNSET


class VmAgentCreateOptionsMixin:
    def normalize_vm_creation_options(
        self,
        *,
        provider: str | None = None,
        vm_name: str,
        session_id: str | None = None,
        allowed_contact_ids: list[str] | None = None,
        teams_channel_allowlist: list[str] | None = None,
        teams_chat_allowlist: list[str] | None = None,
        teams_user_allowlist: list[str] | None = None,
        communication_file_receive_enabled: bool | None = None,
        communication_file_send_enabled: bool | None = None,
        communication_file_max_bytes: int | None = None,
        communication_file_require_approval_for_send: bool | None = None,
        web_search_enabled: bool | None = None,
        image_generation_enabled: bool | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
        model_provider_mode: str | None = None,
    ) -> NormalizedVmCreationOptions:
        payload: dict[str, Any] = {}
        if teams_channel_allowlist is not None:
            payload["teams_channel_allowlist"] = teams_channel_allowlist
        if teams_chat_allowlist is not None:
            payload["teams_chat_allowlist"] = teams_chat_allowlist
        if teams_user_allowlist is not None:
            payload["teams_user_allowlist"] = teams_user_allowlist
        if communication_file_receive_enabled is not None:
            payload["communication_file_receive_enabled"] = communication_file_receive_enabled
        if communication_file_send_enabled is not None:
            payload["communication_file_send_enabled"] = communication_file_send_enabled
        if communication_file_max_bytes is not None:
            payload["communication_file_max_bytes"] = communication_file_max_bytes
        if communication_file_require_approval_for_send is not None:
            payload["communication_file_require_approval_for_send"] = (
                communication_file_require_approval_for_send
            )
        if web_search_enabled is not None:
            payload["web_search_enabled"] = web_search_enabled
        if image_generation_enabled is not None:
            payload["image_generation_enabled"] = image_generation_enabled
        if metadata is not None:
            payload["metadata"] = metadata

        resolved_model_provider_mode = normalize_model_provider_mode_override(model_provider_mode)
        if resolved_model_provider_mode is not None:
            payload["metadata"] = _deep_merge_mapping(
                payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
                {MODEL_PROVIDER_MODE_METADATA_KEY: resolved_model_provider_mode},
            )

        resolved_allowed_contact_ids = self._validate_allowed_contact_ids_for_directory(
            session_id=session_id or vm_name,
            directory_path=directory_path,
            allowed_contact_ids=allowed_contact_ids,
        )
        return NormalizedVmCreationOptions(
            provider=provider,
            allowed_contact_ids=resolved_allowed_contact_ids,
            payload=payload,
        )

    def _validate_allowed_contact_ids_for_directory(
        self,
        *,
        session_id: str,
        directory_path: str,
        allowed_contact_ids: object,
    ) -> list[str] | None:
        if allowed_contact_ids is None:
            return None
        provider_config = getattr(self._deps.runtime, "provider_config", None)
        resolve_contacts_for_directory = getattr(
            provider_config, "resolve_contacts_for_directory", None
        )
        if not callable(resolve_contacts_for_directory):
            raise RuntimeError("contacts config resolver is not available")
        contacts_config = resolve_contacts_for_directory(directory_path)
        return validate_allowed_contact_ids_for_directory(
            contacts_config,
            allowed_contact_ids,
            session_id=session_id,
            directory_path=directory_path,
        )

    def create_vm(
        self,
        *,
        provider: str | None = None,
        vm_name: str,
        session_id: str | None = None,
        labels: dict[str, str] | None = None,
        model: str | None = None,
        cost_category: str | None = None,
        reasoning_effort: str | None = None,
        fast_mode: FastModeValue | None | object = FAST_MODE_UNSET,
        model_provider_mode: str | None = None,
        vm_user: str = "agent",
        ttl_seconds: int | None = None,
        ttl_hours: int | None = None,
        allowed_tools: list[str | ToolName] | None = None,
        connector_grants: dict[str, list[str]] | None = None,
        host_directory_grants: dict[str, list[str]] | None = None,
        tool_argument_policies: dict[str, list[Any]] | None = None,
        allowed_script_names: list[str] | None = None,
        allowed_contact_ids: list[str] | None = None,
        discord_channels: list[str] | None = None,
        teams_channel_allowlist: list[str] | None = None,
        teams_chat_allowlist: list[str] | None = None,
        teams_user_allowlist: list[str] | None = None,
        communication_target_ids: list[str] | None = None,
        email_mailbox_grants: list[Any] | None = None,
        email_mailboxes: list[str] | None = None,
        email_folders: list[str] | None = None,
        communication_file_receive_enabled: bool | None = None,
        communication_file_send_enabled: bool | None = None,
        communication_file_max_bytes: int | None = None,
        communication_file_require_approval_for_send: bool | None = None,
        web_search_enabled: bool | None = None,
        image_generation_enabled: bool | None = None,
        dotnet: bool = False,
        nuget: bool = False,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
        allow_existing_session: bool = False,
    ) -> VmActionResult:
        normalized_options = self.normalize_vm_creation_options(
            provider=provider,
            vm_name=vm_name,
            session_id=session_id,
            allowed_contact_ids=allowed_contact_ids,
            teams_channel_allowlist=teams_channel_allowlist,
            teams_chat_allowlist=teams_chat_allowlist,
            teams_user_allowlist=teams_user_allowlist,
            communication_file_receive_enabled=communication_file_receive_enabled,
            communication_file_send_enabled=communication_file_send_enabled,
            communication_file_max_bytes=communication_file_max_bytes,
            communication_file_require_approval_for_send=(
                communication_file_require_approval_for_send
            ),
            web_search_enabled=web_search_enabled,
            image_generation_enabled=image_generation_enabled,
            directory_path=directory_path,
            metadata=metadata,
            model_provider_mode=model_provider_mode,
        )
        directory_path = str(directory_path or "root")
        vm_defaults = self._deps.resolve_vm_defaults(directory_path)
        resolved_provider = (
            str(provider or getattr(vm_defaults, "provider", None) or "libvirt").strip()
            or "libvirt"
        )
        return (
            self._provider_registry()
            .get(resolved_provider)
            .create_vm(
                vm_name=vm_name,
                session_id=session_id,
                labels=labels,
                model=model,
                cost_category=cost_category,
                reasoning_effort=reasoning_effort,
                fast_mode=fast_mode,
                vm_user=vm_user,
                ttl_seconds=ttl_seconds,
                ttl_hours=ttl_hours,
                allowed_tools=allowed_tools,
                connector_grants=connector_grants,
                host_directory_grants=host_directory_grants,
                tool_argument_policies=tool_argument_policies,
                allowed_script_names=allowed_script_names,
                discord_channels=discord_channels,
                communication_target_ids=communication_target_ids,
                email_mailbox_grants=email_mailbox_grants,
                email_mailboxes=email_mailboxes,
                email_folders=email_folders,
                dotnet=dotnet,
                nuget=nuget,
                mssql=mssql,
                mssql_init_root=mssql_init_root,
                playwright=playwright,
                codex_skills=codex_skills,
                directory_path=directory_path,
                allow_existing_session=allow_existing_session,
                provider=resolved_provider,
                **normalized_options.provider_kwargs(),
            )
        )
