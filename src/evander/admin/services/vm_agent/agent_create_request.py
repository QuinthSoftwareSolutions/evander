from __future__ import annotations

from evander.provider_config import validate_openrouter_session
from evander.admin.services.vm_agent.common import (
    Any,
    EffectiveVmDefaultsConfig,
    MODEL_PROVIDER_MODE_METADATA_KEY,
    Mapping,
    SessionManifest,
    VmActionResult,
    _admin_agent_communication_allowlists,
    _admin_agent_email_mailbox_grants,
    _build_issue_session_manifest,
    _normalize_session_labels,
    _normalize_session_policy_bool,
    _normalize_session_policy_non_negative_int_or_none,
    _sync_added_discord_session_cursors,
    _validate_allowed_contact_ids_for_directory,
    normalize_model_provider_mode_override,
    resolve_container_worker_image_selector,
    resolve_libvirt_vm_template_selector,
)
from evander.session_auth import ensure_session_mcp_auth_token
from evander.codex_auth import (
    CodexAuthMode,
    normalize_codex_auth_mode,
    normalize_subscription_profile_id,
)
from evander.connectors.grants import resolve_and_validate_connector_grants
from evander.host_directories.grants import (
    resolve_and_validate_host_directory_grants,
)
from evander.admin.services.session_helpers import normalize_http_targets_metadata


def _connector_grants_for_admin_agent(
    runtime: Any,
    *,
    session_id: str,
    directory_path: str,
    value: object,
) -> dict[str, list[str]]:
    return resolve_and_validate_connector_grants(
        runtime.provider_config,
        directory_path=directory_path,
        session_id=session_id,
        value=value,
    )


def _host_directory_grants_for_admin_agent(
    runtime: Any,
    *,
    session_id: str,
    directory_path: str,
    value: object,
) -> dict[str, list[str]]:
    return resolve_and_validate_host_directory_grants(
        provider_config=runtime.provider_config,
        directory_path=directory_path,
        session_id=session_id,
        value=value,
    )


class VmAgentCreateRequestMixin:
    def sync_added_discord_session_cursors(
        self,
        *,
        previous_session: SessionManifest | None,
        updated_session: SessionManifest,
    ) -> dict[str, str]:
        return _sync_added_discord_session_cursors(
            self.runtime,
            previous_session=previous_session,
            updated_session=updated_session,
        )

    def _create_vm_for_admin_agent(self, **kwargs: Any) -> VmActionResult:
        return self._vm_lifecycle_port().create_vm(**kwargs)

    def create_admin_agent(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        worker_id = self._agent_optional_string(payload, "worker_id")
        selected_backend = self._normalize_agent_backend(
            self._agent_optional_string(payload, "backend")
            or self._agent_optional_string(payload, "execution_backend")
        )
        if selected_backend == "libvirt_vm":
            if worker_id:
                raise ValueError("worker_id is supported only for container_worker_vm agents")
            vm_name = self._agent_optional_string(payload, "vm_name")
            if not vm_name:
                raise ValueError("vm_name is required for libvirt_vm agents")
            codex_skills = self.validate_managed_codex_skills(
                self._agent_string_list(payload, "codex_skills")
            )
            agent_provider = self._agent_optional_string(payload, "agent_provider")
            directory_path = self._agent_optional_string(payload, "directory_path") or "root"
            vm_image_selector = self._agent_optional_string(payload, "vm_image")
            vm_defaults = self.resolve_vm_defaults(directory_path)
            vm_image_selection = resolve_libvirt_vm_template_selector(
                vm_image_selector,
                default_template=vm_defaults.libvirt_vm_default_template,
                templates=vm_defaults.libvirt_vm_templates,
            )
            create_metadata: dict[str, Any] = {
                "libvirt_vm_template": {
                    "image_ref": vm_image_selection["image_ref"],
                    "template_alias": vm_image_selection["template_alias"],
                }
            }
            if agent_provider:
                create_metadata["agent_provider"] = agent_provider
            model_provider_mode = normalize_model_provider_mode_override(
                payload.get(MODEL_PROVIDER_MODE_METADATA_KEY)
            )
            if model_provider_mode:
                create_metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = model_provider_mode
            if "http_targets" in payload:
                create_metadata["http_targets"] = normalize_http_targets_metadata(
                    payload.get("http_targets")
                )
            resolved_allowed_contact_ids = _validate_allowed_contact_ids_for_directory(
                self.runtime,
                session_id=self._agent_optional_string(payload, "session_id") or vm_name,
                directory_path=directory_path,
                allowed_contact_ids=payload.get("allowed_contact_ids"),
            )
            communication_allowlists = _admin_agent_communication_allowlists(
                payload,
                config=self.config,
                directory_path=directory_path,
            )
            email_mailbox_grants = _admin_agent_email_mailbox_grants(
                payload,
                runtime=self.runtime,
                directory_path=directory_path,
            )
            connector_grants = _connector_grants_for_admin_agent(
                self.runtime,
                session_id=self._agent_optional_string(payload, "session_id") or vm_name,
                directory_path=directory_path,
                value=payload.get("connector_grants"),
            )
            host_directory_grants = _host_directory_grants_for_admin_agent(
                self.runtime,
                session_id=self._agent_optional_string(payload, "session_id") or vm_name,
                directory_path=directory_path,
                value=payload.get("host_directory_grants"),
            )
            result = self._create_vm_for_admin_agent(
                provider=self._agent_optional_string(payload, "provider"),
                vm_name=vm_name,
                session_id=self._agent_optional_string(payload, "session_id"),
                labels=_normalize_session_labels(payload.get("labels")),
                model=self._agent_optional_string(payload, "model"),
                vm_user=self._agent_optional_string(payload, "vm_user") or "agent",
                ttl_seconds=self._agent_int_or_none(payload, "ttl_seconds"),
                ttl_hours=self._agent_int_or_none(payload, "ttl_hours"),
                allowed_tools=self._agent_string_list(payload, "allowed_tools"),
                **({"connector_grants": connector_grants} if "connector_grants" in payload else {}),
                **(
                    {"host_directory_grants": host_directory_grants}
                    if "host_directory_grants" in payload
                    else {}
                ),
                **(
                    {"tool_argument_policies": payload["tool_argument_policies"]}
                    if "tool_argument_policies" in payload
                    else {}
                ),
                allowed_script_names=self._agent_string_list(payload, "allowed_script_names"),
                allowed_contact_ids=resolved_allowed_contact_ids,
                discord_channels=communication_allowlists["discord_channels"],
                teams_channel_allowlist=communication_allowlists["teams_channel_allowlist"],
                teams_chat_allowlist=communication_allowlists["teams_chat_allowlist"],
                teams_user_allowlist=communication_allowlists["teams_user_allowlist"],
                communication_target_ids=communication_allowlists["communication_target_ids"],
                email_mailbox_grants=email_mailbox_grants,
                email_mailboxes=communication_allowlists["email_mailboxes"],
                email_folders=communication_allowlists["email_folders"],
                communication_file_receive_enabled=(
                    _normalize_session_policy_bool(
                        payload.get("communication_file_receive_enabled"),
                        field_name="communication_file_receive_enabled",
                    )
                    if "communication_file_receive_enabled" in payload
                    else None
                ),
                communication_file_send_enabled=(
                    _normalize_session_policy_bool(
                        payload.get("communication_file_send_enabled"),
                        field_name="communication_file_send_enabled",
                    )
                    if "communication_file_send_enabled" in payload
                    else None
                ),
                communication_file_max_bytes=(
                    _normalize_session_policy_non_negative_int_or_none(
                        payload.get("communication_file_max_bytes"),
                        field_name="communication_file_max_bytes",
                    )
                    if "communication_file_max_bytes" in payload
                    else None
                ),
                communication_file_require_approval_for_send=(
                    _normalize_session_policy_bool(
                        payload.get("communication_file_require_approval_for_send"),
                        field_name="communication_file_require_approval_for_send",
                    )
                    if "communication_file_require_approval_for_send" in payload
                    else None
                ),
                web_search_enabled=(
                    _normalize_session_policy_bool(
                        payload.get("web_search_enabled"),
                        field_name="web_search_enabled",
                    )
                    if "web_search_enabled" in payload
                    else False
                ),
                image_generation_enabled=(
                    _normalize_session_policy_bool(
                        payload.get("image_generation_enabled"),
                        field_name="image_generation_enabled",
                    )
                    if "image_generation_enabled" in payload
                    else False
                ),
                dotnet=bool(payload.get("dotnet", False)),
                nuget=bool(payload.get("nuget", False)),
                mssql=bool(payload.get("mssql", False)),
                mssql_init_root=self._agent_optional_string(payload, "mssql_init_root"),
                playwright=bool(payload.get("playwright", False)),
                codex_skills=codex_skills,
                directory_path=directory_path,
                metadata=create_metadata,
            )
            session_id = self._agent_optional_string(payload, "session_id")
            session = (
                self.runtime.session_repository.get_session(session_id) if session_id else None
            )
            return {
                "success": result.success,
                "agent_id": result.vm_name or vm_name,
                "backend": "libvirt_vm",
                "target_name": result.vm_name or vm_name,
                "action": result.action,
                "message": result.message,
                "status": "created" if result.success else "failed",
                "session_id": session_id,
                "provider": result.provider,
                "provider_operation_id": result.provider_operation_id,
                "effective_tool_policy": self.effective_tool_policy_payload(session),
                "vm_image": vm_image_selection["image_ref"],
                "vm_image_alias": vm_image_selection["template_alias"],
            }

        session_id = self._agent_optional_string(payload, "session_id")
        if not session_id:
            raise ValueError("session_id is required for container_worker_vm agents")
        if bool(payload.get("mssql", False)) or self._agent_optional_string(
            payload, "mssql_init_root"
        ):
            raise ValueError(
                "create_admin_agent with container_worker_vm does not support MSSQL yet"
            )
        codex_skill_files = self.resolve_container_codex_skill_files(
            self._agent_string_list(payload, "codex_skills")
        )
        target_name = self._agent_optional_string(payload, "target_name") or session_id
        directory_path = self._normalize_directory_path(
            self._agent_optional_string(payload, "directory_path")
        )
        session_repository = self.runtime.session_repository
        if session_repository.get_session(session_id) is not None:
            raise ValueError(f"Session already exists: {session_id}")

        policy = self.runtime.policy_config.resolve_for_directory(directory_path)
        labels = _normalize_session_labels(payload.get("labels"))
        model = self._agent_optional_string(payload, "model")
        backend = self.container_worker_backend(
            directory_path=directory_path,
            session_id=session_id,
            **({"worker_id": worker_id} if worker_id else {}),
        )
        from evander.effects.providers.agent_session import default_agent_provider_for_config

        requested_agent_provider = self._agent_optional_string(payload, "agent_provider")
        agent_provider = requested_agent_provider or default_agent_provider_for_config(
            backend.config
        )
        codex_auth_mode = normalize_codex_auth_mode(payload.get("codex_auth_mode"))
        subscription_profile_id = normalize_subscription_profile_id(
            payload.get("subscription_profile_id")
        )
        if (
            codex_auth_mode is CodexAuthMode.CHATGPT_SUBSCRIPTION
            and str(agent_provider).strip().casefold() != "codex"
        ):
            raise ValueError("chatgpt_subscription is supported only for Codex agents")
        if (
            subscription_profile_id is not None
            and codex_auth_mode is not CodexAuthMode.CHATGPT_SUBSCRIPTION
        ):
            raise ValueError(
                "subscription_profile_id requires codex_auth_mode='chatgpt_subscription'"
            )
        requested_model_provider_mode = normalize_model_provider_mode_override(
            payload.get(MODEL_PROVIDER_MODE_METADATA_KEY)
        )
        effective_model_provider_mode = (
            requested_model_provider_mode
            or str(getattr(backend.config, "model_provider_mode", "") or "").strip()
        )
        if (
            subscription_profile_id is not None
            and codex_auth_mode is CodexAuthMode.CHATGPT_SUBSCRIPTION
            and effective_model_provider_mode != "openai_public"
        ):
            raise ValueError(
                "chatgpt_subscription is supported only with model_provider_mode='openai_public'"
            )
        if subscription_profile_id is not None:
            profile_status = backend.client.codex_auth_profile_status(subscription_profile_id)
            if not bool(profile_status.get("enabled")):
                raise ValueError(
                    f"Codex subscription profile {subscription_profile_id!r} is disabled"
                )
        container_image_selection = resolve_container_worker_image_selector(
            self._agent_optional_string(payload, "container_image"),
            default_image=backend.config.container_worker_default_image,
            images=backend.config.container_worker_images,
        )
        metadata: dict[str, Any] = {
            "execution_backend": "container_worker_vm",
            "target_name": target_name,
        }
        if requested_agent_provider or agent_provider == "claude":
            metadata["agent_provider"] = agent_provider
        model_provider_mode = requested_model_provider_mode
        if model_provider_mode:
            metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = model_provider_mode
        if labels:
            metadata["labels"] = labels
        worker_url = self._container_worker_backend_url(backend)
        if worker_url:
            metadata["worker_url"] = worker_url
        metadata["container_worker"] = container_image_selection
        metadata["playwright"] = bool(payload.get("playwright", False))
        if "http_targets" in payload:
            metadata["http_targets"] = normalize_http_targets_metadata(payload.get("http_targets"))
        resolved_allowed_contact_ids = _validate_allowed_contact_ids_for_directory(
            self.runtime,
            session_id=session_id,
            directory_path=directory_path,
            allowed_contact_ids=payload.get("allowed_contact_ids"),
        )
        communication_allowlists = _admin_agent_communication_allowlists(
            payload,
            config=self.config,
            directory_path=directory_path,
        )
        email_mailbox_grants = _admin_agent_email_mailbox_grants(
            payload,
            runtime=self.runtime,
            directory_path=directory_path,
        )
        manifest = _build_issue_session_manifest(
            policy=policy,
            session_id=session_id,
            vm_id=target_name,
            issue_number=None,
            directory_path=directory_path,
            ttl_hours=self._agent_int_or_none(payload, "ttl_hours"),
            ttl_seconds=self._agent_int_or_none(payload, "ttl_seconds"),
            extra_allowed_tools=self._agent_string_list(payload, "allowed_tools"),
            connector_grants=_connector_grants_for_admin_agent(
                self.runtime,
                session_id=session_id,
                directory_path=directory_path,
                value=payload.get("connector_grants"),
            ),
            host_directory_grants=_host_directory_grants_for_admin_agent(
                self.runtime,
                session_id=session_id,
                directory_path=directory_path,
                value=payload.get("host_directory_grants"),
            ),
            **(
                {"tool_argument_policies": payload["tool_argument_policies"]}
                if "tool_argument_policies" in payload
                else {}
            ),
            allowed_script_names=self._agent_string_list(payload, "allowed_script_names"),
            allowed_contact_ids=resolved_allowed_contact_ids,
            discord_channels=communication_allowlists["discord_channels"] or [],
            teams_channel_allowlist=communication_allowlists["teams_channel_allowlist"] or [],
            teams_chat_allowlist=communication_allowlists["teams_chat_allowlist"] or [],
            teams_user_allowlist=communication_allowlists["teams_user_allowlist"] or [],
            communication_target_ids=communication_allowlists["communication_target_ids"],
            email_mailbox_grants=email_mailbox_grants,
            email_mailboxes=communication_allowlists["email_mailboxes"] or [],
            email_folders=communication_allowlists["email_folders"] or [],
            communication_file_receive_enabled=(
                _normalize_session_policy_bool(
                    payload.get("communication_file_receive_enabled"),
                    field_name="communication_file_receive_enabled",
                )
                if "communication_file_receive_enabled" in payload
                else None
            ),
            communication_file_send_enabled=(
                _normalize_session_policy_bool(
                    payload.get("communication_file_send_enabled"),
                    field_name="communication_file_send_enabled",
                )
                if "communication_file_send_enabled" in payload
                else None
            ),
            communication_file_max_bytes=(
                _normalize_session_policy_non_negative_int_or_none(
                    payload.get("communication_file_max_bytes"),
                    field_name="communication_file_max_bytes",
                )
                if "communication_file_max_bytes" in payload
                else None
            ),
            communication_file_require_approval_for_send=(
                _normalize_session_policy_bool(
                    payload.get("communication_file_require_approval_for_send"),
                    field_name="communication_file_require_approval_for_send",
                )
                if "communication_file_require_approval_for_send" in payload
                else None
            ),
            web_search_enabled=(
                _normalize_session_policy_bool(
                    payload.get("web_search_enabled"),
                    field_name="web_search_enabled",
                )
                if "web_search_enabled" in payload
                else False
            ),
            image_generation_enabled=(
                _normalize_session_policy_bool(
                    payload.get("image_generation_enabled"),
                    field_name="image_generation_enabled",
                )
                if "image_generation_enabled" in payload
                else False
            ),
        ).model_copy(
            update={
                "vm_id": None,
                "vm_provider": "container_worker_vm",
                "vm_provider_id": target_name,
                "model": model,
                "codex_auth_mode": codex_auth_mode,
                "subscription_profile_id": subscription_profile_id,
                "metadata": metadata,
            }
        )
        validate_openrouter_session(backend.config, manifest)
        manifest, mcp_session_token = ensure_session_mcp_auth_token(manifest)
        session_record = backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": directory_path,
                "target_name": target_name,
                "labels": labels,
                "container_worker": container_image_selection,
                **({"worker_url": worker_url} if worker_url else {}),
            },
        )
        from evander.effects.providers.agent_session import (
            build_container_session_files,
        )

        backend.write_files(
            session_id=session_id,
            files=[
                *build_container_session_files(
                    config=backend.config,
                    session_id=session_id,
                    model=model,
                    agent_provider=agent_provider,
                    web_search_enabled=(
                        _normalize_session_policy_bool(
                            payload.get("web_search_enabled"),
                            field_name="web_search_enabled",
                        )
                        if "web_search_enabled" in payload
                        else False
                    ),
                    dotnet=bool(payload.get("dotnet", False)),
                    nuget=bool(payload.get("nuget", False)),
                    playwright=bool(payload.get("playwright", False)),
                    mcp_session_token=mcp_session_token,
                ),
                *codex_skill_files,
            ],
        )
        session_repository.upsert_session(manifest)
        _sync_added_discord_session_cursors(
            self.runtime,
            previous_session=None,
            updated_session=manifest,
        )
        session = session_repository.get_session(session_id)
        return {
            "success": True,
            "agent_id": session_id,
            "backend": "container_worker_vm",
            "target_name": target_name,
            "action": "create",
            "message": f"Prepared container worker session {session_id}",
            "status": "prepared",
            "session_id": session_id,
            "agent_home": session_record.agent_home_container_path,
            "repo_root": session_record.repo_root_container_path,
            "container_image": container_image_selection["image"],
            "container_image_alias": container_image_selection["image_alias"],
            "subscription_profile_id": subscription_profile_id,
            "effective_tool_policy": self.effective_tool_policy_payload(session),
        }

    def resolve_vm_defaults(self, directory_path: str) -> EffectiveVmDefaultsConfig:
        return self._resolve_vm_defaults(directory_path)

    def agent_create_options(
        self, directory_path: str | None = None, *, worker_id: str | None = None
    ) -> dict[str, Any]:
        normalized_directory = self._normalize_directory_path(directory_path)
        vm_defaults = self.resolve_vm_defaults(normalized_directory)
        provider_config = self.runtime.provider_config.resolve_for_directory(normalized_directory)
        workers = [
            {"worker_id": key, "display_name": profile.display_name or key}
            for key, profile in sorted(provider_config.container_workers.items())
        ]
        if worker_id:
            from evander.admin.services.container_worker_assignment_service import (
                ContainerWorkerAssignmentService,
            )

            provider_config = ContainerWorkerAssignmentService(
                provider_config
            ).config_for_worker_id(worker_id)
        policy_config = self.runtime.policy_config.resolve_for_directory(normalized_directory)
        libvirt_templates = [
            {"alias": alias, "image_ref": image_ref}
            for alias, image_ref in sorted(vm_defaults.libvirt_vm_templates.items())
        ]
        container_images = [
            {"alias": alias, "image_ref": image_ref}
            for alias, image_ref in sorted(provider_config.container_worker_images.items())
        ]
        subscription_profiles: list[dict[str, object]] = []
        try:
            profile_payload = self.container_worker_backend(
                directory_path=normalized_directory,
                **({"worker_id": worker_id} if worker_id else {}),
            ).client.list_codex_auth_profiles()
            subscription_profiles = [
                {
                    "profile_id": str(profile.get("profile_id") or "").strip(),
                    "display_name": (
                        str(profile.get("display_name") or profile.get("profile_id") or "").strip()
                    ),
                    "enabled": True,
                }
                for profile in profile_payload.get("profiles", [])
                if isinstance(profile, Mapping)
                and bool(profile.get("enabled"))
                and str(profile.get("profile_id") or "").strip()
            ]
        except Exception:
            subscription_profiles = []
        connector_config = self.runtime.provider_config.resolve_connectors_for_directory(
            normalized_directory
        )
        connectors = [
            {
                "connector_id": connector_id,
                "display_name": connector.display_name,
                "description": connector.description,
                "operations": [
                    {
                        "name": operation_name,
                        "description": operation.description,
                        "access": operation.access,
                        "requires_approval": operation.requires_approval,
                        "result_mode": operation.result_mode,
                    }
                    for operation_name, operation in sorted(connector.operations.items())
                    if not operation.disabled
                ],
            }
            for connector_id, connector in sorted(connector_config.connectors.items())
            if not connector.disabled
        ]
        host_directory_config = self.runtime.provider_config.resolve_host_directories_for_directory(
            normalized_directory
        )
        host_directories = [
            {
                "directory_id": directory_id,
                "display_name": definition.display_name,
                "description": definition.description,
                "capabilities": list(definition.capabilities),
                "delete_requires_approval": definition.delete_requires_approval,
            }
            for directory_id, definition in sorted(host_directory_config.host_directories.items())
            if not definition.disabled
        ]
        allowed_mailbox_ids = set(policy_config.email_mailbox_allowlist)
        email_mailboxes: list[dict[str, object]] = []
        for mailbox in provider_config.email_mailboxes:
            if mailbox.mailbox_id not in allowed_mailbox_ids:
                continue
            allowed_folders = list(
                policy_config.email_folder_allowlist_by_mailbox.get(
                    mailbox.mailbox_id, mailbox.folder_allowlist_defaults
                )
            )
            allowed_folder_set = set(allowed_folders)
            email_mailboxes.append(
                {
                    "mailbox_id": mailbox.mailbox_id,
                    "provider": mailbox.provider,
                    "account_identifier": mailbox.account_identifier,
                    "folders": allowed_folders,
                    "default_folders": [
                        folder
                        for folder in mailbox.folder_allowlist_defaults
                        if folder in allowed_folder_set
                    ],
                    "send_enabled": bool(policy_config.email_send_enabled),
                }
            )
        return {
            "directory_path": normalized_directory,
            "backends": ["libvirt_vm", "container_worker_vm"],
            "agent_providers": [
                {
                    "value": "codex",
                    "label": "Codex",
                    "execution_backends": ["libvirt_vm", "container_worker_vm"],
                },
                {
                    "value": "claude",
                    "label": "Claude",
                    "execution_backends": ["libvirt_vm", "container_worker_vm"],
                },
                {
                    "value": "copilot",
                    "label": "Copilot",
                    "execution_backends": ["libvirt_vm"],
                },
            ],
            "model_provider_modes": [
                {"value": "openai_public", "label": "Public OpenAI"},
                {"value": "local_openai", "label": "Local OpenAI"},
                {"value": "azure_openai", "label": "Azure OpenAI"},
                {"value": "openrouter", "label": "OpenRouter"},
            ],
            "communication_targets": [
                option.model_dump(mode="json")
                for option in self._agent_communication_target_options(normalized_directory)
            ],
            "email_mailboxes": email_mailboxes,
            "connectors": connectors,
            "host_directories": host_directories,
            "libvirt": {
                "default_template": vm_defaults.libvirt_vm_default_template,
                "templates": libvirt_templates,
            },
            "container_worker": {
                "workers": workers,
                "selected_worker_id": worker_id,
                "default_image": provider_config.container_worker_default_image,
                "images": container_images,
                "codex_auth_mode": provider_config.container_worker_codex_auth_mode,
                "model_provider_mode": provider_config.model_provider_mode,
                "subscription_profiles": subscription_profiles,
            },
        }

    create_vm_for_admin_agent = _create_vm_for_admin_agent
