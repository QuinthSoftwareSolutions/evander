from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import GatewayConfig, load_config
from .models import AuditEvent, ToolName
from .policy import PolicyError
from .runtime import RuntimeContext, build_runtime


def _redact_args(config: GatewayConfig, args: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in args.items():
        redacted[key] = "<redacted>" if key in config.redacted_keys else value
    return redacted


def create_server(context: RuntimeContext) -> FastMCP:
    server = FastMCP("Agent Sandbox Gateway", json_response=True)

    def audit_success(
        session_id: str,
        tool_name: ToolName,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        session = context.policy.get_session(session_id)
        context.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id,
                vm_id=session.vm_id,
                tool_name=tool_name.value,
                decision="allowed",
                args=_redact_args(context.config, args),
                result=result,
            )
        )

    def audit_failure(session_id: str | None, tool_name: ToolName, args: dict[str, Any], error: str) -> None:
        vm_id = None
        if session_id:
            session = context.sessions.get(session_id)
            vm_id = None if session is None else session.vm_id
        context.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id,
                vm_id=vm_id,
                tool_name=tool_name.value,
                decision="denied",
                args=_redact_args(context.config, args),
                result={"error": error},
            )
        )

    @server.tool()
    def discord_get_commands(session_id: str, since_cursor: str | None = None) -> list[dict[str, Any]]:
        args = {"session_id": session_id, "since_cursor": since_cursor}
        try:
            session = context.policy.require_tool(session_id, ToolName.DISCORD_GET_COMMANDS)
            raw_commands = context.discord_service.get_commands(since_cursor)
            commands: list[dict[str, Any]] = []
            denied_count = 0
            for command in raw_commands:
                try:
                    context.policy.require_discord_command_source(session, command)
                except PolicyError as exc:
                    denied_count += 1
                    audit_failure(
                        session_id,
                        ToolName.DISCORD_GET_COMMANDS,
                        {
                            **args,
                            "command_id": command.command_id,
                            "source_metadata": command.metadata,
                        },
                        str(exc),
                    )
                    continue
                commands.append(command.model_dump(mode="json"))
            audit_success(
                session_id,
                ToolName.DISCORD_GET_COMMANDS,
                args,
                {"count": len(commands), "denied_count": denied_count},
            )
            return commands
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DISCORD_GET_COMMANDS, args, str(exc))
            raise

    @server.tool()
    def discord_post_status(session_id: str, channel_id: str, message: str) -> dict[str, Any]:
        args = {"session_id": session_id, "channel_id": channel_id, "message": message}
        try:
            session = context.policy.require_tool(session_id, ToolName.DISCORD_POST_STATUS)
            context.policy.require_discord_channel(session, channel_id)
            result = context.discord_service.post_status(message, channel_id, session_id)
            audit_success(session_id, ToolName.DISCORD_POST_STATUS, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DISCORD_POST_STATUS, args, str(exc))
            raise

    @server.tool()
    def email_list_unread(session_id: str, folder: str, limit: int = 10) -> list[dict[str, Any]]:
        args = {"session_id": session_id, "folder": folder, "limit": limit}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_LIST_UNREAD)
            context.policy.require_email_folder(session, folder)
            messages = [
                message.model_dump(mode="json")
                for message in context.email_service.list_unread(folder=folder, limit=limit)
            ]
            audit_success(session_id, ToolName.EMAIL_LIST_UNREAD, args, {"count": len(messages)})
            return messages
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_LIST_UNREAD, args, str(exc))
            raise

    @server.tool()
    def email_get_message(session_id: str, message_id: str) -> dict[str, Any]:
        args = {"session_id": session_id, "message_id": message_id}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_GET_MESSAGE)
            message = context.email_service.get_message(message_id)
            context.policy.require_email_folder(session, message.folder)
            result = message.model_dump(mode="json")
            audit_success(session_id, ToolName.EMAIL_GET_MESSAGE, args, {"message_id": message_id})
            return result
        except (KeyError, PolicyError) as exc:
            audit_failure(session_id, ToolName.EMAIL_GET_MESSAGE, args, str(exc))
            raise

    @server.tool()
    def email_create_draft(
        session_id: str,
        body: str,
        thread_or_message_id: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "thread_or_message_id": thread_or_message_id,
            "subject": subject,
            "body": body,
        }
        try:
            context.policy.require_tool(session_id, ToolName.EMAIL_CREATE_DRAFT)
            draft = context.email_service.create_draft(thread_or_message_id, subject, body)
            result = draft.model_dump(mode="json")
            audit_success(session_id, ToolName.EMAIL_CREATE_DRAFT, args, {"draft_id": draft.draft_id})
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_CREATE_DRAFT, args, str(exc))
            raise

    @server.tool()
    def approval_submit(
        session_id: str,
        action_type: str,
        payload: dict[str, Any],
        human_summary: str,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "action_type": action_type,
            "payload": payload,
            "human_summary": human_summary,
        }
        try:
            context.policy.require_tool(session_id, ToolName.APPROVAL_SUBMIT)
            approval = context.policy.create_approval(session_id, action_type, payload, human_summary)
            result = approval.model_dump(mode="json")
            audit_success(session_id, ToolName.APPROVAL_SUBMIT, args, {"request_id": approval.request_id})
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.APPROVAL_SUBMIT, args, str(exc))
            raise

    @server.tool()
    def approval_check(session_id: str, request_id: str) -> dict[str, Any]:
        args = {"session_id": session_id, "request_id": request_id}
        try:
            context.policy.require_tool(session_id, ToolName.APPROVAL_CHECK)
            approval = context.policy.get_approval(request_id)
            if approval.session_id != session_id:
                raise PolicyError(f"Approval request {request_id} does not belong to session {session_id}")
            result = approval.model_dump(mode="json")
            audit_success(session_id, ToolName.APPROVAL_CHECK, args, {"status": approval.status.value})
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.APPROVAL_CHECK, args, str(exc))
            raise

    @server.tool()
    def email_send_approved(session_id: str, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        args = {"session_id": session_id, "request_id": request_id, "payload": payload}
        try:
            context.policy.require_tool(session_id, ToolName.EMAIL_SEND_APPROVED)
            approval = context.policy.require_approved(request_id, payload)
            if approval.session_id != session_id:
                raise PolicyError(f"Approval request {request_id} does not belong to session {session_id}")
            result = context.email_service.send_from_draft(request_id=request_id, payload=payload)
            audit_success(session_id, ToolName.EMAIL_SEND_APPROVED, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND_APPROVED, args, str(exc))
            raise

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Agent Sandbox MCP gateway.")
    parser.add_argument("--config", default="config/gateway.yaml", help="Path to the gateway YAML config.")
    args = parser.parse_args()

    config = load_config(args.config)
    context = build_runtime(config)
    server = create_server(context)
    server.settings.host = config.host
    server.settings.port = config.port
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
