from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from evander.container_worker_images import resolve_container_worker_image_selector
from evander.codex_auth import (
    DEFAULT_SUBSCRIPTION_PROFILE_ID,
    CodexAuthMode,
    normalize_codex_auth_mode,
    normalize_subscription_profile_id,
)
from evander.connectors.grants import resolve_and_validate_connector_grants
from evander.contact_allowlists import validate_allowed_contact_ids_for_directory
from evander.directories import DirectoryStore
from evander.events.provider_models import AgentCreateEffectDefinition
from evander.host_directories.grants import resolve_and_validate_host_directory_grants
from evander.messaging.targets import load_communication_targets_config
from evander.provider_config import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    normalize_model_provider_mode_override,
)
from evander.services import parse_teams_target
from evander.session_auth import ensure_session_mcp_auth_token
from evander.email.grants import (
    normalize_email_mailbox_grants,
    validate_email_mailbox_grants,
)
from evander.models import EmailMailboxGrant
from evander.vm.templates import resolve_libvirt_vm_template_selector

from .agent_session import (
    allowed_tools_for_profile,
    as_bool_or_none,
    as_int_or_none,
    as_string_list,
    as_string_mapping,
    build_container_session_files,
    build_container_session_manifest,
    container_backend,
    contacts_config_for_directory,
    default_agent_provider_for_config,
    effective_config_for_directory,
    normalize_existing_agent_behavior,
    normalize_optional_http_url,
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
        effect_fields_set = getattr(effect, "model_fields_set", set())
        allowed_contact_ids_provided = "allowed_contact_ids" in effect_fields_set
        codex_auth_mode_provided = "codex_auth_mode" in effect_fields_set
        subscription_profile_id_provided = "subscription_profile_id" in effect_fields_set
        payload = {
            "provider": effect.provider,
            "agent_provider": effect.agent_provider,
            "execution_backend": effect.execution_backend,
            "worker_id": effect.worker_id,
            "vm_name": effect.vm_name,
            "session_id": effect.session_id,
            "existing_agent_behavior": effect.existing_agent_behavior,
            "labels": effect.labels,
            "model": effect.model,
            "model_provider_mode": effect.model_provider_mode,
            "subscription_profile_id": effect.subscription_profile_id,
            "vm_user": effect.vm_user,
            "vm_image": effect.vm_image,
            "ttl_hours": effect.ttl_hours,
            "capability_profile": effect.capability_profile,
            "allowed_tools": effect.allowed_tools,
            "connector_grants": effect.connector_grants,
            "host_directory_grants": effect.host_directory_grants,
            "tool_argument_policies": effect.tool_argument_policies,
            "allowed_script_names": effect.allowed_script_names,
            "allowed_contact_ids": effect.allowed_contact_ids,
            "communication_target_ids": effect.communication_target_ids,
            "email_mailbox_grants": effect.email_mailbox_grants,
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
            "web_search_enabled": effect.web_search_enabled,
            "image_generation_enabled": effect.image_generation_enabled,
            "dotnet": effect.dotnet,
            "nuget": effect.nuget,
            "mssql": effect.mssql,
            "mssql_init_root": effect.mssql_init_root,
            "playwright": effect.playwright,
            "codex_skills": effect.codex_skills,
            "container_image": effect.container_image,
            "npm_registry_url": effect.npm_registry_url,
            "directory_path": effect.directory_path,
            "metadata": effect.metadata,
        }
        if codex_auth_mode_provided:
            payload["codex_auth_mode"] = effect.codex_auth_mode
        if subscription_profile_id_provided:
            payload["subscription_profile_id"] = effect.subscription_profile_id
        if resolved_config:
            resolved_payload = dict(resolved_config)
            if "allowed_contact_ids" in resolved_payload:
                allowed_contact_ids_provided = True
            if "codex_auth_mode" in resolved_payload:
                codex_auth_mode_provided = True
            if "subscription_profile_id" in resolved_payload:
                subscription_profile_id_provided = True
            payload.update(resolved_payload)
        payload["_allowed_contact_ids_provided"] = allowed_contact_ids_provided
        payload["_codex_auth_mode_provided"] = codex_auth_mode_provided
        payload["_subscription_profile_id_provided"] = subscription_profile_id_provided

        directory_path = normalize_optional_string(payload.get("directory_path")) or str(
            context.metadata.get("directory_path") or "root"
        )
        execution_backend, config = selected_execution_backend(
            context, payload, directory_path=directory_path
        )
        if (
            normalize_optional_string(payload.get("worker_id"))
            and execution_backend != "container_worker_vm"
        ):
            raise ValueError(
                "worker_id is supported only with execution_backend='container_worker_vm'"
            )
        if (
            normalize_subscription_profile_id(payload.get("subscription_profile_id")) is not None
            and execution_backend != "container_worker_vm"
        ):
            raise ValueError(
                "subscription_profile_id is supported only with "
                "execution_backend='container_worker_vm'"
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
        session_repository = runtime_session_repository(context, config)
        existing_session = _existing_session_for_create(
            session_repository,
            session_id=session_id,
            vm_name=vm_name,
        )
        resolved_contact_ids = None
        if payload.get("allowed_contact_ids") is not None:
            resolved_contact_ids = validate_allowed_contact_ids_for_directory(
                contacts_config_for_directory(context, directory_path),
                payload.get("allowed_contact_ids"),
                session_id=session_id or f"session-{vm_name}",
                directory_path=directory_path,
            )

        vm_image_selector = normalize_optional_string(payload.get("vm_image"))
        if existing_agent_behavior == "noop" and existing_session is not None:
            vm_image_selection = _libvirt_vm_template_selection_from_session(existing_session)
            return _existing_agent_noop_result(
                session_id=existing_session.session_id,
                execution_backend=execution_backend,
                vm_name=existing_session.vm_id or vm_name,
                vm_image=vm_image_selection["image_ref"] if vm_image_selection else None,
                vm_image_alias=vm_image_selection["template_alias"] if vm_image_selection else None,
            )
        vm_image_selection: dict[str, str | None] | None = None
        record_vm_image_selection = False
        if vm_image_selector is not None or existing_session is None:
            vm_defaults = _resolve_vm_defaults_for_create(context, directory_path=directory_path)
            vm_image_selection = resolve_libvirt_vm_template_selector(
                vm_image_selector,
                default_template=vm_defaults.libvirt_vm_default_template,
                templates=vm_defaults.libvirt_vm_templates,
            )
            record_vm_image_selection = True
        elif existing_session is not None:
            vm_image_selection = _libvirt_vm_template_selection_from_session(existing_session)
        codex_skills = managed_codex_skill_names(
            context=context,
            config=context.resolved_config,
            skill_names=as_string_list(payload.get("codex_skills")),
        )
        metadata = _metadata_patch(payload.get("metadata"))
        if existing_session is not None and vm_image_selector is None:
            metadata.pop("libvirt_vm_template", None)
        agent_provider = normalize_optional_string(payload.get("agent_provider"))
        if agent_provider:
            metadata = _deep_merge_metadata(metadata, {"agent_provider": agent_provider})
        model_provider_mode = normalize_model_provider_mode_override(
            payload.get(MODEL_PROVIDER_MODE_METADATA_KEY)
        )
        if model_provider_mode:
            metadata = _deep_merge_metadata(
                metadata, {MODEL_PROVIDER_MODE_METADATA_KEY: model_provider_mode}
            )
        if record_vm_image_selection and vm_image_selection is not None:
            metadata = _deep_merge_metadata(
                metadata,
                {
                    "libvirt_vm_template": {
                        "image_ref": vm_image_selection["image_ref"],
                        "template_alias": vm_image_selection["template_alias"],
                    }
                },
            )
        communication_allowlists = _agent_communication_allowlists(
            payload,
            config=config,
            directory_path=directory_path,
        )
        email_mailbox_grants = _agent_email_mailbox_grants(
            payload,
            context=context,
            config=config,
            directory_path=directory_path,
        )
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
            provider=normalize_optional_string(payload.get("provider")),
            vm_name=vm_name,
            session_id=session_id,
            labels=as_string_mapping(payload.get("labels")),
            model=normalize_optional_string(payload.get("model")),
            vm_user=normalize_optional_string(payload.get("vm_user")) or "agent",
            ttl_hours=as_int_or_none(payload.get("ttl_hours")),
            allowed_tools=as_string_list(payload.get("allowed_tools")),
            connector_grants=connector_grants,
            host_directory_grants=host_directory_grants,
            tool_argument_policies=payload.get("tool_argument_policies"),
            allowed_script_names=as_string_list(payload.get("allowed_script_names")),
            allowed_contact_ids=resolved_contact_ids,
            discord_channels=communication_allowlists["discord_channels"],
            teams_channel_allowlist=communication_allowlists["teams_channel_allowlist"],
            teams_chat_allowlist=communication_allowlists["teams_chat_allowlist"],
            teams_user_allowlist=communication_allowlists["teams_user_allowlist"],
            communication_target_ids=communication_allowlists["communication_target_ids"],
            email_mailbox_grants=email_mailbox_grants,
            email_mailboxes=communication_allowlists["email_mailboxes"],
            email_folders=communication_allowlists["email_folders"],
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
            web_search_enabled=as_bool_or_none(payload.get("web_search_enabled")),
            image_generation_enabled=as_bool_or_none(
                payload.get("image_generation_enabled")
            ),
            dotnet=bool(payload.get("dotnet", False)),
            nuget=bool(payload.get("nuget", False)),
            mssql=bool(payload.get("mssql", False)),
            mssql_init_root=normalize_optional_string(payload.get("mssql_init_root")),
            playwright=bool(payload.get("playwright", False)),
            codex_skills=codex_skills,
            directory_path=normalize_optional_string(payload.get("directory_path"))
            or directory_path,
            metadata=metadata,
            allow_existing_session=existing_agent_behavior == "upsert",
        )
        output = {
            "success": bool(result.success),
            "message": normalize_optional_string(getattr(result, "message", None)) or "",
            "session_id": session_id or f"session-{vm_name}",
            "execution_backend": execution_backend,
            "vm_name": result.vm_name or vm_name,
            "skipped": False,
        }
        if vm_image_selection is not None:
            output["vm_image"] = vm_image_selection["image_ref"]
            output["vm_image_alias"] = vm_image_selection["template_alias"]
        return output

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
        vm_image = normalize_optional_string(payload.get("vm_image"))
        if vm_image:
            raise ValueError(
                "effect:agent:create vm_image is only supported with execution_backend='libvirt_vm'; "
                "use container_image for container_worker_vm"
            )
        session_repository = runtime_session_repository(context, config)
        existing_agent_behavior = normalize_existing_agent_behavior(
            payload.get("existing_agent_behavior")
        )
        existing_session = session_repository.get_session(session_id)
        resolved_contact_ids = None
        if bool(payload.get("_allowed_contact_ids_provided")):
            resolved_contact_ids = validate_allowed_contact_ids_for_directory(
                contacts_config_for_directory(context, directory_path),
                payload.get("allowed_contact_ids"),
                session_id=session_id,
                directory_path=directory_path,
            )
        else:
            existing_contact_ids = getattr(existing_session, "allowed_contact_ids", None)
            if existing_contact_ids is not None:
                resolved_contact_ids = list(existing_contact_ids)
        if existing_agent_behavior == "noop" and existing_session is not None:
            worker_id = normalize_optional_string(payload.get("worker_id"))
            if worker_id:
                from evander.admin.services.container_worker_assignment_service import (
                    ContainerWorkerAssignmentService,
                )

                ContainerWorkerAssignmentService(config).resolve(
                    session_id,
                    session=existing_session,
                    directory_path=directory_path,
                    worker_id=worker_id,
                )
            return _existing_agent_noop_result(
                session_id=session_id,
                execution_backend="container_worker_vm",
                vm_name=None,
            )
        from evander.admin.services.container_worker_assignment_service import (
            ContainerWorkerAssignmentService,
        )

        config = ContainerWorkerAssignmentService(config).resolve(
            session_id,
            session=existing_session,
            directory_path=directory_path,
            worker_id=normalize_optional_string(payload.get("worker_id")),
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
        existing_metadata = _existing_session_metadata(existing_session)
        metadata = dict(existing_metadata) if existing_session is not None else {}
        labels = (
            as_string_mapping(payload.get("labels"))
            if payload.get("labels") is not None
            else as_string_mapping(metadata.get("labels"))
        ) or {}
        requested_model = normalize_optional_string(payload.get("model"))
        model = requested_model or normalize_optional_string(
            getattr(existing_session, "model", None)
        )
        requested_agent_provider = normalize_optional_string(payload.get("agent_provider"))
        existing_agent_provider = normalize_optional_string(metadata.get("agent_provider"))
        agent_provider = (
            requested_agent_provider
            or existing_agent_provider
            or default_agent_provider_for_config(config)
        )
        codex_auth_mode = normalize_codex_auth_mode(
            payload.get("codex_auth_mode")
            if payload.get("_codex_auth_mode_provided")
            else getattr(existing_session, "codex_auth_mode", None)
        )
        existing_profile_id = normalize_subscription_profile_id(
            getattr(existing_session, "subscription_profile_id", None)
        )
        existing_auth_mode = normalize_codex_auth_mode(
            getattr(existing_session, "codex_auth_mode", None)
        )
        existing_binding = existing_profile_id
        if (
            existing_binding is None
            and existing_session is not None
            and existing_auth_mode is CodexAuthMode.CHATGPT_SUBSCRIPTION
        ):
            existing_binding = DEFAULT_SUBSCRIPTION_PROFILE_ID
        requested_profile_id = (
            normalize_subscription_profile_id(payload.get("subscription_profile_id"))
            if payload.get("_subscription_profile_id_provided")
            else None
        )
        if (
            existing_binding is not None
            and requested_profile_id is not None
            and requested_profile_id != existing_binding
        ):
            raise ValueError(
                "subscription_profile_id is write-once for an agent session; create a new "
                "agent to use a different profile"
            )
        subscription_profile_id = existing_profile_id or requested_profile_id
        if (
            existing_binding is not None
            and codex_auth_mode is not CodexAuthMode.CHATGPT_SUBSCRIPTION
        ):
            raise ValueError("A profile-bound agent cannot switch away from subscription mode")
        if codex_auth_mode is CodexAuthMode.CHATGPT_SUBSCRIPTION and agent_provider != "codex":
            raise ValueError("chatgpt_subscription is supported only for Codex agents")
        if (
            subscription_profile_id is not None
            and codex_auth_mode is not CodexAuthMode.CHATGPT_SUBSCRIPTION
        ):
            raise ValueError(
                "subscription_profile_id requires codex_auth_mode='chatgpt_subscription'"
            )
        container_image_selection = _container_image_selection_for_create(
            selector=normalize_optional_string(payload.get("container_image")),
            config=config,
            existing_session=existing_session,
        )
        npm_registry_url = normalize_optional_http_url(
            payload.get("npm_registry_url"), field_name="npm_registry_url"
        )
        metadata["execution_backend"] = "container_worker_vm"
        metadata["labels"] = labels
        capability_profile = normalize_optional_string(payload.get("capability_profile"))
        if capability_profile:
            metadata["capability_profile"] = capability_profile
        if requested_model:
            metadata["model"] = model
        if requested_agent_provider or (
            existing_agent_provider is None and agent_provider == "claude"
        ):
            metadata["agent_provider"] = agent_provider
        model_provider_mode = normalize_model_provider_mode_override(
            payload.get(MODEL_PROVIDER_MODE_METADATA_KEY)
        )
        if model_provider_mode:
            metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = model_provider_mode
        effective_model_provider_mode = (
            model_provider_mode
            or normalize_optional_string(metadata.get(MODEL_PROVIDER_MODE_METADATA_KEY))
            or normalize_optional_string(getattr(config, "model_provider_mode", None))
        )
        if (
            requested_profile_id is not None
            and existing_binding is None
            and codex_auth_mode is CodexAuthMode.CHATGPT_SUBSCRIPTION
            and effective_model_provider_mode != "openai_public"
        ):
            raise ValueError(
                "chatgpt_subscription is supported only with model_provider_mode='openai_public'"
            )
        requested_tools = as_string_list(payload.get("allowed_tools"))
        if requested_tools:
            resolved_allowed_tools = requested_tools
        else:
            resolved_allowed_tools = allowed_tools_for_profile(capability_profile)
        backend = container_backend(context, config)
        if requested_profile_id is not None and existing_binding is None:
            status_reader = getattr(backend.client, "codex_auth_profile_status", None)
            if not callable(status_reader):
                raise RuntimeError(
                    "container worker does not support named Codex subscription profiles"
                )
            profile_status = status_reader(requested_profile_id)
            if not bool(profile_status.get("enabled")):
                raise ValueError(f"Codex subscription profile {requested_profile_id!r} is disabled")
        worker_url = normalize_optional_string(
            getattr(getattr(backend, "client", None), "base_url", None)
        ) or normalize_optional_string(
            getattr(getattr(backend, "config", None), "container_worker_base_url", None)
        )
        if worker_url:
            metadata["worker_url"] = worker_url
        metadata = _deep_merge_metadata(metadata, _metadata_patch(payload.get("metadata")))
        if worker_url:
            metadata["worker_url"] = worker_url
        metadata["container_worker"] = container_image_selection
        metadata["playwright"] = bool(payload.get("playwright", False))
        requested_web_search_enabled = as_bool_or_none(payload.get("web_search_enabled"))
        effective_web_search_enabled = (
            requested_web_search_enabled
            if requested_web_search_enabled is not None
            else bool(getattr(existing_session, "web_search_enabled", False))
        )
        requested_image_generation_enabled = as_bool_or_none(
            payload.get("image_generation_enabled")
        )
        effective_image_generation_enabled = (
            requested_image_generation_enabled
            if requested_image_generation_enabled is not None
            else bool(getattr(existing_session, "image_generation_enabled", False))
        )
        communication_allowlists = _agent_communication_allowlists(
            payload,
            config=config,
            directory_path=directory_path,
        )
        email_mailbox_grants = _agent_email_mailbox_grants(
            payload,
            context=context,
            config=config,
            directory_path=directory_path,
        )
        connector_grants = _validated_connector_grants(
            context,
            directory_path=directory_path,
            session_id=session_id,
            value=payload.get("connector_grants"),
        )
        if (
            connector_grants is None
            and existing_session is not None
            and existing_session.directory_path == directory_path
        ):
            connector_grants = {
                connector_id: list(operation_names)
                for connector_id, operation_names in existing_session.connector_grants.items()
            }
        host_directory_grants = _validated_host_directory_grants(
            context,
            directory_path=directory_path,
            session_id=session_id,
            value=payload.get("host_directory_grants"),
        )
        if (
            host_directory_grants is None
            and existing_session is not None
            and existing_session.directory_path == directory_path
        ):
            host_directory_grants = {
                directory_id: list(capabilities)
                for directory_id, capabilities in existing_session.host_directory_grants.items()
            }
        manifest = build_container_session_manifest(
            config=effective_config_for_directory(context, directory_path),
            session_id=session_id,
            directory_path=directory_path,
            model=model,
            codex_auth_mode=codex_auth_mode,
            subscription_profile_id=subscription_profile_id,
            ttl_hours=as_int_or_none(payload.get("ttl_hours")),
            allowed_tools=resolved_allowed_tools,
            connector_grants=connector_grants,
            host_directory_grants=host_directory_grants,
            tool_argument_policies=payload.get("tool_argument_policies"),
            allowed_script_names=as_string_list(payload.get("allowed_script_names")),
            allowed_contact_ids=resolved_contact_ids,
            discord_channels=communication_allowlists["discord_channels"],
            teams_channel_allowlist=communication_allowlists["teams_channel_allowlist"],
            teams_chat_allowlist=communication_allowlists["teams_chat_allowlist"],
            teams_user_allowlist=communication_allowlists["teams_user_allowlist"],
            communication_target_ids=communication_allowlists["communication_target_ids"],
            email_mailbox_grants=email_mailbox_grants,
            email_mailboxes=communication_allowlists["email_mailboxes"],
            email_folders=communication_allowlists["email_folders"],
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
            web_search_enabled=effective_web_search_enabled,
            image_generation_enabled=effective_image_generation_enabled,
            metadata=metadata,
        )
        manifest, mcp_session_token = ensure_session_mcp_auth_token(manifest)
        session_record = backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": directory_path,
                "labels": labels,
                "container_worker": container_image_selection,
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
                    agent_provider=agent_provider,
                    web_search_enabled=effective_web_search_enabled,
                    dotnet=bool(payload.get("dotnet", False)),
                    nuget=bool(payload.get("nuget", False)),
                    playwright=bool(payload.get("playwright", False)),
                    npm_registry_url=npm_registry_url,
                    mcp_session_token=mcp_session_token,
                ),
                *codex_skill_files,
            ],
        )
        session_repository.upsert_session(manifest)
        _sync_added_discord_session_cursors_from_context(
            context,
            previous_session=existing_session,
            updated_session=manifest,
        )
        return {
            "success": True,
            "message": f"Prepared container worker session {session_id} with agent bootstrap files",
            "session_id": session_id,
            "execution_backend": "container_worker_vm",
            "vm_name": None,
            "agent_home": session_record.agent_home_container_path,
            "repo_root": session_record.repo_root_container_path,
            "container_image": container_image_selection["image"],
            "container_image_alias": container_image_selection["image_alias"],
            "subscription_profile_id": subscription_profile_id,
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


def _existing_session_metadata(existing_session: Any | None) -> dict[str, Any]:
    metadata = getattr(existing_session, "metadata", {}) if existing_session is not None else {}
    if not isinstance(metadata, Mapping):
        return {}
    return dict(metadata)


def _sync_added_discord_session_cursors_from_context(
    context: Any,
    *,
    previous_session: Any | None,
    updated_session: Any,
) -> None:
    admin_service = getattr(context, "admin_service", None)
    sync = getattr(admin_service, "sync_added_discord_session_cursors", None)
    if callable(sync):
        sync(previous_session=previous_session, updated_session=updated_session)


def _deep_merge_metadata(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in update.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge_metadata(existing, value)
            continue
        merged[key] = value
    return merged


def _append_unique(base: list[str], values: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*base, *values]:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def _agent_communication_allowlists(
    payload: Mapping[str, Any],
    *,
    config: Any,
    directory_path: str,
) -> dict[str, list[str] | None]:
    discord_channels = as_string_list(payload.get("discord_channels")) or []
    teams_channel_allowlist = as_string_list(payload.get("teams_channel_allowlist")) or []
    teams_chat_allowlist = as_string_list(payload.get("teams_chat_allowlist")) or []
    teams_user_allowlist = as_string_list(payload.get("teams_user_allowlist")) or []
    email_mailboxes = as_string_list(payload.get("email_mailboxes")) or []
    email_folders = as_string_list(payload.get("email_folders")) or []
    has_communication_target_ids = "communication_target_ids" in payload
    communication_target_ids_are_legacy = (
        has_communication_target_ids and payload.get("communication_target_ids") is None
    )
    communication_target_ids = [
        target_id.lower()
        for target_id in (as_string_list(payload.get("communication_target_ids")) or [])
    ]
    if not communication_target_ids:
        return {
            "communication_target_ids": (
                None
                if not has_communication_target_ids or communication_target_ids_are_legacy
                else []
            ),
            "discord_channels": discord_channels or None,
            "teams_channel_allowlist": teams_channel_allowlist or None,
            "teams_chat_allowlist": teams_chat_allowlist or None,
            "teams_user_allowlist": teams_user_allowlist or None,
            "email_mailboxes": email_mailboxes or None,
            "email_folders": email_folders or None,
        }

    targets_by_id = load_communication_targets_config(
        runtime_dir=config.runtime_dir,
        directory_store=DirectoryStore(config.directories_store_path),
        directory_path=directory_path,
    ).by_id()
    selected_discord_channels: list[str] = []
    selected_teams_channels: list[str] = []
    selected_teams_chats: list[str] = []
    selected_teams_users: list[str] = []
    for target_id in communication_target_ids:
        target = targets_by_id.get(target_id)
        if target is None:
            raise ValueError(
                "effect:agent:create communication_target_ids contains unknown target "
                f"{target_id!r} for directory {directory_path!r}"
            )
        if target.provider == "discord":
            selected_discord_channels.append(target.id)
            continue
        if target.provider == "teams":
            teams_kind = parse_teams_target(target.address).kind
            if teams_kind == "chat":
                selected_teams_chats.append(target.id)
            elif teams_kind == "user":
                selected_teams_users.append(target.id)
            else:
                selected_teams_channels.append(target.id)
            continue
        if target.provider == "email":
            if "write" not in set(target.policy.capabilities):
                raise ValueError(
                    "effect:agent:create email communication target "
                    f"{target.id!r} requires write capability"
                )
            _email_mailbox_id_for_target(target)
            continue
        raise ValueError(
            "effect:agent:create communication_target_ids only supports Teams, Discord, and email "
            f"targets for now; target {target.id!r} uses provider {target.provider!r}"
        )

    return {
        "communication_target_ids": communication_target_ids,
        "discord_channels": _append_unique(discord_channels, selected_discord_channels) or None,
        "teams_channel_allowlist": _append_unique(
            teams_channel_allowlist,
            selected_teams_channels,
        )
        or None,
        "teams_chat_allowlist": _append_unique(
            teams_chat_allowlist,
            selected_teams_chats,
        )
        or None,
        "teams_user_allowlist": _append_unique(
            teams_user_allowlist,
            selected_teams_users,
        )
        or None,
        "email_mailboxes": email_mailboxes or None,
        "email_folders": email_folders or None,
    }


def _agent_email_mailbox_grants(
    payload: Mapping[str, Any],
    *,
    context: Any,
    config: Any,
    directory_path: str,
) -> list[EmailMailboxGrant] | None:
    if "email_mailbox_grants" not in payload:
        return None
    grants = normalize_email_mailbox_grants(payload.get("email_mailbox_grants"))
    if grants is None:
        return None
    policy = context.provider_config_resolver.resolve_policy_for_directory(directory_path)
    return validate_email_mailbox_grants(
        grants,
        mailboxes=config.email_mailboxes,
        allowed_mailbox_ids=policy.email_mailbox_allowlist,
        folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
        email_send_enabled=policy.email_send_enabled,
        directory_path=directory_path,
        error_context="effect:agent:create ",
    )


def _email_mailbox_id_for_target(target: Any) -> str:
    auth_profile_ref = getattr(getattr(target, "policy", None), "auth_profile_ref", None)
    normalized = str(auth_profile_ref or "").strip()
    if normalized.startswith("email."):
        normalized = normalized.removeprefix("email.")
    if not normalized:
        raise ValueError(
            "effect:agent:create email communication target "
            f"{getattr(target, 'id', '')!r} requires policy.auth_profile_ref"
        )
    return normalized


def _container_image_selection_for_create(
    *,
    selector: str | None,
    config: Any,
    existing_session: Any | None,
) -> dict[str, str | None]:
    if selector is not None:
        return resolve_container_worker_image_selector(
            selector,
            default_image=config.container_worker_default_image,
            images=config.container_worker_images,
        )

    existing_metadata = getattr(existing_session, "metadata", {}) if existing_session else {}
    container_worker = (
        existing_metadata.get("container_worker")
        if isinstance(existing_metadata, Mapping)
        else None
    )
    if isinstance(container_worker, Mapping):
        existing_image = normalize_optional_string(container_worker.get("image"))
        if existing_image is not None:
            raw_alias = container_worker.get("image_alias")
            return {
                "image": existing_image,
                "image_alias": normalize_optional_string(raw_alias)
                if isinstance(raw_alias, str)
                else None,
            }

    return resolve_container_worker_image_selector(
        None,
        default_image=config.container_worker_default_image,
        images=config.container_worker_images,
    )


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
        list_sessions_for_vm = getattr(repository, "list_sessions_for_vm", None)
        if callable(list_sessions_for_vm):
            matches = list_sessions_for_vm(vm_name)
        else:
            list_sessions = getattr(repository, "list_sessions", None)
            matches = [
                session
                for session in (list_sessions() if callable(list_sessions) else [])
                if getattr(session, "vm_id", None) == vm_name
            ]
        if matches:
            return matches[-1]
    return None


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


def _libvirt_vm_template_selection_from_session(session: Any) -> dict[str, str | None] | None:
    metadata = getattr(session, "metadata", {})
    if not isinstance(metadata, Mapping):
        return None
    template = metadata.get("libvirt_vm_template")
    if not isinstance(template, Mapping):
        return None
    image_ref = normalize_optional_string(template.get("image_ref"))
    if image_ref is None:
        return None
    raw_alias = template.get("template_alias")
    return {
        "image_ref": image_ref,
        "template_alias": normalize_optional_string(raw_alias)
        if isinstance(raw_alias, str)
        else None,
    }


def _existing_agent_noop_result(
    *,
    session_id: str,
    execution_backend: str,
    vm_name: str | None,
    vm_image: str | None = None,
    vm_image_alias: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": True,
        "message": f"Agent session already exists: {session_id}; skipped create",
        "session_id": session_id,
        "execution_backend": execution_backend,
        "vm_name": vm_name,
        "skipped": True,
    }
    if vm_image is not None or vm_image_alias is not None:
        result["vm_image"] = vm_image
        result["vm_image_alias"] = vm_image_alias
    if execution_backend == "container_worker_vm":
        result["agent_home"] = "/home/agent"
        result["repo_root"] = "/home/agent/repos"
    return result


def _resolve_vm_defaults_for_create(context: Any, *, directory_path: str) -> Any:
    for owner in (getattr(context, "vm_creator", None), getattr(context, "admin_service", None)):
        resolve_vm_defaults = getattr(owner, "resolve_vm_defaults", None)
        if callable(resolve_vm_defaults):
            return resolve_vm_defaults(directory_path)
    raise RuntimeError(
        "effect:agent:create requires vm_creator.resolve_vm_defaults "
        "or admin_service.resolve_vm_defaults"
    )
