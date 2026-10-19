"""Shared, session-authorized host commands for MCP and deterministic effects."""

from __future__ import annotations

import hashlib
from typing import Any, Callable

from evander.models import AuditEvent, ToolName
from evander.policy import PolicyError
from evander.integration_services.teams import parse_teams_target


class TeamsMessageCommands:
    def __init__(self, runtime, *, audit_success=None, audit_failure=None, workflow_context=None):
        self.runtime = runtime
        self.audit_success = audit_success
        self.audit_failure = audit_failure
        self.workflow_context = workflow_context or {}

    def _audit(self, session_id, args, result=None, error=None):
        tool = ToolName.TEAMS_UPDATE_MESSAGE
        session_id = session_id if isinstance(session_id, str) else None
        denied = isinstance(error, PolicyError)
        if error is None and self.audit_success is not None:
            self.audit_success(session_id, tool, args, result)
            return
        if error is not None and self.audit_failure is not None:
            self.audit_failure(
                session_id,
                tool,
                args,
                str(error),
                decision="denied" if denied else "error",
                error_class="policy_error" if denied else "teams_edit_error",
            )
            return
        session = self.runtime.session_repository.get_session(session_id) if session_id else None
        self.runtime.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id or None,
                tool_name=tool.value,
                provider="teams",
                directory_path=session.directory_path if session else None,
                vm_id=session.vm_id if session else None,
                workflow_run_id=self.workflow_context.get("workflow_run_id"),
                correlation_id=self.workflow_context.get("correlation_id"),
                decision=("denied" if denied else "error") if error else "allowed",
                reason=str(error) if error else None,
                args={**args, "workflow": self.workflow_context},
                result=result or {"error_class": "policy_error" if denied else "teams_edit_error"},
            )
        )

    def update_message(
        self,
        session_id: str,
        channel_target: str,
        message_id: str,
        message: str,
        thread_id: str | None = None,
        expected_revision: int | None = None,
        *,
        authorize_session: Callable[[Any], None] | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "channel_target": channel_target,
            "message_id": message_id,
            "thread_id": thread_id,
            "expected_revision": expected_revision,
            "message_sha256": hashlib.sha256(str(message).encode()).hexdigest(),
        }
        try:
            if not isinstance(session_id, str) or not session_id.strip():
                raise PolicyError("A real active session_id is required")
            session = self.runtime.policy.require_tool(session_id, ToolName.TEAMS_UPDATE_MESSAGE)
            if authorize_session is not None:
                try:
                    authorize_session(session)
                except ValueError as exc:
                    raise PolicyError(str(exc)) from exc
            if not isinstance(message, str) or not message.strip():
                raise ValueError("message must be non-empty replacement text")
            if not isinstance(message_id, str) or not message_id.strip():
                raise ValueError("message_id must be non-empty")
            if expected_revision is not None and (
                type(expected_revision) is not int or expected_revision < 1
            ):
                raise ValueError("expected_revision must be a positive integer")
            # Resolve configured target references without resolving user aliases
            # or creating chats. Either capability permits locating the receipt;
            # the actual recorded parent decides the final required capability.
            try:
                target = self.runtime.policy.require_teams_channel_target(
                    session, channel_target, capability="write"
                )
            except PolicyError:
                target = self.runtime.policy.require_teams_channel_target(
                    session, channel_target, capability="reply"
                )
            receipt = self.runtime.teams_messages.get_outbound_receipt(target, message_id)
            if receipt is None or receipt.session_id != session.session_id:
                raise PolicyError("No outbound ownership receipt for this session and conversation")
            parsed = parse_teams_target(target)
            if (parsed.kind == "chat" and thread_id is not None) or (
                thread_id is not None and thread_id != receipt.thread_id
            ):
                raise PolicyError("thread_id does not match the recorded Teams message")
            capability = (
                "reply" if parsed.kind == "channel" and receipt.thread_id != message_id else "write"
            )
            self.runtime.policy.require_teams_channel_target(
                session, channel_target, capability=capability
            )
            result = self.runtime.teams_service_for_session(session_id).update_message(
                session_id=session_id,
                channel_target=target,
                message_id=message_id,
                message=message,
                thread_id=thread_id,
                expected_revision=expected_revision,
            )
        except Exception as exc:
            self._audit(session_id, args, error=exc)
            raise
        self._audit(session_id, args, result=result)
        return result
