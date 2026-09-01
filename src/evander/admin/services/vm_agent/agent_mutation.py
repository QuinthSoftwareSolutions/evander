from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
    Mapping,
    SessionManifest,
    _normalize_allowed_tools,
    _admin_agent_communication_allowlists,
    _admin_agent_email_mailbox_grants,
    _normalize_session_labels,
    _prune_approvals_for_sessions,
    _prune_pr_feedback_store,
    _prune_pr_map,
    _revoke_container_git_write_access_for_session,
    _resolve_session_approval_notification_target_ids,
    _session_policy_update_from_payload,
    _sync_added_discord_session_cursors,
    timedelta,
)
from evander.codex_auth import CodexAuthMode, normalize_codex_auth_mode
from evander.connectors.grants import resolve_and_validate_connector_grants
from evander.host_directories.grants import (
    host_directory_tool_names_for_grants,
    resolve_and_validate_host_directory_grants,
)


class VmAgentMutationMixin:
    def update_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(backend)
        body = dict(payload or {})
        allowed_fields = {
            "model",
            "ttl_hours",
            "allowed_tools",
            "connector_grants",
            "host_directory_grants",
            "approval_required_tools",
            "approval_tool_settings",
            "approval_notification_target_ids",
            "tool_argument_policies",
            "allowed_script_names",
            "allowed_contact_ids",
            "discord_channels",
            "discord_channel_allowlist",
            "teams_channels",
            "teams_channel_allowlist",
            "teams_chat_allowlist",
            "teams_user_allowlist",
            "http_targets",
            "metadata",
            "web_search_enabled",
            "image_generation_enabled",
            "email_mailboxes",
            "email_folders",
            "email_folder_allowlist_by_mailbox",
            "communication_target_ids",
            "email_mailbox_grants",
            "labels",
            "codex_auth_mode",
            *COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
        }
        unsupported = sorted(set(body) - allowed_fields)
        if unsupported:
            raise ValueError("Unsupported agent policy fields: " + ", ".join(unsupported))
        session: SessionManifest | None
        if selected_backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=True)
            del record
            target_name = self._container_session_target_name(session)
        else:
            session = self._resolve_vm_agent_session(agent_id)
            target_name = str(session.vm_id or agent_id)

        update: dict[str, Any] = {}
        if "model" in body:
            update["model"] = self._agent_optional_string(body, "model")
        if "ttl_hours" in body:
            ttl_hours = self._agent_int_or_none(body, "ttl_hours")
            if ttl_hours is None:
                raise ValueError("ttl_hours must be an integer when supplied")
            update["expires_at"] = self._current_utc_now() + timedelta(hours=ttl_hours)
        if "allowed_tools" in body:
            update["allowed_tools"] = _normalize_allowed_tools(
                self._agent_string_list(body, "allowed_tools") or []
            )
        if "connector_grants" in body:
            connector_grants = resolve_and_validate_connector_grants(
                self.runtime.provider_config,
                directory_path=session.directory_path,
                session_id=session.session_id,
                value=body.get("connector_grants"),
            )
            update["connector_grants"] = connector_grants
        if "host_directory_grants" in body:
            update["host_directory_grants"] = resolve_and_validate_host_directory_grants(
                provider_config=self.runtime.provider_config,
                directory_path=session.directory_path,
                session_id=session.session_id,
                value=body.get("host_directory_grants"),
            )
            update["allowed_tools"] = _normalize_allowed_tools(
                [
                    *update.get("allowed_tools", session.allowed_tools),
                    *host_directory_tool_names_for_grants(update["host_directory_grants"]),
                ]
            )
        session_policy_fields = {
            key: body[key]
            for key in (
                "approval_required_tools",
                "approval_tool_settings",
                "approval_notification_target_ids",
                "tool_argument_policies",
                "allowed_script_names",
                "allowed_contact_ids",
                "discord_channels",
                "discord_channel_allowlist",
                "teams_channels",
                "teams_channel_allowlist",
                "teams_chat_allowlist",
                "teams_user_allowlist",
                "http_targets",
                "metadata",
                "web_search_enabled",
                "image_generation_enabled",
                *COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
            )
            if key in body
        }
        if session_policy_fields:
            contacts_config = None
            if "allowed_contact_ids" in session_policy_fields:
                contacts_config = self.runtime.provider_config.resolve_contacts_for_session_id(
                    session.session_id
                )
            update.update(
                _session_policy_update_from_payload(
                    session,
                    session_policy_fields,
                    contacts_config=contacts_config,
                )
            )
        if "approval_notification_target_ids" in update:
            update["approval_notification_target_ids"] = (
                _resolve_session_approval_notification_target_ids(
                    self.runtime,
                    self.config,
                    session,
                    update["approval_notification_target_ids"],
                )
            )
        if "email_mailboxes" in body:
            update["email_mailbox_allowlist"] = (
                self._agent_string_list(body, "email_mailboxes") or []
            )
        if "email_folders" in body:
            update["email_folder_allowlist"] = self._agent_string_list(body, "email_folders") or []
        if "email_folder_allowlist_by_mailbox" in body:
            raw_folder_map = body.get("email_folder_allowlist_by_mailbox")
            if not isinstance(raw_folder_map, Mapping):
                raise ValueError("email_folder_allowlist_by_mailbox must be an object")
            folder_map: dict[str, list[str]] = {}
            for raw_mailbox_id, raw_folders in raw_folder_map.items():
                mailbox_id = str(raw_mailbox_id or "").strip()
                if not mailbox_id:
                    raise ValueError(
                        "email_folder_allowlist_by_mailbox keys must be non-empty strings"
                    )
                if mailbox_id in folder_map:
                    raise ValueError(
                        "email_folder_allowlist_by_mailbox contains duplicate mailbox "
                        f"{mailbox_id!r}"
                    )
                folder_map[mailbox_id] = (
                    self._agent_string_list({"folders": raw_folders}, "folders") or []
                )
            update["email_folder_allowlist_by_mailbox"] = folder_map
        if "communication_target_ids" in body:
            communication_allowlists = _admin_agent_communication_allowlists(
                body,
                config=self.config,
                directory_path=session.directory_path,
            )
            update["communication_target_ids"] = communication_allowlists[
                "communication_target_ids"
            ]
            if communication_allowlists["communication_target_ids"] is not None:
                update.update(
                    {
                        "discord_channel_allowlist": communication_allowlists["discord_channels"]
                        or [],
                        "teams_channel_allowlist": communication_allowlists[
                            "teams_channel_allowlist"
                        ]
                        or [],
                        "teams_chat_allowlist": communication_allowlists["teams_chat_allowlist"]
                        or [],
                        "teams_user_allowlist": communication_allowlists["teams_user_allowlist"]
                        or [],
                    }
                )
        if "email_mailbox_grants" in body:
            grants = _admin_agent_email_mailbox_grants(
                body,
                runtime=self.runtime,
                directory_path=session.directory_path,
            )
            update["email_mailbox_grants"] = grants
            if grants is not None:
                update["email_mailbox_allowlist"] = [grant.mailbox_id for grant in grants]
                update["email_folder_allowlist_by_mailbox"] = {
                    grant.mailbox_id: list(grant.folders) for grant in grants
                }
                update["email_folder_allowlist"] = sorted(
                    {folder for grant in grants for folder in grant.folders}
                )
        if "labels" in body:
            metadata = dict(update["metadata"] if "metadata" in update else session.metadata)
            labels = _normalize_session_labels(body.get("labels"))
            if labels:
                metadata["labels"] = labels
            else:
                metadata.pop("labels", None)
            update["metadata"] = metadata
        if "codex_auth_mode" in body:
            if selected_backend != "container_worker_vm":
                raise ValueError("codex_auth_mode is supported only for container worker agents")
            codex_auth_mode = normalize_codex_auth_mode(body.get("codex_auth_mode"))
            agent_provider = (
                str(session.metadata.get("agent_provider") or "codex").strip().casefold()
            )
            if codex_auth_mode is CodexAuthMode.CHATGPT_SUBSCRIPTION and agent_provider != "codex":
                raise ValueError("chatgpt_subscription is supported only for Codex agents")
            update["codex_auth_mode"] = codex_auth_mode

        updated = session.model_copy(update=update)
        self.runtime.session_repository.upsert_session(updated)
        _sync_added_discord_session_cursors(
            self.runtime,
            previous_session=session,
            updated_session=updated,
        )
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": selected_backend,
            "target_name": target_name,
            "action": "edit",
            "message": f"Updated policy for session {updated.session_id}",
            "status": "updated",
            "session_id": updated.session_id,
            "effective_tool_policy": self.effective_tool_policy_payload(updated),
        }

    def delete_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
    ) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(backend)
        if selected_backend == "libvirt_vm":
            direct_session = self.runtime.session_repository.get_session(agent_id)
            if direct_session is not None:
                vm_name = str(direct_session.vm_id or "").strip()
                if not vm_name:
                    raise ValueError(f"Session {agent_id!r} is not linked to a VM")
            else:
                vm_name = agent_id
                matches = self.runtime.session_repository.list_sessions_for_vm(vm_name)
                if len(matches) > 1:
                    session_ids = ", ".join(sorted(session.session_id for session in matches))
                    raise ValueError(
                        f"Refusing to delete VM {vm_name!r}: multiple linked sessions "
                        f"({session_ids}). Delete is only allowed when the VM-to-session link is unambiguous."
                    )
            matches = self.runtime.session_repository.list_sessions_for_vm(vm_name)
            if len(matches) > 1:
                session_ids = ", ".join(sorted(session.session_id for session in matches))
                raise ValueError(
                    f"Refusing to delete VM {vm_name!r}: multiple linked sessions "
                    f"({session_ids}). Delete is only allowed when the VM-to-session link is unambiguous."
                )
            session_ids_to_delete = {session.session_id for session in matches}
            result = self.destroy_vm(vm_name)
            removed_assistant_mappings = (
                self._delete_chat_assistant_session_mappings(session_ids_to_delete)
                if result.success
                else 0
            )
            message = result.message
            if removed_assistant_mappings:
                message = f"{message} removed_assistant_mappings={removed_assistant_mappings}"
            return {
                "success": result.success,
                "agent_id": vm_name,
                "backend": "libvirt_vm",
                "target_name": vm_name,
                "action": "delete",
                "message": message,
                "status": "deleted" if result.success else "failed",
                "session_id": matches[0].session_id if len(matches) == 1 else None,
            }

        session, record = self._resolve_container_agent_session(agent_id, require_session=False)
        session_id = (
            str(getattr(session, "session_id", "") or "").strip()
            if session is not None
            else str(getattr(record, "session_id", "") or "").strip()
        )
        if not session_id:
            raise ValueError(f"Unknown container worker agent target: {agent_id}")
        git_write_access_revoked = _revoke_container_git_write_access_for_session(session)
        target_name = (
            self._container_session_target_name(session)
            if session is not None
            else str(getattr(record, "target_name", "") or agent_id)
        )
        execution_records = self._container_execution_records_for_session(session_id)
        worker_backend = self.container_worker_backend(
            directory_path=(
                session.directory_path
                if session is not None
                else getattr(record, "directory_path", None)
            ),
            worker_url=self._container_worker_url_for_target(
                session=session,
                record=record,
                records=execution_records,
            ),
        )
        if record is not None:
            worker_backend.destroy(record.execution_id)
        worker_session_deleted = worker_backend.delete_session(session_id)
        worker_session_removed = self._worker_delete_session_removed(worker_session_deleted)
        container_repository = self._container_worker_execution_repository()
        removed_mappings = 0
        list_by_session = getattr(container_repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            records = [
                candidate
                for candidate in container_repository.read_all().values()
                if candidate.session_id == session_id
            ]
        for candidate in records:
            container_repository.delete(candidate.execution_id)
            removed_mappings += 1
        managed_git = self._deps.managed_git_admin
        if managed_git is not None:
            managed_git.unbind_all("sessions", session_id)
            managed_git.unbind_all("authorized_sessions", session_id)
        removed_session = self.runtime.session_repository.delete_session(session_id)
        removed_assistant_mappings = self._delete_chat_assistant_session_mappings({session_id})
        removed_approvals = _prune_approvals_for_sessions(self.runtime, session_ids={session_id})
        removed_pr_map_entries, removed_streams = _prune_pr_map(
            self.config.pr_map_path,
            session_ids={session_id},
        )
        removed_cursors, removed_feedback = _prune_pr_feedback_store(
            self.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": "container_worker_vm",
            "target_name": target_name,
            "action": "delete",
            "message": (
                f"Deleted container worker session {session_id}; "
                f"removed_mappings={removed_mappings} "
                f"removed_session={int(removed_session)} "
                f"removed_assistant_mappings={removed_assistant_mappings} "
                f"removed_approvals={removed_approvals} "
                f"removed_pr_map_entries={removed_pr_map_entries} "
                f"removed_feedback_cursors={removed_cursors} "
                f"removed_feedback_items={removed_feedback} "
                f"worker_session_deleted={int(worker_session_removed)} "
                f"git_write_access_revoked={int(git_write_access_revoked)}"
            ),
            "status": "deleted",
            "session_id": session_id,
        }

    def run_admin_agent_action(
        self,
        agent_id: str,
        action: str,
        *,
        backend: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(backend)
        body = payload or {}
        if selected_backend == "libvirt_vm":
            if action == "start":
                result = self._vm_lifecycle_port().start_vm(agent_id)
            elif action == "shutdown":
                result = self.shutdown_vm(agent_id)
            elif action == "enable-agent":
                result = self.enable_agent(agent_id)
            elif action == "disable-agent":
                result = self.disable_agent(agent_id)
            elif action == "kill-agent":
                pid = body.get("pid")
                if pid in {None, ""}:
                    raise ValueError("kill-agent requires a running agent pid")
                result = self.kill_agent(agent_id, int(pid))
            elif action == "prompt-agent":
                return self._agent_prompt_dispatch_port().prompt_admin_agent(
                    agent_id, backend=selected_backend, payload=body
                )
            elif action == "resume-agent":
                prompt = str(body.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError("resume-agent requires a non-empty prompt")
                result = self.enqueue_agent_prompt(
                    agent_id,
                    prompt,
                    model=str(body.get("model") or "").strip() or None,
                    resume_mode=str(body.get("resume_mode") or "same_context").strip()
                    or "same_context",
                )
            elif action == "install-skills":
                return self.install_admin_agent_skills(
                    agent_id,
                    backend="libvirt_vm",
                    skill_names=self._agent_string_list(body, "codex_skills"),
                    vm_user=self._agent_optional_string(body, "vm_user") or "agent",
                )
            elif action == "destroy":
                result = self.destroy_vm(agent_id)
            else:
                raise LookupError(f"Unknown VM agent action: {action}")
            return {
                "success": result.success,
                "agent_id": result.vm_name,
                "backend": "libvirt_vm",
                "target_name": result.vm_name,
                "action": result.action,
                "message": result.message,
                "provider": result.provider,
                "provider_operation_id": result.provider_operation_id,
                "thread_id": result.thread_id,
                "queued": result.queued,
                "queue_key": result.queue_key,
                "queue_reason": result.queue_reason,
                "dispatch_id": result.dispatch_id,
                "session_id": result.session_id,
                "model": result.model,
                "model_provider_mode": result.model_provider_mode,
                "local_inference_capacity_enabled": result.local_inference_capacity_enabled,
                "local_inference_active_count": result.local_inference_active_count,
                "local_inference_max_concurrency": result.local_inference_max_concurrency,
                "local_inference_slot_id": result.local_inference_slot_id,
                "target_dir": result.target_dir,
                "guest_repo_name": result.guest_repo_name,
                "remote_url": result.remote_url,
            }
        if selected_backend == "container_worker_vm":
            if action == "install-skills":
                return self.install_admin_agent_skills(
                    agent_id,
                    backend="container_worker_vm",
                    skill_names=self._agent_string_list(body, "codex_skills"),
                    vm_user=self._agent_optional_string(body, "vm_user") or "agent",
                )
            if action == "prompt-agent":
                return self._agent_prompt_dispatch_port().prompt_admin_agent(
                    agent_id,
                    backend=selected_backend,
                    payload=body,
                )
            session, record = self._resolve_container_agent_session(agent_id, require_session=False)
            is_session_target = session is not None and record is None
            session_id = (
                str(session.session_id).strip()
                if session is not None
                else str(getattr(record, "session_id", "") or "").strip()
            )
            execution_records = (
                self._container_execution_records_for_session(session_id) if session_id else []
            )
            worker_backend = self.container_worker_backend(
                directory_path=(
                    session.directory_path
                    if session is not None
                    else getattr(record, "directory_path", None)
                ),
                worker_url=self._container_worker_url_for_target(
                    session=session,
                    record=record,
                    records=execution_records,
                ),
            )
            if action == "stop":
                if session is not None and record is None:
                    active_records = [
                        candidate
                        for candidate in execution_records
                        if self._agent_status_is_active(
                            self._container_agent_status(candidate.last_status)
                        )
                    ]
                    if not active_records:
                        raise ValueError(
                            f"No active container executions for session {session.session_id}"
                        )
                    statuses = [
                        worker_backend.stop(candidate.execution_id) for candidate in active_records
                    ]
                    status = statuses[-1]
                    message = (
                        f"Stopped {len(statuses)} active container execution"
                        f"{'' if len(statuses) == 1 else 's'} for session {session.session_id}"
                    )
                else:
                    status = worker_backend.stop(agent_id)
                    message = status.message or "stop completed"
            elif action == "interrupt-active-tool":
                if session is not None and record is None:
                    active_records = [
                        candidate
                        for candidate in execution_records
                        if self._agent_status_is_active(
                            self._container_agent_status(candidate.last_status)
                        )
                    ]
                    if not active_records:
                        raise ValueError(
                            f"No active container executions for session {session.session_id}"
                        )
                    statuses = [
                        worker_backend.interrupt_active_tool(candidate.execution_id)
                        for candidate in active_records
                    ]
                    status = statuses[-1]
                    message = (
                        f"Requested safe active-tool interruption for {len(statuses)} active "
                        f"container execution{'' if len(statuses) == 1 else 's'}"
                    )
                else:
                    status = worker_backend.interrupt_active_tool(agent_id)
                    interruption = (
                        status.metadata.get("active_tool_interruption")
                        if isinstance(status.metadata, dict)
                        else None
                    )
                    message = (
                        str(interruption.get("message") or "")
                        if isinstance(interruption, dict)
                        else ""
                    ) or "Requested safe active-tool interruption"
            elif action == "destroy":
                if session is not None and record is None:
                    if not execution_records:
                        raise ValueError(
                            f"No container executions for session {session.session_id}"
                        )
                    statuses = [
                        worker_backend.destroy(candidate.execution_id)
                        for candidate in execution_records
                    ]
                    status = statuses[-1]
                    message = (
                        f"Destroyed {len(statuses)} container execution"
                        f"{'' if len(statuses) == 1 else 's'} for session {session.session_id}"
                    )
                else:
                    status = worker_backend.destroy(agent_id)
                    message = status.message or "destroy completed"
            else:
                raise LookupError(f"Unknown container worker agent action: {action}")
            return {
                "success": True,
                "agent_id": session.session_id if is_session_target else status.execution_id,
                "backend": "container_worker_vm",
                "target_name": (
                    self._container_session_target_name(session)
                    if is_session_target
                    else status.target_name
                ),
                "action": action,
                "message": message,
                "status": status.status.value,
                "session_id": session.session_id if session is not None else status.session_id,
            }
        raise ValueError(f"Unknown agent backend: {selected_backend}")
