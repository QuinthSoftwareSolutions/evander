"""Api requests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import (
    AsyncIterator,
    Mapping,
    Sequence,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from typing import (
    Any,
)

from pydantic import (
    ValidationError,
)
from starlette.concurrency import (
    run_in_threadpool,
)
from starlette.requests import (
    Request,
)

from evander.addons import (
    AddonContext,
)
from evander.admin.web.shared import (
    request_has_privileged_operations,
)
from evander.admin.web.user_store import (
    normalize_admin_web_login,
)
from evander.events.internal import (
    InternalEventSource,
)
from evander.orchestration.internal_events import (
    build_internal_event_bus_for_runtime,
    build_scheduled_internal_event_store_for_runtime,
)

from .api_constants import (
    DEFAULT_CHAT_MESSAGE_LIMIT,
    DEFAULT_STATE_HISTORY_LIMIT,
    MAX_CHAT_MESSAGE_LIMIT,
    MAX_STATE_HISTORY_LIMIT,
    TEAM_VIEW_IDS,
    JSONResponse,
)
from .models import (
    ProjectGitConfig,
    ProjectReleasePolicy,
    TeamState,
)
from .outbox_publication import (
    request_team_outbox_publication as _request_team_outbox_publication,
)
from .repository import (
    TeamStateConflictError,
)
from .services.domain.case_task import (
    TaskGitBaseValidationError,
)
from .services.domain.release_packages import (
    ReleasePackageValidationError,
)
from .sqlite_store import (
    TeamIdempotencyConflictError,
)


def _with_errors(handler):
    async def _wrapped(request: Request) -> JSONResponse:
        try:
            return await handler(request)
        except TeamStateConflictError as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "expected_revision": exc.expected_revision,
                    "current_revision": exc.current_revision,
                },
                status_code=409,
            )
        except TeamIdempotencyConflictError as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "request_id": exc.request_id,
                    "existing_digest": exc.existing_digest,
                    "attempted_digest": exc.attempted_digest,
                },
                status_code=409,
            )
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except TaskGitBaseValidationError as exc:
            return JSONResponse(
                {"error": exc.message, "code": exc.code},
                status_code=400,
            )
        except ReleasePackageValidationError as exc:
            return JSONResponse(
                {"error": exc.message, "code": exc.code},
                status_code=400,
            )
        except (ValueError, TypeError, ValidationError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return _wrapped


def _route_requires_privileged_operations(methods: Sequence[str]) -> bool:
    return any(str(method).upper() in {"DELETE", "PATCH", "POST", "PUT"} for method in methods)


def _with_privileged_operations(context: AddonContext, handler):
    async def _wrapped(request: Request) -> JSONResponse:
        if locked_response := _privileged_operations_locked_response(context, request):
            return locked_response
        return await handler(request)

    return _wrapped


def _request_has_unlocked_privileged_operations(context: AddonContext, request: Request) -> bool:
    auth = getattr(context, "auth", None)
    if auth is None:
        return False
    return request_has_privileged_operations(
        auth=auth,
        service=getattr(context, "service", None),
        request=request,
    )


def _privileged_operations_locked_response(
    context: AddonContext, request: Request
) -> JSONResponse | None:
    if not _request_has_unlocked_privileged_operations(context, request):
        return JSONResponse({"error": "Privileged operations are locked."}, status_code=403)
    return None


def _request_wants_view_scoped_response(request: Request) -> bool:
    scope = str(request.headers.get("X-Team-Addon-Response-Scope") or "").strip().casefold()
    if scope in {"view", "scoped"}:
        return True
    query_scope = str(request.query_params.get("response_scope") or "").strip().casefold()
    return query_scope in {"view", "scoped"}


def _mutation_view_response_payload(
    state: TeamState,
    *,
    rebased_from_revision: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "response_scope": "view",
        "revision": state.revision,
        "invalidated_views": list(TEAM_VIEW_IDS),
        "resource_tokens": {},
    }
    if rebased_from_revision is not None:
        payload["rebased_from_revision"] = rebased_from_revision
    return payload


def _state_revision_payload(revision: int) -> dict[str, Any]:
    return {
        "response_scope": "revision",
        "revision": revision,
    }


def _session_repository_from_context(context: AddonContext) -> Any | None:
    service = getattr(context, "service", None)
    runtime = getattr(service, "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    if repository is not None:
        return repository
    return getattr(service, "session_repository", None)


def _agent_execution_records_by_session(service: Any | None) -> dict[str, Any] | None:
    list_agents = getattr(service, "list_agents", None)
    if not callable(list_agents):
        return None
    try:
        records = list_agents(
            include_inactive=True,
            direct_only=False,
            all_directories=True,
            reconcile_containers=False,
            omit_vm_state=True,
            summary_only=True,
            backend="container_worker_vm",
        )
    except Exception:
        return None
    by_session: dict[str, Any] = {}
    for record in records:
        session_id = str(getattr(record, "session_id", "") or "").strip()
        if session_id:
            by_session[session_id] = record
    return by_session


def _runtime_from_context(context: AddonContext) -> Any:
    service = getattr(context, "service", None)
    runtime = getattr(service, "runtime", None)
    if runtime is None and service is not None and hasattr(service, "voice_session_store"):
        runtime = service
    if runtime is None:
        raise RuntimeError("Admin runtime is unavailable")
    return runtime


def _operator_display_name(
    state: TeamState,
    operator_user_id: str,
    *,
    fallback: str,
) -> str:
    for conversation in sorted(
        state.conversations.values(),
        key=lambda item: (item.last_interaction_at, item.id),
        reverse=True,
    ):
        if conversation.operator_user_id == operator_user_id and conversation.operator_display_name:
            return conversation.operator_display_name
    return fallback


def _operator_identity_from_request(auth: Any | None, request: Request) -> dict[str, str]:
    session = _session_from_request(auth, request)
    raw_login = None
    for key in ("login", "preferred_username", "username", "sub", "name"):
        value = str((session or {}).get(key) or "").strip()
        if value:
            raw_login = value
            break
    user_id = _normalize_operator_user_id(raw_login or "admin")
    display_name = str((session or {}).get("name") or raw_login or user_id).strip() or user_id
    return {
        "user_id": user_id,
        "display_name": display_name,
        "actor": f"operator:{user_id}",
    }


def _auth_can(
    auth: Any | None,
    request: Request,
    permission: str,
    *,
    directory_path: str,
) -> bool:
    can = getattr(auth, "can", None)
    if not callable(can):
        return False
    try:
        return bool(can(request, permission, directory_path=directory_path))
    except Exception:
        return False


def _session_from_request(auth: Any | None, request: Request) -> Mapping[str, Any] | None:
    for method_name in ("read_session", "require"):
        method = getattr(auth, method_name, None)
        if not callable(method):
            continue
        raw_session = method(request)
        if isinstance(raw_session, Mapping):
            return raw_session
    return None


def _normalize_operator_user_id(value: object | None) -> str:
    normalized = normalize_admin_web_login(value)
    if normalized is not None:
        return normalized
    text = str(value or "").strip().casefold()
    return text or "admin"


async def _state_snapshot_events(
    snapshot_factory: Any,
    request: Request,
    *,
    poll_interval_seconds: float = 1.0,
) -> AsyncIterator[str]:
    last_serialized = json.dumps(await run_in_threadpool(snapshot_factory), sort_keys=True)
    yield f"event: snapshot\ndata: {last_serialized}\n\n"
    while True:
        if await request.is_disconnected():
            break
        await asyncio.sleep(max(0.0, poll_interval_seconds))
        serialized = json.dumps(await run_in_threadpool(snapshot_factory), sort_keys=True)
        if serialized == last_serialized:
            yield ": keepalive\n\n"
            continue
        last_serialized = serialized
        yield f"event: snapshot\ndata: {serialized}\n\n"


def _agent_display_name(agent: Any) -> str:
    return str(getattr(agent, "display_name", None) or getattr(agent, "id", "")).strip()


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _datetime_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _limit_text(value: Any, max_length: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def _publish_team_events(context: AddonContext, events: object) -> list[str]:
    if not isinstance(events, list):
        return []
    bus = build_internal_event_bus_for_runtime(
        context.runtime_dir,
        project_dir=context.project_dir,
        base_config=context.config,
    )
    scheduled_store = build_scheduled_internal_event_store_for_runtime(
        context.runtime_dir,
        project_dir=context.project_dir,
        base_config=context.config,
    )
    published_ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        run_at = _optional_event_datetime(event.get("run_at"))
        delay_seconds = _optional_event_float(event.get("delay_seconds"))
        if run_at is not None or (delay_seconds is not None and delay_seconds > 0):
            scheduled, _created = scheduled_store.schedule_event(
                event_type=_required_event_text(event.get("event_type"), "event_type"),
                payload=dict(payload),
                correlation_key=_optional_event_text(event.get("correlation_key")),
                run_at=(
                    run_at
                    if run_at is not None
                    else datetime.now(UTC) + timedelta(seconds=delay_seconds or 0.0)
                ),
                idempotency_key=_optional_event_text(event.get("idempotency_key")),
                source=InternalEventSource(
                    source_type="team-addon",
                    source_name="team-addon",
                ),
            )
            published_ids.append(scheduled.schedule_id)
            continue
        published = bus.publish(
            event_type=_required_event_text(event.get("event_type"), "event_type"),
            payload=dict(payload),
            correlation_key=_optional_event_text(event.get("correlation_key")),
            idempotency_key=_optional_event_text(event.get("idempotency_key")),
            source=InternalEventSource(source_type="team-addon", source_name="team-addon"),
        )
        published_ids.append(published.event_id)
    return published_ids


def _idle_context_rotation_outbox_publication_extra(
    context: AddonContext,
    *,
    state: TeamState,
    agent_id: str,
) -> dict[str, object] | None:
    runtime = state.agent_runtimes.get(agent_id)
    if runtime is None or runtime.context_idle_rotation_due_at is None:
        return None
    due_at = runtime.context_idle_rotation_due_at
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    due_key = int(due_at.astimezone(UTC).timestamp() * 1_000_000)
    return {
        "outbox_publication_wake": _request_team_outbox_publication(
            context,
            correlation_key=f"idle-context-rotation:{agent_id}:{due_key}",
        )
    }


def _required_event_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_event_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_event_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("number value is required")
    return float(value)


def _optional_event_datetime(value: object) -> datetime | None:
    text = _optional_event_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("run_at must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _required_iso_datetime(
    payload: Mapping[str, Any],
    field_name: str,
) -> datetime:
    value = _optional_iso_datetime(payload, field_name)
    if value is None:
        raise ValueError(f"{field_name} is required")
    return value


def _optional_iso_datetime(
    payload: Mapping[str, Any],
    field_name: str,
) -> datetime | None:
    raw_value = payload.get(field_name)
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _read_payload(request: Request) -> Mapping[str, Any]:
    payload = await request.json()
    if not isinstance(payload, Mapping):
        raise ValueError("request body must be a JSON object")
    return payload


async def _read_optional_payload(request: Request) -> Mapping[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("request body must be a JSON object") from exc
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ValueError("request body must be a JSON object")
    return payload


def _required(payload: Mapping[str, Any], *field_names: str) -> str:
    for field_name in field_names:
        value = payload.get(field_name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    joined = " or ".join(field_names)
    raise ValueError(f"{joined} is required")


def _mutation_request_id(
    request: Request,
    payload: Mapping[str, Any],
) -> str:
    header_value = str(request.headers.get("Idempotency-Key") or "").strip()
    body_values = {
        str(payload[field_name]).strip()
        for field_name in ("request_id", "idempotency_key")
        if payload.get(field_name) is not None and str(payload[field_name]).strip()
    }
    values = body_values | ({header_value} if header_value else set())
    if not values:
        raise ValueError("request_id or Idempotency-Key is required")
    if len(values) != 1:
        raise ValueError("request_id and Idempotency-Key must match")
    return next(iter(values))


def _optional_bool(payload: Mapping[str, Any], field_name: str) -> bool | None:
    if field_name not in payload:
        return None
    value = payload.get(field_name)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _optional(payload: Mapping[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _operator_actor_from_request(auth: Any | None, request: Request) -> str:
    return _operator_identity_from_request(auth, request)["actor"]


def _optional_present_text(payload: Mapping[str, Any], field_name: str) -> str | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    return str(payload[field_name]).strip()


def _query_text(request: Request, field_name: str) -> str | None:
    query_params = getattr(request, "query_params", None)
    if query_params is None:
        return None
    value = query_params.get(field_name)
    if value is None:
        return None
    text = value.strip()
    return text or None


def _query_values(request: Request, field_name: str) -> list[str] | None:
    values = request.query_params.getlist(field_name)
    normalized: list[str] = []
    for value in values:
        for item in value.split(","):
            text = item.strip()
            if text:
                normalized.append(text)
    if not normalized:
        return None
    return list(dict.fromkeys(normalized))


def _query_bool(request: Request, field_name: str) -> bool | None:
    value = request.query_params.get(field_name)
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be true or false")


def _query_limited_int(
    request: Request,
    field_name: str,
    *,
    default: int,
    max_value: int,
) -> int:
    value = _query_text(request, field_name)
    if value is None:
        return default
    result = _coerce_int(value, field_name)
    if result < 1:
        raise ValueError(f"{field_name} must be greater than zero")
    return min(result, max_value)


def _query_non_negative_int(request: Request, field_name: str, *, default: int) -> int:
    value = _query_text(request, field_name)
    if value is None:
        return default
    result = _coerce_int(value, field_name)
    if result < 0:
        raise ValueError(f"{field_name} must be greater than or equal to zero")
    return result


def _query_state_history_limit(request: Request) -> int:
    return _query_limited_int(
        request,
        "history_limit",
        default=DEFAULT_STATE_HISTORY_LIMIT,
        max_value=MAX_STATE_HISTORY_LIMIT,
    )


def _query_chat_message_limit(request: Request) -> int:
    value = _query_text(request, "limit")
    if value is None:
        return DEFAULT_CHAT_MESSAGE_LIMIT
    limit = _coerce_int(value, "limit")
    if limit < 1:
        raise ValueError("limit must be greater than zero")
    return min(limit, MAX_CHAT_MESSAGE_LIMIT)


def _optional_git_config(payload: Mapping[str, Any]) -> ProjectGitConfig | None:
    git_payload = payload.get("git")
    if git_payload is None:
        return None
    if not isinstance(git_payload, Mapping):
        raise ValueError("git must be a JSON object")
    return ProjectGitConfig.model_validate(dict(git_payload))


def _optional_release_policy(
    payload: Mapping[str, Any],
) -> ProjectReleasePolicy | None:
    raw = payload.get("release_policy")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("release_policy must be a JSON object")
    return ProjectReleasePolicy.model_validate(dict(raw))


def _current_agent_avatar_asset_id(state: Any, agent_id: str) -> str | None:
    agent = state.agents.get(agent_id)
    if agent is None:
        return None
    avatar = getattr(agent, "avatar", None)
    return getattr(avatar, "asset_id", None)


def _expected_revision(payload: Mapping[str, Any]) -> int:
    if "expected_revision" not in payload or payload.get("expected_revision") is None:
        raise ValueError("expected_revision is required")
    return _coerce_int(payload["expected_revision"], "expected_revision")


def _required_int(payload: Mapping[str, Any], field_name: str) -> int:
    if field_name not in payload:
        raise ValueError(f"{field_name} is required")
    return _coerce_int(payload[field_name], field_name)


def _optional_int(payload: Mapping[str, Any], field_name: str) -> int | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    return _coerce_int(payload[field_name], field_name)


def _coerce_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _optional_list(payload: Mapping[str, Any], field_name: str) -> list[Any]:
    value = payload.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return value


def _optional_object(payload: Mapping[str, Any], field_name: str) -> dict[str, Any] | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    value = payload[field_name]
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _optional_string_list(payload: Mapping[str, Any], field_name: str) -> list[str] | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    value = payload[field_name]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    normalized = [str(item).strip() for item in value if str(item).strip()]
    return normalized


def _optional_mapping_list(
    payload: Mapping[str, Any],
    field_name: str,
) -> list[dict[str, Any]]:
    values = _optional_list(payload, field_name)
    normalized: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name}[{index}] must be a JSON object")
        normalized.append(dict(value))
    return normalized


def _memory_artifact_refs(payload: Mapping[str, Any]) -> list[str]:
    refs = _optional_string_list(payload, "artifact_refs") or []
    artifact_space_ref = _optional(payload, "artifact_space_ref")
    if artifact_space_ref is not None and artifact_space_ref not in refs:
        refs.append(artifact_space_ref)
    return refs
