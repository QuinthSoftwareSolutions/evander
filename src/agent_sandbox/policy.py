from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .config import GatewayConfig
from .models import (
    ApprovalRequest,
    ApprovalStatus,
    DiscordCommandEnvelope,
    SessionManifest,
    TeamsMessageEnvelope,
    ToolName,
    digest_payload,
)
from .services import normalize_teams_target, teams_target_from_message
from .policy_config import EffectivePolicyConfig, PolicyConfigResolver
from .storage import JsonStore


class PolicyError(RuntimeError):
    """Raised when a tool action violates the session policy."""


def _redact_nested_payload(redacted_keys: set[str], value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested_value in value.items():
            if key in redacted_keys:
                redacted[key] = "<redacted>"
                continue
            redacted[key] = _redact_nested_payload(redacted_keys, nested_value)
        return redacted
    if isinstance(value, list):
        return [_redact_nested_payload(redacted_keys, item) for item in value]
    return value


class PolicyEngine:
    def __init__(
        self,
        config: GatewayConfig,
        session_store: JsonStore[SessionManifest],
        approval_store: JsonStore[ApprovalRequest],
        *,
        policy_config: PolicyConfigResolver | None = None,
    ) -> None:
        self.config = config
        self.session_store = session_store
        self.approval_store = approval_store
        self.policy_config = policy_config or PolicyConfigResolver(
            config,
            session_store=session_store,
        )

    def get_session(self, session_id: str) -> SessionManifest:
        session = self.session_store.get(session_id)
        if session is None:
            raise PolicyError(f"Unknown session: {session_id}")
        if session.expires_at <= datetime.now(UTC):
            raise PolicyError(f"Expired session: {session_id}")
        return session

    def require_tool(self, session_id: str, tool_name: ToolName) -> SessionManifest:
        session = self.get_session(session_id)
        if tool_name not in session.allowed_tools:
            raise PolicyError(f"Tool {tool_name.value} is not allowed for session {session_id}")
        if tool_name == ToolName.SCHEDULE_RESUME and not str(session.vm_id or "").strip():
            raise PolicyError(f"Tool {tool_name.value} requires a session with a VM binding")
        if tool_name in {
            ToolName.HAS_PENDING_FEEDBACK,
            ToolName.LIST_PULL_REQUESTS,
            ToolName.GET_PULL_REQUEST,
            ToolName.LIST_UNREAD_FEEDBACK,
            ToolName.LIST_ALL_FEEDBACK,
            ToolName.POST_PR_COMMENT,
            ToolName.SUBMIT_PR_REVIEW,
        }:
            self.require_pr_feedback_streams(session)
        return session

    def require_pr_feedback_streams(self, session: SessionManifest) -> list[dict[str, str]]:
        streams = session.metadata.get("pr_feedback_streams")
        if not isinstance(streams, list) or not streams:
            raise PolicyError(
                f"Session {session.session_id} is missing metadata.pr_feedback_streams configuration"
            )

        validated: list[dict[str, str]] = []
        for stream in streams:
            if not isinstance(stream, dict):
                raise PolicyError(
                    f"Session {session.session_id} has invalid pr feedback stream configuration"
                )
            provider = str(stream.get("provider", "")).strip()
            repo = str(stream.get("repo", "")).strip()
            pr_id = str(stream.get("pr_id", "")).strip()
            if not provider or not repo or not pr_id:
                raise PolicyError(
                    f"Session {session.session_id} has incomplete pr feedback stream metadata"
                )
            validated.append({"provider": provider, "repo": repo, "pr_id": pr_id})
        return validated

    def require_pr_feedback_stream(
        self,
        session: SessionManifest,
        *,
        provider: str,
        repo: str,
        pr_id: str,
    ) -> None:
        streams = self.require_pr_feedback_streams(session)
        allowed = {(stream["provider"], stream["repo"], stream["pr_id"]) for stream in streams}
        key = (provider, repo, pr_id)
        if key not in allowed:
            raise PolicyError(
                f"PR feedback stream {provider}:{repo}:{pr_id} is not allowlisted for session "
                f"{session.session_id}"
            )

    def require_pr_feedback_repo(
        self,
        session: SessionManifest,
        *,
        provider: str,
        repo: str,
    ) -> None:
        streams = self.require_pr_feedback_streams(session)
        allowed = {(stream["provider"], stream["repo"]) for stream in streams}
        key = (provider, repo)
        if key not in allowed:
            raise PolicyError(
                f"PR repository {provider}:{repo} is not allowlisted for session {session.session_id}"
            )

    def require_discord_channel(self, session: SessionManifest, channel_id: str) -> None:
        if channel_id not in session.discord_channel_allowlist:
            raise PolicyError(f"Discord channel is not allowlisted for session {session.session_id}")

    def require_discord_command_source(
        self,
        session: SessionManifest,
        command: DiscordCommandEnvelope,
    ) -> None:
        effective_policy = self.effective_policy_for_session(session)
        source = str(command.metadata.get("source", ""))
        if source != "discord_bot_api":
            raise PolicyError(f"Discord command {command.command_id} has untrusted source {source!r}")

        source_channel_id = str(command.metadata.get("channel_id", command.channel_id)).strip()
        source_author_id = str(command.metadata.get("author_id", command.author_id)).strip()
        if not source_channel_id or not source_author_id:
            raise PolicyError(
                f"Discord command {command.command_id} is missing required source metadata"
            )

        allowlisted_channel = source_channel_id in session.discord_channel_allowlist
        allowlisted_operator = source_author_id in effective_policy.discord_operator_user_allowlist
        if not (allowlisted_channel or allowlisted_operator):
            raise PolicyError(
                f"Discord command {command.command_id} source is not allowlisted for session "
                f"{session.session_id}"
            )

    def require_teams_message_source(
        self,
        session: SessionManifest,
        message: TeamsMessageEnvelope,
    ) -> None:
        source = str(message.metadata.get("source", ""))
        if source == "teams_graph_api_status":
            # Historical name: this tag marks a Teams message previously posted
            # through the Graph API by one of our agent sessions. Visibility is
            # still enforced by the allowlisted Teams target below.
            pass
        elif source != "teams_graph_api":
            raise PolicyError(f"Teams message {message.cursor} has untrusted source {source!r}")

        try:
            target = teams_target_from_message(
                team_id=message.team_id,
                channel_id=message.channel_id,
                chat_id=message.chat_id,
                metadata=message.metadata,
            )
        except ValueError as exc:
            raise PolicyError(
                f"Teams message {message.cursor} is missing required source metadata"
            ) from exc

        if target not in {normalize_teams_target(value) for value in session.teams_channel_allowlist if value.strip()}:
            raise PolicyError(
                f"Teams message {message.cursor} source is not allowlisted for session "
                f"{session.session_id}"
            )

    def require_teams_channel_target(self, session: SessionManifest, channel_target: str) -> str:
        try:
            normalized_target = normalize_teams_target(channel_target)
        except ValueError as exc:
            raise PolicyError(str(exc)) from exc
        if normalized_target not in {
            normalize_teams_target(value) for value in session.teams_channel_allowlist if value.strip()
        }:
            raise PolicyError(f"Teams target is not allowlisted for session {session.session_id}")
        return normalized_target

    def require_email_folder(self, session: SessionManifest, folder: str) -> None:
        if folder not in session.email_folder_allowlist:
            raise PolicyError(f"Email folder is not allowlisted for session {session.session_id}")

    def require_email_mailbox(self, session: SessionManifest, mailbox_id: str) -> None:
        mailbox_allowlist = session.email_mailbox_allowlist or self.effective_policy_for_session(
            session
        ).email_mailbox_allowlist
        if mailbox_id not in mailbox_allowlist:
            raise PolicyError(f"Email mailbox is not allowlisted for session {session.session_id}")

    def require_email_folder_for_mailbox(
        self,
        session: SessionManifest,
        mailbox_id: str,
        folder: str,
    ) -> None:
        mailbox_folders = session.email_folder_allowlist_by_mailbox or self.effective_policy_for_session(
            session
        ).email_folder_allowlist_by_mailbox
        allowed_folders = mailbox_folders.get(mailbox_id, [])
        if folder in allowed_folders:
            return
        # Backward compatibility for older manifests that only had global folder allowlists.
        self.require_email_folder(session, folder)

    def require_email_send_enabled(self, session: SessionManifest) -> None:
        effective_policy = self.effective_policy_for_session(session)
        if not (session.email_send_enabled or effective_policy.email_send_enabled):
            raise PolicyError(f"Email send is disabled for session {session.session_id}")

    def require_draft_recipients(self, session: SessionManifest, recipients: list[str]) -> None:
        if not recipients:
            return
        normalized_recipients = [recipient.strip().lower() for recipient in recipients if recipient.strip()]
        if not normalized_recipients:
            return

        effective_policy = self.effective_policy_for_session(session)
        recipient_allowlist = (
            session.email_draft_recipient_allowlist or effective_policy.email_draft_recipient_allowlist
        )
        if recipient_allowlist:
            allowlisted = {
                recipient.strip().lower() for recipient in recipient_allowlist
            }
            denied = [recipient for recipient in normalized_recipients if recipient not in allowlisted]
            if denied:
                raise PolicyError(
                    f"Draft recipients are not allowlisted for session {session.session_id}: {', '.join(denied)}"
                )

        domain_allowlist = session.email_draft_domain_allowlist or effective_policy.email_draft_domain_allowlist
        if domain_allowlist:
            allowlisted_domains = {
                domain.strip().lower().lstrip("@") for domain in domain_allowlist
            }
            denied_domains: list[str] = []
            for recipient in normalized_recipients:
                _, separator, domain = recipient.rpartition("@")
                if not separator or domain not in allowlisted_domains:
                    denied_domains.append(recipient)
            if denied_domains:
                raise PolicyError(
                    f"Draft recipient domains are not allowlisted for session {session.session_id}: "
                    f"{', '.join(denied_domains)}"
                )

    def effective_policy_for_session(self, session: SessionManifest) -> EffectivePolicyConfig:
        return self.policy_config.resolve_for_session_manifest(session)

    def effective_policy_for_session_id(self, session_id: str) -> EffectivePolicyConfig:
        return self.policy_config.resolve_for_session_id(session_id)

    def create_approval(
        self,
        session_id: str,
        action_type: str,
        payload: dict[str, Any],
        human_summary: str,
    ) -> ApprovalRequest:
        session = self.get_session(session_id)
        effective_policy = self.effective_policy_for_session(session)
        redacted_payload = _redact_nested_payload(effective_policy.redacted_keys, payload)
        request = ApprovalRequest(
            request_id=f"apr_{digest_payload({'session_id': session_id, **payload})[:16]}",
            session_id=session_id,
            action_type=action_type,
            payload_digest=digest_payload(payload),
            human_summary=human_summary,
            payload=redacted_payload,
            directory_path=session.directory_path,
            vm_id=session.vm_id,
            tool_name=action_type,
            expires_at=datetime.now(UTC) + timedelta(seconds=effective_policy.approval_ttl_seconds),
        )
        self.approval_store.put(request.request_id, request)
        return request

    def get_approval(self, request_id: str) -> ApprovalRequest:
        request = self.approval_store.get(request_id)
        if request is None:
            raise PolicyError(f"Unknown approval request: {request_id}")
        session = self.session_store.get(request.session_id)
        if session is not None:
            redacted_payload = _redact_nested_payload(
                self.effective_policy_for_session(session).redacted_keys,
                request.payload,
            )
            if redacted_payload != request.payload:
                request.payload = redacted_payload
                self.approval_store.put(request.request_id, request)
        if request.status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED} and request.expires_at <= datetime.now(UTC):
            request.status = ApprovalStatus.EXPIRED
            self.approval_store.put(request.request_id, request)
        return request

    def require_approved(self, request_id: str, payload: dict[str, Any]) -> ApprovalRequest:
        request = self.get_approval(request_id)
        if request.status != ApprovalStatus.APPROVED:
            raise PolicyError(f"Approval request {request_id} is not approved")
        if request.payload_digest != digest_payload(payload):
            raise PolicyError(f"Approval payload mismatch for request {request_id}")
        if request.expires_at <= datetime.now(UTC):
            raise PolicyError(f"Approval request {request_id} has expired")
        return request
