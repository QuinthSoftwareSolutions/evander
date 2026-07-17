from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    EffectiveVmDefaultsConfig,
    MODEL_PROVIDER_MODE_METADATA_KEY,
    Mapping,
    SessionManifest,
    VmActionResult,
    _admin_agent_communication_allowlists,
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
from evander.connectors.models import normalize_connector_grants


def _connector_grants_for_admin_agent(
    runtime: Any,
    *,
    session_id: str,
    directory_path: str,
    value: object,
) -> dict[str, list[str]]:
    grants = normalize_connector_grants(value)
    config = runtime.provider_config.resolve_connectors_for_directory(directory_path)
    for connector_id, operation_names in grants.items():
        connector = config.connectors.get(connector_id)
        if connector is None or connector.disabled:
            raise ValueError(f"unknown or disabled connector: {connector_id}")
        for operation_name in operation_names:
            operation = connector.operations.get(operation_name)
            if operation is None or operation.disabled:
                raise ValueError(
                    f"unknown or disabled connector operation: {connector_id}.{operation_name}"
                )
    return grants


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
        selected_backend = self._normalize_agent_backend(
            self._agent_optional_string(payload, "backend")
            or self._agent_optional_string(payload, "execution_backend")
        )
        if selected_backend == "libvirt_vm":
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
            connector_grants = _connector_grants_for_admin_agent(
                self.runtime,
                session_id=self._agent_optional_string(payload, "session_id") or vm_name,
                directory_path=directory_path,
                value=payload.get("connector_grants"),
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
        backend = self.container_worker_backend(directory_path=directory_path)
        from evander.effects.providers.agent_session import default_agent_provider_for_config

        requested_agent_provider = self._agent_optional_string(payload, "agent_provider")
        agent_provider = requested_agent_provider or default_agent_provider_for_config(
            backend.config
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
        model_provider_mode = normalize_model_provider_mode_override(
            payload.get(MODEL_PROVIDER_MODE_METADATA_KEY)
        )
        if model_provider_mode:
            metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = model_provider_mode
        if labels:
            metadata["labels"] = labels
        worker_url = self._container_worker_backend_url(backend)
        if worker_url:
            metadata["worker_url"] = worker_url
        metadata["container_worker"] = container_image_selection
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
        ).model_copy(
            update={
                "vm_id": None,
                "vm_provider": "container_worker_vm",
                "vm_provider_id": target_name,
                "model": model,
                "metadata": metadata,
            }
        )
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
            "effective_tool_policy": self.effective_tool_policy_payload(session),
        }

    def resolve_vm_defaults(self, directory_path: str) -> EffectiveVmDefaultsConfig:
        return self._resolve_vm_defaults(directory_path)

    def agent_create_options(self, directory_path: str | None = None) -> dict[str, Any]:
        normalized_directory = self._normalize_directory_path(directory_path)
        vm_defaults = self.resolve_vm_defaults(normalized_directory)
        provider_config = self.runtime.provider_config.resolve_for_directory(normalized_directory)
        libvirt_templates = [
            {"alias": alias, "image_ref": image_ref}
            for alias, image_ref in sorted(vm_defaults.libvirt_vm_templates.items())
        ]
        container_images = [
            {"alias": alias, "image_ref": image_ref}
            for alias, image_ref in sorted(provider_config.container_worker_images.items())
        ]
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
            ],
            "communication_targets": [
                option.model_dump(mode="json")
                for option in self._agent_communication_target_options(normalized_directory)
            ],
            "connectors": connectors,
            "libvirt": {
                "default_template": vm_defaults.libvirt_vm_default_template,
                "templates": libvirt_templates,
            },
            "container_worker": {
                "default_image": provider_config.container_worker_default_image,
                "images": container_images,
            },
        }

    create_vm_for_admin_agent = _create_vm_for_admin_agent
