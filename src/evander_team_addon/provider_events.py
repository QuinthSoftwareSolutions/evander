"""Publish Team outbox events and enrich cross-workflow handoff payloads."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from pathlib import (
    Path,
)
from typing import (
    Any,
)

from evander.admin.user_notifications import (
    AdminUserNotificationStore,
    normalize_admin_notification_user_id,
)
from evander.admin.web.user_store import (
    AdminWebUserStore,
)
from evander.effects.providers.base import (
    EffectProviderBase,
)
from evander.events.internal import (
    InternalEventBus,
    InternalEventSource,
)
from evander.events.models import (
    WorkflowNodeEnvelope,
)
from evander.events.scheduled import (
    ScheduledInternalEventStore,
)

from .provider_schemas import (
    TeamPublishOutboxEventsEffectDefinition,
)
from .provider_values import (
    _definition_payload,
    _optional_float,
    _optional_int,
    _optional_mapping,
    _optional_text,
    _required_text,
    _runtime_dir,
    _string_list,
    _team_datetime_utc,
)
from .repository import (
    TeamStateRepository,
)
from .services.ado_attribution import (
    release_publication_context,
)
from .services.domain.release_events import (
    TEAM_RELEASE_APPROVED_EVENT_TYPE,
)
from .services.human_review_assignment_service import (
    reconcile_current_human_review_assignments,
)


class TeamPublishOutboxEventsEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamPublishOutboxEventsEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "limit",
            "intent_types",
            "outbox_record_ids",
            "claim_ttl_seconds",
            "retry_after_seconds",
            "worker_id",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        limit = _optional_int(payload.get("limit"), default=25)
        limit = 25 if limit is None else max(0, limit)
        claim_ttl_seconds = _optional_int(payload.get("claim_ttl_seconds"), default=300)
        claim_ttl_seconds = 300 if claim_ttl_seconds is None else max(1, claim_ttl_seconds)
        retry_after_seconds = _optional_int(payload.get("retry_after_seconds"), default=60)
        retry_after_seconds = 60 if retry_after_seconds is None else max(0, retry_after_seconds)
        worker_id = _optional_text(payload.get("worker_id")) or "team-outbox-publisher"
        intent_types = _string_list(payload.get("intent_types"), "intent_types")
        raw_outbox_record_ids = payload.get("outbox_record_ids")
        outbox_record_ids = (
            None
            if raw_outbox_record_ids is None
            else _string_list(raw_outbox_record_ids, "outbox_record_ids")
        )

        return _publish_team_outbox_events(
            context=self.runtime_context,
            repo=TeamStateRepository(_runtime_dir(self.runtime_context)),
            worker_id=worker_id,
            limit=limit,
            intent_types=intent_types or None,
            outbox_record_ids=outbox_record_ids,
            claim_ttl_seconds=claim_ttl_seconds,
            retry_after_seconds=retry_after_seconds,
        )


def _publish_team_outbox_events(
    *,
    context: Any,
    repo: TeamStateRepository,
    worker_id: str,
    limit: int,
    claim_ttl_seconds: int,
    retry_after_seconds: int,
    intent_types: list[str] | None = None,
    outbox_record_ids: list[str] | None = None,
) -> dict[str, Any]:
    claimed = repo.claim_outbox_records(
        worker_id=worker_id,
        limit=limit,
        intent_types=intent_types,
        record_ids=outbox_record_ids,
        claim_ttl_seconds=claim_ttl_seconds,
    )
    published_event_ids: list[str] = []
    completed_record_ids: list[str] = []
    failed_record_ids: list[str] = []
    errors: list[dict[str, Any]] = []
    for record in claimed:
        try:
            event = dict(record.payload)
            event.setdefault("idempotency_key", record.idempotency_key)
            if record.intent_type == "team.release.human_review_requested":
                notification_result = _publish_human_review_admin_notification(
                    runtime_dir=_runtime_dir(context),
                    event=event,
                )
                if notification_result.get("skipped"):
                    repo.complete_outbox_record(
                        record_id=record.id,
                        claim_token=record.claim_token,
                    )
                    completed_record_ids.append(record.id)
                    continue
            record_event_ids = _publish_team_events(context, [event])
            if not record_event_ids:
                raise RuntimeError("outbox record did not publish an internal event")
            repo.complete_outbox_record(
                record_id=record.id,
                claim_token=record.claim_token,
            )
            completed_record_ids.append(record.id)
            published_event_ids.extend(record_event_ids)
        except Exception as exc:
            repo.fail_outbox_record(
                record_id=record.id,
                error_code="publish_failed",
                error_message=str(exc),
                claim_token=record.claim_token,
                retry_after_seconds=retry_after_seconds,
            )
            failed_record_ids.append(record.id)
            errors.append(
                {
                    "record_id": record.id,
                    "intent_type": record.intent_type,
                    "error": str(exc),
                }
            )
    return {
        "success": not errors,
        "claimed_count": len(claimed),
        "published_count": len(completed_record_ids),
        "published_event_ids": published_event_ids,
        "completed_outbox_record_ids": completed_record_ids,
        "failed_outbox_record_ids": failed_record_ids,
        "errors": errors,
    }


def _publish_human_review_admin_notification(
    *,
    runtime_dir: Path,
    event: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _optional_mapping(event.get("payload"), "payload") or {}
    request_id = _required_text(
        payload.get("human_review_request_id"),
        "human_review_request_id",
    )
    release_id = _required_text(
        payload.get("release_package_id"),
        "release_package_id",
    )
    recipient_user_id = normalize_admin_notification_user_id(payload.get("recipient_user_id"))
    if recipient_user_id is None:
        raise ValueError("recipient_user_id is required")
    reconcile_current_human_review_assignments(
        repository=TeamStateRepository(runtime_dir),
        user_store=AdminWebUserStore(runtime_dir / "admin-web.db"),
        request_ids=[request_id],
    )
    state = TeamStateRepository(runtime_dir).read()
    request = state.human_review_requests.get(request_id)
    if (
        request is None
        or request.state != "pending"
        or recipient_user_id not in request.current_assignment.eligible_user_ids
    ):
        return {
            "skipped": True,
            "reason": "human review recipient is no longer decision-eligible",
            "human_review_request_id": request_id,
            "recipient_user_id": recipient_user_id,
        }
    generation = _optional_int(payload.get("assignment_generation"), default=1)
    generation = 1 if generation is None else max(1, generation)
    candidate_sha = _optional_text(payload.get("candidate_commit_sha"))
    candidate_label = candidate_sha[:12] if candidate_sha is not None else "pending candidate"
    return AdminUserNotificationStore(runtime_dir).push(
        user_id=recipient_user_id,
        title="Human release review assigned",
        body=(f"Release {release_id} is ready for your review at candidate {candidate_label}."),
        source="team_human_review",
        source_id=f"{request_id}:{recipient_user_id}:{generation}",
        target_url=(
            f"#addon:team-addon?view=my-human-reviews&human_review_request_id={request_id}"
        ),
        metadata={
            "human_review_request_id": request_id,
            "release_package_id": release_id,
            "assignment_generation": generation,
        },
    )


def _publish_team_events(context: Any, events: Any) -> list[str]:
    if not isinstance(events, list):
        return []
    bus = _internal_event_bus(context)
    scheduled_store = _scheduled_internal_event_store(context)
    published_ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_type = _required_text(event.get("event_type"), "event_type")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        payload = _team_event_payload(context, event_type=event_type, payload=payload)
        run_at = _team_event_run_at(event.get("run_at"))
        delay_seconds = _optional_float(event.get("delay_seconds"), default=None)
        if run_at is not None or (delay_seconds is not None and delay_seconds > 0):
            scheduled, _created = scheduled_store.schedule_event(
                event_type=event_type,
                payload=dict(payload),
                correlation_key=_optional_text(event.get("correlation_key")),
                run_at=(
                    run_at
                    if run_at is not None
                    else datetime.now(UTC) + timedelta(seconds=delay_seconds or 0.0)
                ),
                idempotency_key=_optional_text(event.get("idempotency_key")),
                source=InternalEventSource(
                    source_type="team-addon",
                    source_name="team-addon",
                ),
            )
            published_ids.append(scheduled.schedule_id)
            continue
        published = bus.publish(
            event_type=event_type,
            payload=dict(payload),
            correlation_key=_optional_text(event.get("correlation_key")),
            idempotency_key=_optional_text(event.get("idempotency_key")),
            source=InternalEventSource(source_type="team-addon", source_name="team-addon"),
        )
        published_ids.append(published.event_id)
    return published_ids


def _team_event_payload(
    context: Any,
    *,
    event_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    enriched = dict(payload)
    if event_type != TEAM_RELEASE_APPROVED_EVENT_TYPE:
        return enriched
    release_package_id = _required_text(
        enriched.get("release_package_id"),
        "release_package_id",
    )
    state = TeamStateRepository(_runtime_dir(context)).read()
    enriched["publication_revision"] = state.revision
    enriched["publication_context_id"] = release_publication_context(
        state,
        release_package_id=release_package_id,
        managed_git_admin=getattr(
            getattr(context, "admin_service", None),
            "managed_git_admin",
            None,
        ),
        commit_sha=_optional_text(enriched.get("approved_commit_sha")),
        managed_repo_id=_optional_text(enriched.get("managed_repo_id")),
        directory_path=_optional_text(enriched.get("directory_path")),
    )
    return enriched


def _team_event_run_at(value: object) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return _team_datetime_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as exc:
        raise ValueError("run_at must be an ISO 8601 datetime") from exc


def _internal_event_bus(context: Any) -> InternalEventBus:
    from evander.orchestration.internal_events import build_internal_event_bus_from_metadata

    runtime_dir = _runtime_dir(context)
    metadata = getattr(context, "metadata", {})
    return build_internal_event_bus_from_metadata(
        metadata if isinstance(metadata, Mapping) else None,
        runtime_dir=runtime_dir,
    )


def _scheduled_internal_event_store(context: Any) -> ScheduledInternalEventStore:
    from evander.orchestration.internal_events import (
        build_scheduled_internal_event_store_from_metadata,
    )

    runtime_dir = _runtime_dir(context)
    metadata = getattr(context, "metadata", {})
    return build_scheduled_internal_event_store_from_metadata(
        metadata if isinstance(metadata, Mapping) else None,
        runtime_dir=runtime_dir,
    )


def _materialize(model_class: type[Any], node: WorkflowNodeEnvelope) -> Any:
    config = dict(node.config)
    if (
        "outputs" in config
        and "result_outputs" not in config
        and "result_outputs" in getattr(model_class, "model_fields", {})
    ):
        config["result_outputs"] = config.pop("outputs")
    return model_class.model_validate(
        {
            "id": node.id,
            "display_name": node.display_name,
            "type": node.type,
            "outputs": node.outputs,
            **config,
        }
    )
