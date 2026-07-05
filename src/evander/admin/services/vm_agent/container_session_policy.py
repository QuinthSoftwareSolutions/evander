from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    SessionManifest,
    WorkflowEditorFieldOption,
    _normalize_session_labels,
    tool_name_value,
)


class VmAgentContainerSessionPolicyMixin:
    def _agent_session_policy_payload(
        self, session: SessionManifest | None
    ) -> dict[str, Any] | None:
        if session is None:
            return None
        labels = _normalize_session_labels(session.metadata.get("labels"))
        return {
            "session_id": session.session_id,
            "model": session.model,
            "expires_at": session.expires_at,
            "allowed_tools": [tool_name_value(tool) for tool in session.allowed_tools],
            "approval_required_tools": [
                tool_name_value(tool) for tool in session.approval_required_tools
            ],
            "approval_tool_settings": {
                tool_name: settings.model_dump(mode="json")
                for tool_name, settings in session.approval_tool_settings.items()
            },
            "approval_notification_target_ids": list(session.approval_notification_target_ids),
            "approval_notification_target_options": [
                option.model_dump(mode="json")
                for option in self._approval_notification_target_options(session)
            ],
            "tool_argument_policies": {
                tool_name: [rule.model_dump(mode="json") for rule in rules]
                for tool_name, rules in session.tool_argument_policies.items()
            },
            "allowed_script_names": list(session.allowed_script_names),
            "allowed_contact_ids": (
                None if session.allowed_contact_ids is None else list(session.allowed_contact_ids)
            ),
            "discord_channels": list(session.discord_channel_allowlist),
            "discord_channel_allowlist": list(session.discord_channel_allowlist),
            "teams_channel_allowlist": list(session.teams_channel_allowlist),
            "teams_chat_allowlist": list(session.teams_chat_allowlist),
            "teams_user_allowlist": list(session.teams_user_allowlist),
            "email_mailboxes": list(session.email_mailbox_allowlist),
            "email_folders": list(session.email_folder_allowlist),
            "communication_file_receive_enabled": session.communication_file_receive_enabled,
            "communication_file_send_enabled": session.communication_file_send_enabled,
            "communication_file_max_bytes": session.communication_file_max_bytes,
            "communication_file_allowed_mime_types": list(
                session.communication_file_allowed_mime_types
            ),
            "communication_file_allowed_extensions": list(
                session.communication_file_allowed_extensions
            ),
            "communication_file_blocked_extensions": list(
                session.communication_file_blocked_extensions
            ),
            "communication_file_require_approval_for_send": (
                session.communication_file_require_approval_for_send
            ),
            "web_search_enabled": session.web_search_enabled,
            "metadata": dict(session.metadata),
            "labels": labels,
        }

    def _approval_notification_target_options(
        self, session: SessionManifest | None
    ) -> list[WorkflowEditorFieldOption]:
        if session is None:
            return []
        try:
            return self._communication_notification_target_options(session.directory_path)
        except Exception:
            return []

    def effective_tool_policy_payload(
        self,
        session: SessionManifest | None,
        *,
        effective_policy: Any | None = None,
        tool_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if session is None:
            return None
        effective_policy = effective_policy or self.runtime.policy_config.resolve_for_session_manifest(
            session
        )
        if tool_summary is None:
            tool_summary = self.runtime.policy.allowed_tool_summary_for_session(
                session,
                effective_policy=effective_policy,
            )
        inherited_approval_required_tools: list[str] = []
        approval_required_tools: list[str] = []
        seen_tools: set[str] = set()
        for tool in effective_policy.approval_required_tools:
            value = tool_name_value(tool)
            if value in seen_tools:
                continue
            seen_tools.add(value)
            inherited_approval_required_tools.append(value)
            approval_required_tools.append(value)
        for tool in session.approval_required_tools:
            value = tool_name_value(tool)
            if value in seen_tools:
                continue
            seen_tools.add(value)
            approval_required_tools.append(value)

        tool_argument_policies: dict[str, list[dict[str, Any]]] = {}
        for source in (effective_policy.tool_argument_policies, session.tool_argument_policies):
            for tool_name, rules in source.items():
                tool_rules = tool_argument_policies.setdefault(tool_name, [])
                tool_rules.extend(rule.model_dump(mode="json") for rule in rules)

        approval_tool_settings = {
            tool_name_value(tool): self.runtime.policy.approval_grant_settings_for_tool(
                session,
                tool,
                effective_policy=effective_policy,
            ).model_dump(mode="json")
            for tool in self.runtime.policy._iter_known_tool_names()
        }
        mcp_tool_policies = {
            tool_name: rule.model_dump(mode="json")
            for tool_name, rule in effective_policy.mcp_tool_policies.items()
        }

        return {
            "allowed_tools": [tool_name_value(tool) for tool in session.allowed_tools],
            "tool_summary": tool_summary,
            "inherited_approval_required_tools": inherited_approval_required_tools,
            "approval_required_tools": approval_required_tools,
            "approval_tool_settings": approval_tool_settings,
            "mcp_tool_policies": mcp_tool_policies,
            "tool_argument_policies": tool_argument_policies,
        }

    agent_session_policy_payload = _agent_session_policy_payload
    approval_notification_target_options = _approval_notification_target_options
