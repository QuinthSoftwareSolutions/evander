from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from evander.config import MailboxConfig
from evander.email.providers import EmailDraft
from evander.messaging.targets import (
    CommunicationTargetDefinition,
    load_communication_targets_config,
)
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.runtime import (
    _build_discord_service,
    _build_email_provider,
    _build_teams_service,
)
from evander.services import EmailService, parse_teams_target, resolve_teams_send_target

NotificationSeverity = Literal["info", "success", "warning", "error"]


class NotificationSender:
    def __init__(self, *, project_dir: Path, runtime: Any, directory_path: str = "root") -> None:
        self.project_dir = project_dir
        self.runtime = runtime
        self.directory_path = directory_path or "root"

    def send(
        self,
        *,
        communication_target_id: str,
        message: str,
        severity: NotificationSeverity = "info",
        subject: str | None = None,
        thread_id: str | None = None,
        sender_session_id: str = "workflow",
    ) -> dict[str, Any]:
        target = self._resolve_target(communication_target_id)
        self._require_notify_capability(target)

        if target.provider == "teams":
            result = self._send_teams(
                target=target,
                message=message,
                thread_id=thread_id,
                sender_session_id=sender_session_id,
            )
        elif target.provider == "discord":
            result = self._send_discord(
                target=target,
                message=message,
                thread_id=thread_id,
                sender_session_id=sender_session_id,
            )
        elif target.provider == "email":
            result = self._create_email_draft(
                target=target,
                message=message,
                subject=subject,
            )
        else:
            raise ValueError(
                "effect:notification:send does not support "
                f"communication target provider {target.provider!r}"
            )

        output = dict(result)
        output.update(
            {
                "success": True,
                "provider": target.provider,
                "communication_target_id": target.id,
                "severity": severity,
            }
        )
        return output

    def _resolve_target(self, communication_target_id: str) -> CommunicationTargetDefinition:
        normalized_id = str(communication_target_id or "").strip().lower()
        if not normalized_id:
            raise ValueError("effect:notification:send requires communication_target_id")
        targets = load_communication_targets_config(
            runtime_dir=self.runtime.config.runtime_dir,
            directory_store=self.runtime.directories,
            directory_path=self.directory_path,
        ).by_id()
        target = targets.get(normalized_id)
        if target is None:
            raise ValueError(f"unknown communication target: {normalized_id}")
        return target

    @staticmethod
    def _require_notify_capability(target: CommunicationTargetDefinition) -> None:
        _require_target_capability(target, "notify")

    def _send_teams(
        self,
        *,
        target: CommunicationTargetDefinition,
        message: str,
        thread_id: str | None,
        sender_session_id: str,
    ) -> Mapping[str, Any]:
        parsed_target = parse_teams_target(target.address)
        if parsed_target.kind in {"chat", "user"} and thread_id is not None:
            raise ValueError("Teams chat targets do not support thread replies.")
        service = self._teams_service(target.address)
        send_target = resolve_teams_send_target(service, parsed_target.normalized)
        return service.post_status(
            message,
            send_target,
            sender_session_id,
            thread_id=thread_id,
        )

    def _send_discord(
        self,
        *,
        target: CommunicationTargetDefinition,
        message: str,
        thread_id: str | None,
        sender_session_id: str,
    ) -> Mapping[str, Any]:
        parent_channel_id, channel_id = self._resolve_discord_destination(
            target=target,
            thread_id=thread_id,
        )
        service = self._discord_service(
            sender_session_id=sender_session_id,
            target=target,
            additional_channel_ids=[channel_id] if channel_id != parent_channel_id else None,
        )
        resolve_identity = getattr(self.runtime, "resolve_outbound_identity", None)
        if (
            target.policy.sender_identity == "resolved"
            and callable(resolve_identity)
            and hasattr(service, "post_status_with_identity")
        ):
            identity_resolution = resolve_identity(
                session_id=sender_session_id,
                provider="discord",
                destination=channel_id,
            )
            identity_kwargs: dict[str, Any] = {
                "identity": identity_resolution.identity,
                "identity_status": identity_resolution.status,
            }
            if thread_id is not None:
                identity_kwargs.update(
                    {
                        "webhook_channel_id": parent_channel_id,
                        "thread_id": thread_id,
                    }
                )
            return service.post_status_with_identity(
                message,
                channel_id,
                sender_session_id,
                **identity_kwargs,
            )
        return service.post_status(message, channel_id, sender_session_id)

    def _resolve_discord_destination(
        self,
        *,
        target: CommunicationTargetDefinition,
        thread_id: str | None,
    ) -> tuple[str, str]:
        parent_channel_id = _discord_channel_id(target.address)
        if thread_id is None:
            if target.policy.discord_conversation_scope == "public_threads":
                raise ValueError(
                    "discord_target_requires_thread: this target only allows public child threads"
                )
            return parent_channel_id, parent_channel_id
        if target.policy.discord_conversation_scope not in {
            "public_threads",
            "channel_and_public_threads",
        }:
            raise ValueError(
                "discord_thread_not_authorized: the selected target does not allow public threads"
            )
        binding = DiscordInboxStore(
            self.runtime.config.runtime_dir / "discord-inbox-store.json"
        ).get_thread_binding(thread_id)
        if binding is None or binding.parent_channel_id != parent_channel_id:
            raise ValueError(
                "discord_thread_not_authorized: thread is not registered under the selected target"
            )
        if binding.archived:
            raise ValueError("discord_thread_archived: archived Discord threads are not reopened")
        if binding.locked:
            raise ValueError("discord_thread_locked: locked Discord threads cannot receive replies")
        return parent_channel_id, binding.thread_id

    def _create_email_draft(
        self,
        *,
        target: CommunicationTargetDefinition,
        message: str,
        subject: str | None,
    ) -> dict[str, Any]:
        mailbox_id = self._email_mailbox_id(target)
        draft = self._email_service().create_draft(
            mailbox_id,
            None,
            subject,
            message,
            [target.address],
        )
        return _email_draft_output(draft)

    def _resolved_config(self) -> Any:
        return self.runtime.provider_config.resolve_for_directory(self.directory_path)

    def _discord_service(
        self,
        *,
        sender_session_id: str = "workflow",
        target: CommunicationTargetDefinition | None = None,
        additional_channel_ids: list[str] | None = None,
    ) -> Any:
        allowed_channel_ids_provider = None
        if target is not None and target.provider == "discord":
            # A host-owned workflow is authorized by the selected directory
            # target and its checked capability.  The provider's operator
            # command allowlist controls polling; it must not accidentally
            # gate a configured workflow destination.
            channel_id = _discord_channel_id(target.address)

            def allowed_target_channel_ids() -> list[str]:
                return [channel_id, *(additional_channel_ids or [])]

            allowed_channel_ids_provider = allowed_target_channel_ids
        return _build_discord_service(
            self._resolved_config(),
            self.runtime.discord_commands,
            self.runtime.session_repository,
            self.runtime.policy_config,
            allowed_channel_ids_provider=allowed_channel_ids_provider,
        )

    def _teams_service(self, target_address: str) -> Any:
        parsed_target = parse_teams_target(target_address)
        normalized_target = parsed_target.normalized
        allowed_channel_targets = [] if parsed_target.kind == "user" else [normalized_target]
        allowed_user_targets = [normalized_target] if parsed_target.kind == "user" else []
        return _build_teams_service(
            self._resolved_config(),
            self.runtime.teams_messages,
            self.runtime.session_repository,
            directory_path=self.directory_path,
            allowed_channel_targets_provider=lambda: allowed_channel_targets,
            allowed_user_targets_provider=lambda: allowed_user_targets,
            user_chat_cache=getattr(self.runtime, "teams_user_chat_cache", None),
        )

    def _email_service(self) -> EmailService:
        provider = _build_email_provider(
            self._resolved_config(),
            self.runtime.emails,
            self.runtime.drafts,
            self.runtime.email_cursors,
        )
        return EmailService(provider)

    def _email_mailbox_id(self, target: CommunicationTargetDefinition) -> str:
        auth_profile_ref = target.policy.auth_profile_ref
        if not auth_profile_ref:
            raise ValueError(
                f"email communication target {target.id!r} requires policy.auth_profile_ref"
            )
        mailbox_id = _email_mailbox_id_from_auth_profile_ref(auth_profile_ref)
        mailbox_configs = self._resolved_config().email_mailboxes or [
            MailboxConfig(
                mailbox_id="default",
                provider="json_store",
                account_identifier="default",
            )
        ]
        mailbox_ids = {mailbox.mailbox_id for mailbox in mailbox_configs}
        if mailbox_id not in mailbox_ids:
            raise ValueError(
                f"email communication target {target.id!r} references unknown mailbox "
                f"{mailbox_id!r}"
            )
        return mailbox_id


class CommunicationMessageSender(NotificationSender):
    def _discord_service(
        self,
        *,
        sender_session_id: str = "workflow",
        target: CommunicationTargetDefinition | None = None,
        additional_channel_ids: list[str] | None = None,
    ) -> Any:
        normalized_session_id = str(sender_session_id or "").strip()
        if (
            not normalized_session_id
            or normalized_session_id == "workflow"
            or additional_channel_ids
        ):
            return super()._discord_service(
                sender_session_id=sender_session_id,
                target=target,
                additional_channel_ids=additional_channel_ids,
            )
        return _build_discord_service(
            self._resolved_config(),
            self.runtime.discord_commands,
            self.runtime.session_repository,
            self.runtime.policy_config,
            session_id=normalized_session_id,
            capability="reply" if additional_channel_ids else "write",
        )

    def send_message(
        self,
        *,
        communication_target_id: str,
        message: str,
        thread_id: str | None = None,
        subject: str | None = None,
        sender_session_id: str = "workflow",
    ) -> dict[str, Any]:
        target = self._resolve_target(communication_target_id)
        required_capability = "reply" if thread_id else "write"
        _require_target_capability(target, required_capability)

        if target.provider == "teams":
            result = self._send_teams(
                target=target,
                message=message,
                thread_id=thread_id,
                sender_session_id=sender_session_id,
            )
        elif target.provider == "discord":
            result = self._send_discord(
                target=target,
                message=message,
                thread_id=thread_id,
                sender_session_id=sender_session_id,
            )
        elif target.provider == "email":
            result = self._send_email(
                target=target,
                message=message,
                subject=subject,
                thread_id=thread_id,
            )
        else:
            raise ValueError(
                "effect:communication:send-message does not support "
                f"communication target provider {target.provider!r}"
            )

        output = dict(result)
        output.update(
            {
                "success": True,
                "provider": target.provider,
                "communication_target_id": target.id,
            }
        )
        return output

    def _send_email(
        self,
        *,
        target: CommunicationTargetDefinition,
        message: str,
        subject: str | None,
        thread_id: str | None,
    ) -> dict[str, Any]:
        if not bool(getattr(self._resolved_config(), "email_send_enabled", False)):
            raise ValueError("email send is disabled for this directory")
        mailbox_id = self._email_mailbox_id(target)
        service = self._email_service()
        draft = service.create_draft(
            mailbox_id,
            thread_id,
            subject,
            message,
            [target.address],
        )
        send_result = service.send_draft(mailbox_id, draft.draft_id)
        return {
            **_email_draft_output(draft),
            **send_result.model_dump(mode="json"),
        }


def _require_target_capability(target: CommunicationTargetDefinition, capability: str) -> None:
    if capability not in set(target.policy.capabilities):
        if capability == "notify":
            raise ValueError(f"communication target {target.id!r} does not allow notify capability")
        raise ValueError(
            f"communication target {target.id!r} does not allow {capability} capability"
        )


def _discord_channel_id(address: str) -> str:
    return str(address).strip().split("/")[-1]


def _email_mailbox_id_from_auth_profile_ref(auth_profile_ref: str) -> str:
    normalized = str(auth_profile_ref or "").strip()
    if normalized.startswith("email."):
        normalized = normalized.removeprefix("email.")
    if not normalized:
        raise ValueError("email policy.auth_profile_ref must identify a mailbox")
    return normalized


def _email_draft_output(draft: EmailDraft) -> dict[str, Any]:
    return {
        "status": "draft_created",
        "draft_id": draft.draft_id,
        "mailbox_id": draft.mailbox_id,
        "email_provider": draft.provider,
        "recipients": list(draft.recipients),
        "subject": draft.subject,
    }
