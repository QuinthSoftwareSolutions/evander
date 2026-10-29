from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from evander.models import DirectoryPath
from evander.storage import JsonObjectStore, _NoWrite, _no_write


class WorkflowStateRecord(BaseModel):
    directory_path: str = "root"
    graph_id: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value: Any = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("directory_path", mode="before")
    @classmethod
    def normalize_directory_path(cls, value: object) -> str:
        if isinstance(value, DirectoryPath):
            return value.as_posix()
        if value is None:
            return "root"
        text = str(value).strip()
        if not text:
            return "root"
        return DirectoryPath(segments=text).as_posix()

    @field_validator("graph_id", "namespace", "key", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator("version", mode="before")
    @classmethod
    def normalize_version(cls, value: object) -> int:
        version = int(value)
        if version < 1:
            raise ValueError("version must be greater than zero")
        return version


class WorkflowStateLookupResult(BaseModel):
    found: bool
    record: WorkflowStateRecord | None = None


class WorkflowStateMutationResult(WorkflowStateLookupResult):
    previous_version: int = 0
    updated: bool = False
    deleted: bool = False
    conflict: bool = False
    conflict_reason: str | None = None


class WorkflowStateRepository(Protocol):
    def get(
        self,
        directory_path: str | None,
        graph_id: str,
        namespace: str,
        key: str,
    ) -> WorkflowStateLookupResult: ...

    def set(
        self,
        directory_path: str | None,
        graph_id: str,
        namespace: str,
        key: str,
        *,
        value: Any,
        expected_version: int | None = None,
    ) -> WorkflowStateMutationResult: ...

    def patch(
        self,
        directory_path: str | None,
        graph_id: str,
        namespace: str,
        key: str,
        *,
        patch: Mapping[str, Any],
        expected_version: int | None = None,
    ) -> WorkflowStateMutationResult: ...

    def delete(
        self,
        directory_path: str | None,
        graph_id: str,
        namespace: str,
        key: str,
        *,
        expected_version: int | None = None,
    ) -> WorkflowStateMutationResult: ...


class WorkflowStateStore:
    """Atomic JSON-backed store for persisted workflow state records."""

    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.path = self.runtime_dir / "workflow-state.json"
        self._store = JsonObjectStore(
            self.path,
            {"records": {}},
            empty_file_factory=lambda: {"records": {}},
        )

    def read_raw(self) -> dict[str, Any]:
        return self._store.read_object()

    def get(
        self,
        directory_path: str | None,
        graph_id: str,
        namespace: str,
        key: str,
    ) -> WorkflowStateLookupResult:
        scope = self._normalize_scope(directory_path, graph_id, namespace, key)
        raw_record = self._get_raw_record(self._store.read_object(), scope)
        record = self._validate_record(raw_record)
        return WorkflowStateLookupResult(found=record is not None, record=record)

    def set(
        self,
        directory_path: str | None,
        graph_id: str,
        namespace: str,
        key: str,
        *,
        value: Any,
        expected_version: int | None = None,
    ) -> WorkflowStateMutationResult:
        scope = self._normalize_scope(directory_path, graph_id, namespace, key)
        expected = self._normalize_expected_version(expected_version)
        now = datetime.now(UTC)

        def update(
            payload: dict[str, Any],
        ) -> WorkflowStateMutationResult | _NoWrite[WorkflowStateMutationResult]:
            current_record = self._get_record(payload, scope)
            current_version = current_record.version if current_record is not None else 0
            if expected is not None and expected != current_version:
                return _no_write(
                    self._conflict_result(
                        found=current_record is not None,
                        previous_version=current_version,
                        current_record=current_record,
                        conflict_reason=self._expected_version_conflict_message(
                            expected=expected, current_version=current_version
                        ),
                    )
                )

            namespace_bucket = self._get_namespace_bucket(payload, scope, create=True)
            record = WorkflowStateRecord(
                directory_path=scope.directory_path,
                graph_id=scope.graph_id,
                namespace=scope.namespace,
                key=scope.key,
                value=value,
                version=current_version + 1,
                created_at=current_record.created_at if current_record is not None else now,
                updated_at=now,
            )
            namespace_bucket[scope.key] = record.model_dump(mode="json")
            return self._mutation_result(
                found=current_record is not None,
                previous_version=current_version,
                record=record,
                updated=True,
            )

        return self._store.update_object(update)

    def patch(
        self,
        directory_path: str | None,
        graph_id: str,
        namespace: str,
        key: str,
        *,
        patch: Mapping[str, Any],
        expected_version: int | None = None,
    ) -> WorkflowStateMutationResult:
        if not isinstance(patch, Mapping):
            raise ValueError("patch must be a JSON object")
        scope = self._normalize_scope(directory_path, graph_id, namespace, key)
        expected = self._normalize_expected_version(expected_version)
        patch_payload = dict(patch)
        now = datetime.now(UTC)

        def update(
            payload: dict[str, Any],
        ) -> WorkflowStateMutationResult | _NoWrite[WorkflowStateMutationResult]:
            current_record = self._get_record(payload, scope)
            current_version = current_record.version if current_record is not None else 0
            if expected is not None and expected != current_version:
                return _no_write(
                    self._conflict_result(
                        found=current_record is not None,
                        previous_version=current_version,
                        current_record=current_record,
                        conflict_reason=self._expected_version_conflict_message(
                            expected=expected, current_version=current_version
                        ),
                    )
                )

            current_value = deepcopy(current_record.value) if current_record is not None else {}
            merged_value = self._apply_merge_patch(current_value, patch_payload)
            namespace_bucket = self._get_namespace_bucket(payload, scope, create=True)
            record = WorkflowStateRecord(
                directory_path=scope.directory_path,
                graph_id=scope.graph_id,
                namespace=scope.namespace,
                key=scope.key,
                value=merged_value,
                version=current_version + 1,
                created_at=current_record.created_at if current_record is not None else now,
                updated_at=now,
            )
            namespace_bucket[scope.key] = record.model_dump(mode="json")
            return self._mutation_result(
                found=current_record is not None,
                previous_version=current_version,
                record=record,
                updated=True,
            )

        return self._store.update_object(update)

    def delete(
        self,
        directory_path: str | None,
        graph_id: str,
        namespace: str,
        key: str,
        *,
        expected_version: int | None = None,
    ) -> WorkflowStateMutationResult:
        scope = self._normalize_scope(directory_path, graph_id, namespace, key)
        expected = self._normalize_expected_version(expected_version)

        def update(
            payload: dict[str, Any],
        ) -> WorkflowStateMutationResult | _NoWrite[WorkflowStateMutationResult]:
            current_record = self._get_record(payload, scope)
            current_version = current_record.version if current_record is not None else 0
            if expected is not None and expected != current_version:
                return _no_write(
                    self._conflict_result(
                        found=current_record is not None,
                        previous_version=current_version,
                        current_record=current_record,
                        conflict_reason=self._expected_version_conflict_message(
                            expected=expected, current_version=current_version
                        ),
                    )
                )

            if current_record is None:
                return _no_write(
                    self._mutation_result(
                        found=False,
                        previous_version=0,
                        record=None,
                        deleted=False,
                    )
                )

            namespace_bucket = self._get_namespace_bucket(payload, scope, create=False)
            if namespace_bucket is not None:
                namespace_bucket.pop(scope.key, None)
            return self._mutation_result(
                found=True,
                previous_version=current_version,
                record=None,
                deleted=True,
            )

        return self._store.update_object(update)

    def _normalize_scope(
        self,
        directory_path: str | None,
        graph_id: str,
        namespace: str,
        key: str,
    ) -> "_WorkflowStateScope":
        return _WorkflowStateScope(
            directory_path=self._normalize_directory_path(directory_path),
            graph_id=self._normalize_required_text(graph_id, field_name="graph_id"),
            namespace=self._normalize_required_text(namespace, field_name="namespace"),
            key=self._normalize_required_text(key, field_name="key"),
        )

    def _get_raw_record(
        self, payload: Mapping[str, Any], scope: "_WorkflowStateScope"
    ) -> dict[str, Any] | None:
        namespace_bucket = self._get_namespace_bucket(payload, scope, create=False)
        if namespace_bucket is None:
            return None
        raw_record = namespace_bucket.get(scope.key)
        return raw_record if isinstance(raw_record, dict) else None

    def _get_record(
        self, payload: Mapping[str, Any], scope: "_WorkflowStateScope"
    ) -> WorkflowStateRecord | None:
        raw_record = self._get_raw_record(payload, scope)
        return self._validate_record(raw_record)

    def _get_namespace_bucket(
        self,
        payload: MutableMapping[str, Any],
        scope: "_WorkflowStateScope",
        *,
        create: bool,
    ) -> dict[str, Any] | None:
        records = self._ensure_bucket(payload, "records", create=create)
        if records is None:
            return None
        directory_bucket = self._ensure_bucket(records, scope.directory_path, create=create)
        if directory_bucket is None:
            return None
        graph_bucket = self._ensure_bucket(directory_bucket, scope.graph_id, create=create)
        if graph_bucket is None:
            return None
        namespace_bucket = self._ensure_bucket(graph_bucket, scope.namespace, create=create)
        if namespace_bucket is None:
            return None
        return namespace_bucket

    def _ensure_bucket(
        self,
        container: MutableMapping[str, Any],
        key: str,
        *,
        create: bool,
    ) -> dict[str, Any] | None:
        value = container.get(key)
        if isinstance(value, dict):
            return value
        if not create:
            return None
        bucket: dict[str, Any] = {}
        container[key] = bucket
        return bucket

    def _validate_record(self, raw_record: object) -> WorkflowStateRecord | None:
        if not isinstance(raw_record, dict):
            return None
        try:
            return WorkflowStateRecord.model_validate(raw_record)
        except Exception:
            return None

    def _mutation_result(
        self,
        *,
        found: bool,
        previous_version: int,
        record: WorkflowStateRecord | None,
        updated: bool = False,
        deleted: bool = False,
    ) -> WorkflowStateMutationResult:
        return WorkflowStateMutationResult(
            found=found,
            record=record,
            previous_version=previous_version,
            updated=updated,
            deleted=deleted,
            conflict=False,
            conflict_reason=None,
        )

    def _conflict_result(
        self,
        *,
        found: bool,
        previous_version: int,
        current_record: WorkflowStateRecord | None,
        conflict_reason: str,
    ) -> WorkflowStateMutationResult:
        return WorkflowStateMutationResult(
            found=found,
            record=current_record,
            previous_version=previous_version,
            updated=False,
            deleted=False,
            conflict=True,
            conflict_reason=conflict_reason,
        )

    def _apply_merge_patch(self, value: Any, patch: Mapping[str, Any]) -> Any:
        if not isinstance(patch, Mapping):
            raise ValueError("patch must be a JSON object")
        target = deepcopy(value) if isinstance(value, dict) else {}
        for key, patch_value in patch.items():
            normalized_key = str(key)
            if patch_value is None:
                target.pop(normalized_key, None)
                continue
            if isinstance(patch_value, Mapping) and isinstance(target.get(normalized_key), dict):
                target[normalized_key] = self._apply_merge_patch(
                    target[normalized_key], patch_value
                )
                continue
            if isinstance(patch_value, Mapping):
                target[normalized_key] = self._apply_merge_patch({}, patch_value)
                continue
            target[normalized_key] = deepcopy(patch_value)
        return target

    def _normalize_directory_path(self, value: object | None) -> str:
        if isinstance(value, DirectoryPath):
            return value.as_posix()
        if value is None:
            return "root"
        text = str(value).strip()
        if not text:
            return "root"
        return DirectoryPath(segments=text).as_posix()

    def _normalize_required_text(self, value: object, *, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} must be a non-empty string")
        return text

    def _normalize_expected_version(self, value: int | None) -> int | None:
        if value is None:
            return None
        version = int(value)
        if version < 0:
            raise ValueError("expected_version must be greater than or equal to zero")
        return version

    def _expected_version_conflict_message(self, *, expected: int, current_version: int) -> str:
        if current_version == 0:
            return f"expected version {expected} but the record is missing"
        return f"expected version {expected} but current version is {current_version}"


class _WorkflowStateScope(BaseModel):
    directory_path: str
    graph_id: str
    namespace: str
    key: str


__all__ = [
    "WorkflowStateLookupResult",
    "WorkflowStateMutationResult",
    "WorkflowStateRecord",
    "WorkflowStateRepository",
    "WorkflowStateStore",
]
