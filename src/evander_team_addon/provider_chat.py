"""Provider chat."""

from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from evander.admin.web.rbac import (
    scope_matches,
)
from evander.effects.providers.base import (
    EffectProviderBase,
    scoped_effect_directory_path,
)
from evander.events.models import (
    WorkflowNodeEnvelope,
)
from evander.messaging.discord_inbox_store import (
    DiscordInboxStore,
)
from evander.messaging.targets import (
    load_communication_targets_config,
)
from evander.services import (
    parse_teams_target,
)

from .chat_attachments import (
    TeamChatAttachmentPolicyService,
    TeamChatAttachmentStorage,
)
from .external_chat_attachments import (
    ExternalAttachmentImportFailure,
    ExternalAttachmentImportResult,
    ExternalTeamChatAttachmentImporter,
)
from .models import (
    TeamState,
)
from .operator_chat_steering import (
    queue_operator_chat_steering,
)
from .provider_events import (
    _materialize,
    _publish_team_events,
)
from .provider_runtime import (
    _session_records_by_id,
    _session_repository_from_context,
)
from .provider_schemas import (
    TeamEnqueueOperatorChatMessageEffectDefinition,
    TeamRecordExternalChatDeliveryEffectDefinition,
    TeamRecordOperatorEscalationDeliveryEffectDefinition,
)
from .provider_values import (
    _definition_payload,
    _optional_bool,
    _optional_text,
    _required_text,
    _runtime_dir,
)
from .repository import (
    TeamStateRepository,
)
from .services.chat_service import (
    TeamChatService,
    project_scoped_team_chat_authorization,
    reply_only_team_chat_authorization,
)
from .services.domain.chat import (
    CHAT_MESSAGE_QUEUED_EVENT_TYPE,
)
from .services.domain.operator_escalation import (
    record_operator_escalation_delivery,
)


class TeamEnqueueOperatorChatMessageEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamEnqueueOperatorChatMessageEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "conversation_id",
            "agent_id",
            "operator_user_id",
            "operator_display_name",
            "body",
            "provider",
            "communication_target_id",
            "source_message_id",
            "source_thread_id",
            "source_author_id",
            "source_author_name",
            "import_attachments",
            "allow_team_mutations",
            "authorized_author_ids",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        context = self.runtime_context
        if context is None:  # pragma: no cover - EffectProviderBase guard
            raise RuntimeError("effect:team:enqueue-operator-chat-message requires runtime context")
        provider = _required_text(payload.get("provider"), "provider").lower()
        target_id = _required_text(
            payload.get("communication_target_id"),
            "communication_target_id",
        ).lower()
        directory_path = scoped_effect_directory_path(context)
        target = _resolve_bridge_communication_target(
            context,
            directory_path=directory_path,
            communication_target_id=target_id,
        )
        if target.provider != provider:
            raise ValueError(
                f"communication target {target.id!r} uses provider {target.provider!r}, "
                f"not {provider!r}"
            )
        if "read" not in set(target.policy.capabilities):
            raise ValueError(f"communication target {target.id!r} does not allow read capability")
        source_thread_id = _optional_text(payload.get("source_thread_id"))
        if provider == "teams" and parse_teams_target(target.address).kind != "channel":
            source_thread_id = None
        if provider == "discord" and source_thread_id is not None:
            if target.policy.discord_conversation_scope not in {
                "public_threads",
                "channel_and_public_threads",
            }:
                raise ValueError(
                    f"communication target {target.id!r} does not allow Discord public threads"
                )
            binding = DiscordInboxStore(
                _runtime_dir(context) / "discord-inbox-store.db"
            ).get_thread_binding(source_thread_id)
            parent_channel_id = str(target.address).strip().split("/")[-1]
            if binding is None or binding.parent_channel_id != parent_channel_id:
                raise ValueError(
                    "Discord source_thread_id is not registered under the selected communication target"
                )
        outbound_capability = "reply" if source_thread_id is not None else "write"
        if outbound_capability not in set(target.policy.capabilities):
            raise ValueError(
                f"communication target {target.id!r} does not allow "
                f"{outbound_capability} capability required for the Team Chat reply"
            )

        source_author_id = _required_text(payload.get("source_author_id"), "source_author_id")
        import_attachments = _optional_bool(
            payload.get("import_attachments"),
            default=False,
        )
        if import_attachments and provider not in {"teams", "discord", "email"}:
            raise ValueError(
                "external Team Chat attachment import supports teams, discord, and email"
            )
        allow_team_mutations = _optional_bool(
            payload.get("allow_team_mutations"),
            default=False,
        )
        authorized_author_ids = _bridge_author_ids(payload.get("authorized_author_ids"))
        if allow_team_mutations:
            if not authorized_author_ids:
                raise ValueError(
                    "authorized_author_ids is required when allow_team_mutations is enabled"
                )
            if source_author_id not in authorized_author_ids:
                raise ValueError(
                    "source_author_id is not authorized for Team mutations on this bridge"
                )
        repo = TeamStateRepository(_runtime_dir(context))
        before = repo.read()
        conversation_id = _optional_text(payload.get("conversation_id"))
        agent_id = _optional_text(payload.get("agent_id"))
        duplicate_ingress = next(
            (
                record
                for record in before.external_chat_ingress.values()
                if record.provider == provider
                and record.communication_target_id == target.id
                and record.source_message_id
                == _required_text(payload.get("source_message_id"), "source_message_id")
            ),
            None,
        )
        if duplicate_ingress is not None:
            mutation_authorization = reply_only_team_chat_authorization(
                source="external_bridge",
                principal_id=source_author_id,
                reason="idempotent_replay_uses_existing_authorization",
            )
        else:
            if conversation_id is not None:
                conversation = before.conversations.get(conversation_id)
                if conversation is None:
                    raise ValueError(f"conversation {conversation_id} not found")
                agent_id = conversation.agent_id
            agent = before.agents.get(agent_id or "")
            if agent is None:
                raise ValueError(f"agent {agent_id or ''} not found")
        session = None
        recipient_directory_path = None
        if duplicate_ingress is None:
            session_repository = _session_repository_from_context(context)
            get_session = getattr(session_repository, "get_session", None)
            try:
                session = get_session(agent.session_id) if callable(get_session) else None
            except Exception:
                session = None
            if session is None:
                session = (_session_records_by_id(session_repository) or {}).get(agent.session_id)
            recipient_directory_path = _optional_text(getattr(session, "directory_path", None))
        if duplicate_ingress is None and allow_team_mutations:
            if recipient_directory_path is None:
                raise ValueError(
                    "mutation-enabled external Team chat requires a resolvable recipient directory"
                )
            if not scope_matches(directory_path, recipient_directory_path):
                raise ValueError(
                    "external Team chat recipient is outside the workflow directory scope"
                )
            mutation_authorization = project_scoped_team_chat_authorization(
                before,
                agent=agent,
                source="external_bridge",
                principal_id=source_author_id,
                recipient_directory_path=recipient_directory_path,
                project_is_authorized=lambda project_directory_path: scope_matches(
                    directory_path,
                    project_directory_path,
                ),
            )
            if mutation_authorization.mode != "project_scoped":
                raise ValueError("mutation-enabled external Team chat has no authorized projects")
        elif duplicate_ingress is None:
            mutation_authorization = reply_only_team_chat_authorization(
                source="external_bridge",
                principal_id=source_author_id,
                reason="mutations_disabled_by_bridge",
            )
        attachment_import = ExternalAttachmentImportResult(status="not_requested")
        prepared_attachments = []
        if duplicate_ingress is None and import_attachments:
            runtime = getattr(context.admin_service, "runtime", None)
            if runtime is None:
                attachment_import = ExternalAttachmentImportResult(
                    status="failed",
                    failures=[
                        ExternalAttachmentImportFailure(
                            code="provider_runtime_unavailable",
                            message="external attachment provider runtime is unavailable",
                        )
                    ],
                )
            elif recipient_directory_path is None:
                attachment_import = ExternalAttachmentImportResult(
                    status="failed",
                    failures=[
                        ExternalAttachmentImportFailure(
                            code="recipient_directory_unavailable",
                            message="recipient directory could not be resolved",
                        )
                    ],
                )
            else:
                policy = TeamChatAttachmentPolicyService(repo).effective_policy(
                    recipient_directory_path,
                    state=before,
                )
                attachment_import = ExternalTeamChatAttachmentImporter(
                    runtime,
                    TeamChatAttachmentStorage(repo.runtime_dir),
                ).import_message_attachments(
                    source_directory_path=directory_path,
                    provider=provider,
                    communication_target_id=target.id,
                    target_address=target.address,
                    source_message_id=_required_text(
                        payload.get("source_message_id"),
                        "source_message_id",
                    ),
                    source_thread_id=source_thread_id,
                    policy=policy,
                    auth_profile_ref=target.policy.auth_profile_ref,
                )
            prepared_attachments = list(attachment_import.attachments)
        body = _optional_text(payload.get("body"))
        if body is None and not prepared_attachments and attachment_import.failures:
            body = "External message attachments could not be imported."
        storage = TeamChatAttachmentStorage(repo.runtime_dir)
        try:
            state, result = TeamChatService(repo).enqueue_external_operator_chat_message(
                conversation_id=conversation_id,
                agent_id=agent_id,
                operator_user_id=_required_text(
                    payload.get("operator_user_id"), "operator_user_id"
                ),
                operator_display_name=_optional_text(payload.get("operator_display_name")),
                body=body,
                attachments=prepared_attachments,
                attachment_import=attachment_import.payload(),
                provider=provider,
                communication_target_id=target.id,
                source_message_id=_required_text(
                    payload.get("source_message_id"), "source_message_id"
                ),
                source_thread_id=source_thread_id,
                source_author_id=source_author_id,
                source_author_name=_optional_text(payload.get("source_author_name")),
                mutation_authorization=mutation_authorization,
            )
        except Exception:
            storage.delete_prepared(item.id for item in prepared_attachments)
            raise
        if result.get("duplicate") and prepared_attachments:
            storage.delete_prepared(item.id for item in prepared_attachments)
        if not result.get("duplicate"):
            try:
                state, _message, steering = queue_operator_chat_steering(
                    repo,
                    message_id=result["message_id"],
                )
            except Exception:
                steering = None
            if steering is not None:
                result["delivery"] = "next_model_call"
            conversation = state.conversations[result["conversation_id"]]
            agent = state.agents[conversation.agent_id]
            published_event_ids = _publish_team_events(
                context,
                [
                    {
                        "event_type": CHAT_MESSAGE_QUEUED_EVENT_TYPE,
                        "correlation_key": result["dispatch_id"],
                        "idempotency_key": (
                            f"team-addon:{CHAT_MESSAGE_QUEUED_EVENT_TYPE}:"
                            f"{provider}:{target.id}:"
                            f"{_required_text(payload.get('source_message_id'), 'source_message_id')}"
                        ),
                        "payload": {
                            "team_id": agent.team_id,
                            "agent_id": agent.id,
                            "session_id": agent.session_id,
                            "conversation_id": conversation.id,
                            "message_id": result["message_id"],
                            "dispatch_id": result["dispatch_id"],
                        },
                    }
                ],
            )
            if published_event_ids:
                result["published_event_ids"] = published_event_ids
        return {**result, "revision": state.revision}


class TeamRecordExternalChatDeliveryEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRecordExternalChatDeliveryEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "delivery_id",
            "success",
            "provider_message_id",
            "error",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        state, delivery = TeamChatService(
            TeamStateRepository(_runtime_dir(self.runtime_context))
        ).record_external_chat_delivery(
            delivery_id=_required_text(payload.get("delivery_id"), "delivery_id"),
            success=_optional_bool(payload.get("success"), default=False),
            provider_message_id=_optional_text(payload.get("provider_message_id")),
            error=_optional_text(payload.get("error")),
        )
        return {
            "success": delivery.status == "sent",
            "revision": state.revision,
            "delivery_id": delivery.id,
            "status": delivery.status,
            "attempt_count": delivery.attempt_count,
            "provider_message_id": delivery.provider_message_id,
            "error": delivery.last_error,
        }


class TeamRecordOperatorEscalationDeliveryEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(
                TeamRecordOperatorEscalationDeliveryEffectDefinition,
                effect_definition,
            )
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "escalation_id",
            "success",
            "provider_message_id",
            "error",
            "fallback_used",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        delivery: dict[str, Any] = {}

        def _mutate(state: TeamState) -> TeamState:
            nonlocal delivery
            delivery = record_operator_escalation_delivery(
                state,
                escalation_id=_required_text(payload.get("escalation_id"), "escalation_id"),
                success=_optional_bool(payload.get("success"), default=False),
                provider_message_id=_optional_text(payload.get("provider_message_id")),
                error=_optional_text(payload.get("error")),
                fallback_used=_optional_bool(payload.get("fallback_used"), default=False),
            )
            return state

        state = TeamStateRepository(_runtime_dir(self.runtime_context)).update(_mutate)
        return {
            "success": delivery["status"] == "delivered",
            "revision": state.revision,
            **delivery,
        }


def _resolve_bridge_communication_target(
    context: Any,
    *,
    directory_path: str,
    communication_target_id: str,
) -> Any:
    admin_service = getattr(context, "admin_service", None)
    runtime = getattr(admin_service, "runtime", None)
    directory_store = getattr(runtime, "directories", None)
    if directory_store is None:
        directory_store = getattr(context, "metadata", {}).get("directory_store")
    if directory_store is None:
        raise RuntimeError(
            "effect:team:enqueue-operator-chat-message requires directory configuration"
        )
    targets = load_communication_targets_config(
        runtime_dir=_runtime_dir(context),
        directory_store=directory_store,
        directory_path=directory_path,
    ).by_id()
    target = targets.get(communication_target_id)
    if target is None:
        raise ValueError(
            f"communication target {communication_target_id!r} is not configured for "
            f"directory {directory_path!r}"
        )
    return target


def _bridge_author_ids(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise ValueError("authorized_author_ids must be a list of provider author IDs")
    return {str(item).strip() for item in value if str(item).strip()}
