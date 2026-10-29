"""Provider artifacts."""

from __future__ import annotations

import hashlib
import re
from collections.abc import (
    Mapping,
)
from pathlib import (
    Path,
)
from typing import (
    Any,
)

from evander.artifact_spaces import (
    ArtifactSpacePermissionError,
    ArtifactSpaceStore,
    normalize_space_path,
)
from evander.effects.providers.artifacts import (
    ArtifactImportPublishedEffectProvider,
    ArtifactPublishArtifactsEffectProvider,
)
from evander.events.provider_models import (
    ArtifactImportPublishedEffectDefinition,
    ArtifactPublishArtifactsEffectDefinition,
)
from evander.published_artifacts import (
    PublishedArtifactStore,
)

from .provider_constants import (
    _ARTIFACT_SPACE_COMPONENT_MAX_BYTES,
    _ARTIFACT_SPACE_ENCODED_SEGMENT_PREFIX,
    _ARTIFACT_SPACE_REF_PREFIX,
    _ARTIFACT_SPACE_SAFE_SEGMENT_RE,
    _TEAM_REVIEW_ARTIFACT_ID_DEFAULT,
)
from .provider_projection import (
    _string_items,
)
from .provider_values import (
    _optional_text,
    _required_text,
    _runtime_dir,
)


def _publish_deliverable_artifact_bundle(
    context: Any,
    *,
    agent: Mapping[str, Any],
    team_id: str,
    project_id: str,
    deliverable_id: str,
    dispatch_id: str,
    source_paths: list[str],
    evidence_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_dir = _runtime_dir(context)
    safe_team_id = _artifact_space_path_segment(team_id, "team_id")
    safe_project_id = _artifact_space_path_segment(project_id, "project_id")
    safe_deliverable_id = _artifact_space_path_segment(deliverable_id, "deliverable_id")
    safe_dispatch_id = _artifact_space_path_segment(dispatch_id, "dispatch_id")
    evidence_segment = "evidence/" if evidence_metadata is not None else ""
    artifact_key = (
        f"team/{safe_team_id}/{safe_project_id}/{evidence_segment}"
        f"{safe_deliverable_id}/{safe_dispatch_id}/bundle"
    )
    record_collection = "evidence" if evidence_metadata is not None else "deliverables"
    artifact_ref_path = (
        f"/teams/{safe_team_id}/projects/{safe_project_id}/{record_collection}/{safe_deliverable_id}"
    )
    artifact_ref = f"artifact-space:{artifact_ref_path}"
    store = PublishedArtifactStore(runtime_dir)
    if store.exists(artifact_key):
        artifact = store.get(artifact_key)
        publish_result: dict[str, Any] = {
            "success": True,
            "key": artifact.metadata.key,
            "byte_count": artifact.metadata.byte_count,
            "sha256": artifact.metadata.sha256,
            "source_file_count": artifact.metadata.source_file_count,
            "source_byte_count": artifact.metadata.source_byte_count,
            "reused": True,
        }
    else:
        provider = ArtifactPublishArtifactsEffectProvider()
        provider.initialize(
            ArtifactPublishArtifactsEffectDefinition(
                id=f"team-deliverable-artifacts-{deliverable_id}",
                display_name="Publish Team deliverable artifacts",
                type="effect:artifact:publish-artifacts",
                session_id=_required_text(agent.get("session_id"), "session_id"),
                key=artifact_key,
                source_paths=source_paths,
            ),
            context,
        )
        publish_result = provider.execute()
        publish_result["reused"] = False

    version = _ensure_deliverable_artifact_space_version(
        runtime_dir,
        space_path=artifact_ref_path,
        artifact_key=artifact_key,
        team_id=team_id,
        project_id=project_id,
        deliverable_id=deliverable_id,
        agent_id=_required_text(agent.get("id"), "agent_id"),
        evidence_metadata=evidence_metadata,
    )
    return {
        ("evidence_id" if evidence_metadata is not None else "deliverable_id"): deliverable_id,
        "project_id": project_id,
        "team_id": team_id,
        "artifact_key": artifact_key,
        "artifact_ref": artifact_ref,
        "artifact_space_path": artifact_ref_path,
        "artifact_space_version_id": version.get("id"),
        "artifact_space_version": version.get("version"),
        "source_paths": source_paths,
        "publish_result": publish_result,
    }


def _publish_memory_artifact_bundle(
    context: Any,
    *,
    agent: Mapping[str, Any],
    team_id: str,
    project_id: str | None,
    memory_id: str,
    dispatch_id: str,
    source_paths: list[str],
) -> dict[str, Any]:
    runtime_dir = _runtime_dir(context)
    safe_team_id = _artifact_space_path_segment(team_id, "team_id")
    safe_memory_id = _artifact_space_path_segment(memory_id, "memory_id")
    safe_dispatch_id = _artifact_space_path_segment(dispatch_id, "dispatch_id")
    safe_project_id = (
        _artifact_space_path_segment(project_id, "project_id") if project_id is not None else None
    )
    project_segment = f"/projects/{safe_project_id}" if safe_project_id is not None else ""
    project_key_segment = f"/{safe_project_id}" if safe_project_id is not None else ""
    artifact_key = (
        f"team/{safe_team_id}{project_key_segment}/memory/{safe_memory_id}/"
        f"{safe_dispatch_id}/bundle"
    )
    artifact_ref_path = f"/teams/{safe_team_id}{project_segment}/memory/{safe_memory_id}"
    artifact_ref = f"artifact-space:{artifact_ref_path}"
    store = PublishedArtifactStore(runtime_dir)
    if store.exists(artifact_key):
        artifact = store.get(artifact_key)
        publish_result: dict[str, Any] = {
            "success": True,
            "key": artifact.metadata.key,
            "byte_count": artifact.metadata.byte_count,
            "sha256": artifact.metadata.sha256,
            "source_file_count": artifact.metadata.source_file_count,
            "source_byte_count": artifact.metadata.source_byte_count,
            "reused": True,
        }
    else:
        provider = ArtifactPublishArtifactsEffectProvider()
        provider.initialize(
            ArtifactPublishArtifactsEffectDefinition(
                id=f"team-memory-artifacts-{memory_id}",
                display_name="Publish Team memory artifacts",
                type="effect:artifact:publish-artifacts",
                session_id=_required_text(agent.get("session_id"), "session_id"),
                key=artifact_key,
                source_paths=source_paths,
            ),
            context,
        )
        publish_result = provider.execute()
        publish_result["reused"] = False

    version = _ensure_memory_artifact_space_version(
        runtime_dir,
        space_path=artifact_ref_path,
        artifact_key=artifact_key,
        team_id=team_id,
        project_id=project_id,
        memory_id=memory_id,
        agent_id=_required_text(agent.get("id"), "agent_id"),
    )
    return {
        "memory_id": memory_id,
        "project_id": project_id,
        "team_id": team_id,
        "artifact_key": artifact_key,
        "artifact_ref": artifact_ref,
        "artifact_space_path": artifact_ref_path,
        "artifact_space_version_id": version.get("id"),
        "artifact_space_version": version.get("version"),
        "source_paths": source_paths,
        "publish_result": publish_result,
    }


def _prepare_review_artifacts_for_lease(
    context: Any,
    *,
    lease: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    imports: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    requests = _review_artifact_ref_requests(lease)
    if not requests:
        return {
            "review_artifact_imports": imports,
            "review_artifact_import_errors": errors,
        }

    try:
        artifact_state = ArtifactSpaceStore(_runtime_dir(context)).read()
    except Exception as exc:
        for request in requests:
            errors.append({**_review_artifact_request_payload(request), "error": str(exc)})
        return {
            "review_artifact_imports": imports,
            "review_artifact_import_errors": errors,
        }

    for request in requests:
        artifact_ref = _required_text(request.get("artifact_ref"), "artifact_ref")
        try:
            artifact_space_path = _artifact_space_path_from_ref(artifact_ref)
            _require_artifact_space_read(
                artifact_state,
                artifact_space_path=artifact_space_path,
                subject=_required_text(request.get("subject"), "subject"),
            )
            versions = artifact_state.latest_versions_for_space(
                artifact_space_path,
                artifact_id=_TEAM_REVIEW_ARTIFACT_ID_DEFAULT,
            )
            if not versions:
                versions = artifact_state.latest_versions_for_space(artifact_space_path)
            if not versions:
                raise ValueError(
                    f"artifact space has no latest published versions: {artifact_space_path}"
                )
        except Exception as exc:
            errors.append(
                {
                    **_review_artifact_request_payload(request),
                    "artifact_space_path": artifact_ref.removeprefix(_ARTIFACT_SPACE_REF_PREFIX),
                    "error": str(exc),
                }
            )
            continue

        for version in versions:
            try:
                imports.append(
                    _import_review_artifact_version(
                        context,
                        request=request,
                        artifact_space_path=artifact_space_path,
                        artifact_id=version.artifact_id,
                        artifact_key=version.artifact_key,
                        artifact_space_version_id=version.id,
                        artifact_space_version=version.version,
                    )
                )
            except Exception as exc:
                errors.append(
                    {
                        **_review_artifact_request_payload(request),
                        "artifact_space_path": artifact_space_path,
                        "artifact_id": version.artifact_id,
                        "artifact_key": version.artifact_key,
                        "artifact_space_version_id": version.id,
                        "artifact_space_version": version.version,
                        "error": str(exc),
                    }
                )

    return {
        "review_artifact_imports": imports,
        "review_artifact_import_errors": errors,
    }


def _review_artifact_ref_requests(lease: Mapping[str, Any]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    agent = lease.get("agent")
    if not isinstance(agent, Mapping):
        return requests
    agent_id = _optional_text(agent.get("id"))
    if agent_id is None:
        return requests
    session_id = _optional_text(agent.get("session_id"))
    if session_id is None:
        return requests
    subject = f"team-agent:{agent_id}"
    for item in lease.get("inbox") or []:
        if not isinstance(item, Mapping):
            continue
        payload = item.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if _optional_text(payload.get("kind")) != "review_request":
            continue
        review_request_id = (
            _optional_text(payload.get("review_request_id"))
            or _optional_text(item.get("id"))
            or "review"
        )
        deliverable_id = _optional_text(payload.get("deliverable_id")) or "deliverable"
        for artifact_ref in _string_items(payload.get("artifact_refs")):
            if not artifact_ref.startswith(_ARTIFACT_SPACE_REF_PREFIX):
                continue
            key = (review_request_id, deliverable_id, artifact_ref)
            if key in seen:
                continue
            seen.add(key)
            requests.append(
                {
                    "review_request_id": review_request_id,
                    "deliverable_id": deliverable_id,
                    "artifact_ref": artifact_ref,
                    "inbox_item_id": _optional_text(item.get("id")),
                    "session_id": session_id,
                    "subject": subject,
                }
            )
    return requests


def _review_artifact_request_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: request[key]
        for key in (
            "review_request_id",
            "deliverable_id",
            "artifact_ref",
            "inbox_item_id",
        )
        if request.get(key) is not None
    }


def _artifact_space_path_from_ref(artifact_ref: str) -> str:
    if not artifact_ref.startswith(_ARTIFACT_SPACE_REF_PREFIX):
        raise ValueError(f"unsupported artifact ref: {artifact_ref}")
    return normalize_space_path(artifact_ref.removeprefix(_ARTIFACT_SPACE_REF_PREFIX))


def _require_artifact_space_read(
    artifact_state: Any,
    *,
    artifact_space_path: str,
    subject: str,
) -> None:
    space = artifact_state.spaces.get(artifact_space_path)
    if space is None:
        raise ValueError(f"artifact space does not exist: {artifact_space_path}")
    if not space.grants.allows(subject, "read"):
        raise ArtifactSpacePermissionError(
            f"subject {subject or '<anonymous>'} cannot read artifact space {artifact_space_path}"
        )


def _require_team_artifact_space_read(
    artifact_state: Any,
    *,
    artifact_space_path: str,
    team_id: str,
    subject: str,
) -> str:
    space = artifact_state.spaces.get(artifact_space_path)
    if space is None:
        raise ValueError(f"artifact space does not exist: {artifact_space_path}")
    safe_team_id = _artifact_space_path_segment(team_id, "team_id")
    team_root = f"/teams/{safe_team_id}"
    if (
        _optional_text(space.metadata.get("source")) != "team_addon"
        or _optional_text(space.metadata.get("team_id")) != team_id
        or (
            artifact_space_path != team_root and not artifact_space_path.startswith(f"{team_root}/")
        )
    ):
        raise ArtifactSpacePermissionError(f"artifact space is not owned by Team {team_id}")
    _require_artifact_space_read(
        artifact_state,
        artifact_space_path=artifact_space_path,
        subject=subject,
    )
    return safe_team_id


def _import_team_artifact_for_session(
    context: Any,
    *,
    artifact_ref: str,
    artifact_id: str,
    session_id: str,
    subject: str,
    team_id: str,
) -> dict[str, Any]:
    normalized_ref = _required_text(artifact_ref, "artifact_ref")
    normalized_artifact_id = _required_text(artifact_id, "artifact_id")
    normalized_session_id = _required_text(session_id, "session_id")
    normalized_subject = _required_text(subject, "subject")
    normalized_team_id = _required_text(team_id, "team_id")
    artifact_space_path = _artifact_space_path_from_ref(normalized_ref)
    artifact_state = ArtifactSpaceStore(_runtime_dir(context)).read()
    safe_team_id = _require_team_artifact_space_read(
        artifact_state,
        artifact_space_path=artifact_space_path,
        team_id=normalized_team_id,
        subject=normalized_subject,
    )
    versions = artifact_state.latest_versions_for_space(
        artifact_space_path,
        artifact_id=normalized_artifact_id,
    )
    if not versions:
        raise ValueError(
            "artifact space has no latest published version for "
            f"artifact_id {normalized_artifact_id}: {artifact_space_path}"
        )
    version = versions[0]
    if not version.artifact_key.startswith(f"team/{safe_team_id}/"):
        raise ArtifactSpacePermissionError(
            f"artifact version is not owned by Team {normalized_team_id}"
        )
    destination_path = _team_artifact_import_destination_path(
        artifact_space_path=artifact_space_path,
        artifact_id=version.artifact_id,
        artifact_space_version_id=version.id,
    )
    effect_hash = hashlib.sha256(
        f"{normalized_session_id}\0{version.artifact_key}".encode("utf-8")
    ).hexdigest()[:24]
    provider = ArtifactImportPublishedEffectProvider()
    provider.initialize(
        ArtifactImportPublishedEffectDefinition(
            id=f"team-artifact-import-{effect_hash}",
            display_name="Import Team artifact",
            type="effect:artifact:import-published",
            session_id=normalized_session_id,
            key=version.artifact_key,
            destination_path=destination_path,
            overwrite=True,
        ),
        context,
    )
    import_result = provider.execute()
    relative_paths = [str(path) for path in import_result.get("imported_paths") or []]
    return {
        "artifact_ref": normalized_ref,
        "artifact_space_path": artifact_space_path,
        "artifact_id": version.artifact_id,
        "artifact_key": version.artifact_key,
        "artifact_space_version_id": version.id,
        "artifact_space_version": version.version,
        "destination_path": destination_path,
        "artifact_path": f"/artifacts/{destination_path}",
        "imported_paths": [f"/artifacts/{path.lstrip('/')}" for path in relative_paths],
        "imported_relative_paths": relative_paths,
        "imported_file_count": int(import_result.get("imported_file_count") or 0),
        "source_byte_count": int(import_result.get("source_byte_count") or 0),
        "archive_sha256": import_result.get("sha256"),
    }


def _team_artifact_import_destination_path(
    *,
    artifact_space_path: str,
    artifact_id: str,
    artifact_space_version_id: str,
) -> str:
    return "/".join(
        [
            "team-artifact-imports",
            _artifact_space_path_segment(artifact_id, "artifact_id"),
            _review_artifact_ref_version_segment(
                artifact_space_path=artifact_space_path,
                artifact_space_version_id=artifact_space_version_id,
            ),
        ]
    )


def _import_review_artifact_version(
    context: Any,
    *,
    request: Mapping[str, Any],
    artifact_space_path: str,
    artifact_id: str,
    artifact_key: str,
    artifact_space_version_id: str,
    artifact_space_version: int,
) -> dict[str, Any]:
    review_request_id = _required_text(
        request.get("review_request_id"),
        "review_request_id",
    )
    deliverable_id = _required_text(request.get("deliverable_id"), "deliverable_id")
    destination_path = _review_artifact_destination_path(
        review_request_id=review_request_id,
        deliverable_id=deliverable_id,
        artifact_space_path=artifact_space_path,
        artifact_id=artifact_id,
        artifact_space_version_id=artifact_space_version_id,
    )
    provider = ArtifactImportPublishedEffectProvider()
    ref_version_segment = _review_artifact_ref_version_segment(
        artifact_space_path=artifact_space_path,
        artifact_space_version_id=artifact_space_version_id,
    )
    provider.initialize(
        ArtifactImportPublishedEffectDefinition(
            id=_bounded_generated_identifier(
                "team-review-artifact-import-",
                review_request_id,
                artifact_id,
                ref_version_segment,
            ),
            display_name="Import Team review artifact",
            type="effect:artifact:import-published",
            session_id=_required_text(request.get("session_id"), "session_id"),
            key=artifact_key,
            destination_path=destination_path,
            overwrite=True,
        ),
        context,
    )
    import_result = provider.execute()
    return {
        "review_request_id": review_request_id,
        "deliverable_id": deliverable_id,
        "artifact_ref": request.get("artifact_ref"),
        "artifact_space_path": artifact_space_path,
        "artifact_id": artifact_id,
        "artifact_key": artifact_key,
        "artifact_space_version_id": artifact_space_version_id,
        "artifact_space_version": artifact_space_version,
        "destination_path": destination_path,
        "artifact_path": f"/artifacts/{destination_path}",
        "imported_paths": list(import_result.get("imported_paths") or []),
        "imported_file_count": int(import_result.get("imported_file_count") or 0),
    }


def _review_artifact_destination_path(
    *,
    review_request_id: str,
    deliverable_id: str,
    artifact_space_path: str,
    artifact_id: str,
    artifact_space_version_id: str,
) -> str:
    ref_version_segment = _review_artifact_ref_version_segment(
        artifact_space_path=artifact_space_path,
        artifact_space_version_id=artifact_space_version_id,
    )
    return "/".join(
        [
            "team-review-inputs",
            _artifact_space_path_segment(review_request_id, "review_request_id"),
            _artifact_space_path_segment(deliverable_id, "deliverable_id"),
            _artifact_space_path_segment(artifact_id, "artifact_id"),
            ref_version_segment,
        ]
    )


def _review_artifact_ref_version_segment(
    *,
    artifact_space_path: str,
    artifact_space_version_id: str,
) -> str:
    ref_hash = hashlib.sha256(artifact_space_path.encode("utf-8")).hexdigest()[:16]
    version_segment = _artifact_space_path_segment(
        artifact_space_version_id,
        "artifact_space_version_id",
    )
    candidate = f"ref-{ref_hash}-{version_segment}"
    if len(candidate.encode("utf-8")) <= _ARTIFACT_SPACE_COMPONENT_MAX_BYTES:
        return candidate
    return _bounded_generated_identifier(f"ref-{ref_hash}-", artifact_space_version_id)


def _artifact_space_path_segment(value: str, field_name: str) -> str:
    text = _required_text(value, field_name)
    if (
        _ARTIFACT_SPACE_SAFE_SEGMENT_RE.fullmatch(text)
        and text not in {".", ".."}
        and not text.startswith(_ARTIFACT_SPACE_ENCODED_SEGMENT_PREFIX)
        and len(text.encode("utf-8")) <= _ARTIFACT_SPACE_COMPONENT_MAX_BYTES
    ):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("._-") or "value"
    suffix = f"-{digest}"
    prefix = _ARTIFACT_SPACE_ENCODED_SEGMENT_PREFIX
    available = _ARTIFACT_SPACE_COMPONENT_MAX_BYTES - len((prefix + suffix).encode("ascii"))
    readable = readable.encode("utf-8")[:available].decode("utf-8", errors="ignore").rstrip("._-")
    return f"{prefix}{readable or 'value'}{suffix}"


def _bounded_generated_identifier(prefix: str, *parts: str) -> str:
    normalized_prefix = _required_text(prefix, "prefix")
    if len(normalized_prefix.encode("utf-8")) >= _ARTIFACT_SPACE_COMPONENT_MAX_BYTES:
        raise ValueError("generated identifier prefix exceeds the component byte budget")
    source = "-".join(_required_text(part, "identifier part") for part in parts)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", source).strip("._-") or "value"
    suffix = f"-{digest}"
    available = _ARTIFACT_SPACE_COMPONENT_MAX_BYTES - len(
        (normalized_prefix + suffix).encode("ascii")
    )
    readable = readable.encode("utf-8")[:available].decode("utf-8", errors="ignore").rstrip("._-")
    return f"{normalized_prefix}{readable or 'value'}{suffix}"


def _ensure_deliverable_artifact_space_version(
    runtime_dir: Path,
    *,
    space_path: str,
    artifact_key: str,
    team_id: str,
    project_id: str,
    deliverable_id: str,
    agent_id: str,
    evidence_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    subject = f"team-agent:{agent_id}"
    store = ArtifactSpaceStore(runtime_dir)
    metadata = {
        **dict(evidence_metadata or {}),
        "team_id": team_id,
        "project_id": project_id,
        ("evidence_id" if evidence_metadata is not None else "deliverable_id"): deliverable_id,
        "source": "team_addon",
    }
    state = store.ensure_space(
        path=space_path,
        display_name=(
            f"{'Evidence' if evidence_metadata is not None else 'Deliverable'} {deliverable_id}"
        ),
        metadata=metadata,
        subject=subject,
    )
    for version in state.versions_for_space(space_path, artifact_id="bundle"):
        if version.artifact_key == artifact_key:
            return version.model_dump(mode="json")
    state = store.publish_version(
        space_path=space_path,
        artifact_id="bundle",
        artifact_key=artifact_key,
        created_by_subject=subject,
        metadata=metadata,
    )
    return state.versions_for_space(space_path, artifact_id="bundle")[-1].model_dump(mode="json")


def _ensure_memory_artifact_space_version(
    runtime_dir: Path,
    *,
    space_path: str,
    artifact_key: str,
    team_id: str,
    project_id: str | None,
    memory_id: str,
    agent_id: str,
) -> dict[str, Any]:
    subject = f"team-agent:{agent_id}"
    metadata = {
        "team_id": team_id,
        "project_id": project_id,
        "memory_id": memory_id,
        "source": "team_addon",
        "record_kind": "memory",
    }
    store = ArtifactSpaceStore(runtime_dir)
    state = store.ensure_space(
        path=space_path,
        display_name=f"Memory {memory_id}",
        metadata=metadata,
        subject=subject,
    )
    for version in state.versions_for_space(space_path, artifact_id="bundle"):
        if version.artifact_key == artifact_key:
            return version.model_dump(mode="json")
    state = store.publish_version(
        space_path=space_path,
        artifact_id="bundle",
        artifact_key=artifact_key,
        created_by_subject=subject,
        metadata=metadata,
    )
    return state.versions_for_space(space_path, artifact_id="bundle")[-1].model_dump(mode="json")


def _append_unique_text(values: list[str], value: str) -> list[str]:
    return values if value in values else [*values, value]
