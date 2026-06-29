from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_sandbox.admin.payloads import PublishedArtifactListPayload
from agent_sandbox.artifact_spaces import (
    ArtifactSpacePermissionError,
    ArtifactSpaceStore,
    normalize_space_path,
)
from agent_sandbox.published_artifacts import (
    PublishedArtifact,
    PublishedArtifactMetadata,
    PublishedArtifactStore,
    validate_artifact_key,
)


@dataclass(frozen=True)
class ArtifactAdminDependencies:
    runtime_dir: Path


class ArtifactAdminService:
    """Published artifact and artifact-space operations for the admin runtime."""

    def __init__(self, dependencies: ArtifactAdminDependencies) -> None:
        self._runtime_dir = dependencies.runtime_dir

    def published_artifact_store(self) -> PublishedArtifactStore:
        return PublishedArtifactStore(self._runtime_dir)

    def list_published_artifacts(
        self,
        *,
        key: str | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PublishedArtifactListPayload:
        normalized_limit = max(1, min(int(limit or 50), 200))
        try:
            offset = int(str(cursor or "0").strip() or "0")
        except ValueError as exc:
            raise ValueError("cursor must be a non-negative integer offset") from exc
        if offset < 0:
            raise ValueError("cursor must be a non-negative integer offset")
        records = self.published_artifact_store().list_metadata(
            key=key,
            published_after=published_after,
            published_before=published_before,
        )
        page = records[offset : offset + normalized_limit]
        next_offset = offset + normalized_limit
        return PublishedArtifactListPayload(
            items=page,
            limit=normalized_limit,
            total=len(records),
            next_cursor=str(next_offset) if next_offset < len(records) else None,
        )

    def get_published_artifact(self, key: str) -> PublishedArtifactMetadata:
        return self.published_artifact_store().get(validate_artifact_key(key)).metadata

    def published_artifact_file(self, key: str) -> PublishedArtifact:
        return self.published_artifact_store().get(validate_artifact_key(key))

    def upload_published_artifact(
        self,
        *,
        key: str,
        filename: str,
        content: bytes,
        artifact_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PublishedArtifactMetadata:
        return self.published_artifact_store().publish_single_file(
            key=validate_artifact_key(key),
            session_id="manual-upload",
            filename=filename,
            content=content,
            artifact_path=artifact_path,
            metadata=metadata,
        )

    def list_artifact_spaces(
        self,
        *,
        path: str | None = None,
        recursive: bool = True,
        subject: str | None = None,
        include_versions: bool = False,
    ) -> dict[str, Any]:
        state = ArtifactSpaceStore(self._runtime_dir).read()
        root_path = normalize_space_path(path or "/")
        spaces: list[dict[str, Any]] = []
        for space in state.spaces.values():
            matches = space.path == root_path or (
                recursive and space.path.startswith(root_path.rstrip("/") + "/")
            )
            if not matches:
                continue
            if not space.grants.allows(subject, "read"):
                continue
            spaces.append(space.model_dump(mode="json"))
        spaces.sort(key=lambda item: item["path"])
        readable_paths = {space["path"] for space in spaces}
        versions = (
            [
                version.model_dump(mode="json")
                for version in state.versions.values()
                if version.space_path in readable_paths
            ]
            if include_versions
            else []
        )
        return {
            "success": True,
            "revision": state.revision,
            "spaces": spaces,
            "versions": versions,
            "count": len(spaces),
        }

    def create_artifact_space(
        self,
        *,
        path: str,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        grants: dict[str, Any] | None = None,
        subject: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        state = ArtifactSpaceStore(self._runtime_dir).ensure_space(
            path=path,
            display_name=display_name,
            metadata=metadata,
            grants=grants,
            subject=subject,
            expected_revision=expected_revision,
        )
        return {
            "success": True,
            "revision": state.revision,
            "space": state.spaces[normalize_space_path(path)].model_dump(mode="json"),
        }

    def publish_artifact_space_version(
        self,
        *,
        space_path: str,
        artifact_id: str,
        artifact_key: str,
        subject: str | None = None,
        metadata: dict[str, Any] | None = None,
        expected_revision: int | None = None,
        require_existing_artifact: bool = True,
    ) -> dict[str, Any]:
        if require_existing_artifact and not self.published_artifact_store().exists(artifact_key):
            raise FileNotFoundError(f"published artifact key does not exist: {artifact_key}")
        state = ArtifactSpaceStore(self._runtime_dir).publish_version(
            space_path=space_path,
            artifact_id=artifact_id,
            artifact_key=artifact_key,
            created_by_subject=subject,
            metadata=metadata,
            expected_revision=expected_revision,
        )
        versions = state.versions_for_space(space_path, artifact_id=artifact_id)
        return {
            "success": True,
            "revision": state.revision,
            "version": versions[-1].model_dump(mode="json"),
        }

    def latest_artifact_space_versions(
        self,
        *,
        space_path: str,
        artifact_id: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        state = ArtifactSpaceStore(self._runtime_dir).read()
        normalized_space_path = normalize_space_path(space_path)
        space = state.spaces.get(normalized_space_path)
        if space is None:
            raise FileNotFoundError(f"artifact space does not exist: {normalized_space_path}")
        if not space.grants.allows(subject, "read"):
            raise ArtifactSpacePermissionError(
                f"subject {subject or '<anonymous>'} cannot read artifact space {normalized_space_path}"
            )
        versions = [
            version.model_dump(mode="json")
            for version in state.latest_versions_for_space(
                normalized_space_path,
                artifact_id=artifact_id,
            )
        ]
        return {
            "success": True,
            "revision": state.revision,
            "versions": versions,
            "count": len(versions),
        }
