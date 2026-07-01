from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from evander.models import DirectoryPath

from .base import QueryProviderBase


class IssueListQueryProvider(QueryProviderBase):
    NODE_TYPE = "query:issue:list"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = self._resolve_directory_path(payload.get("directory_path"))
        direct_only = self._coerce_direct_only(payload.get("direct_only"))
        states = self._coerce_string_set(payload.get("states"))
        exclude_states = self._coerce_string_set(payload.get("exclude_states"))
        source = self._normalize_optional_string(payload.get("source"))
        source_type = self._normalize_optional_string(payload.get("source_type"))
        tags_any = self._coerce_string_set(payload.get("tags_any"))
        tags_all = self._coerce_string_set(payload.get("tags_all"))
        metadata_equals = self._coerce_mapping(payload.get("metadata_equals"), "metadata_equals")
        metadata_exists = self._coerce_exists_selectors(payload.get("metadata_exists"))
        metadata_prefix = self._coerce_mapping(payload.get("metadata_prefix"), "metadata_prefix")
        limit = self._coerce_optional_limit(payload.get("limit"))

        issues = self._list_issues(
            directory_path=directory_path if direct_only else None,
            direct_only=direct_only,
            states=states,
            exclude_states=exclude_states,
        )
        if not direct_only:
            issues = [issue for issue in issues if self._matches_directory(issue, directory_path)]

        issues = [
            issue
            for issue in issues
            if self._matches_filters(
                issue,
                source=source,
                source_type=source_type,
                tags_any=tags_any,
                tags_all=tags_all,
                metadata_equals=metadata_equals,
                metadata_exists=metadata_exists,
                metadata_prefix=metadata_prefix,
            )
        ]
        if limit is not None:
            issues = issues[:limit]

        payloads = [self._issue_payload(issue) for issue in issues]
        return {"issues": payloads, "count": len(payloads)}

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in (
            "directory_path",
            "direct_only",
            "states",
            "exclude_states",
            "source",
            "source_type",
            "tags_any",
            "tags_all",
            "metadata_equals",
            "metadata_exists",
            "metadata_prefix",
            "limit",
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _list_issues(
        self,
        *,
        directory_path: str | None,
        direct_only: bool,
        states: set[str] | None,
        exclude_states: set[str] | None,
    ) -> list[Any]:
        context = self._context()
        list_issues = getattr(context.issue_reader, "list_issues", None)
        if not callable(list_issues):
            raise RuntimeError("query:issue:list requires issue_reader")
        records = list_issues(
            directory_path=directory_path,
            direct_only=direct_only,
            states=states,
            exclude_states=exclude_states,
        )
        if isinstance(records, Mapping):
            return list(records.values())
        if isinstance(records, Sequence) and not isinstance(records, (str, bytes, bytearray)):
            return list(records)
        raise RuntimeError(
            "query:issue:list issue_reader.list_issues must return a mapping or sequence"
        )

    def _matches_filters(
        self,
        issue: Any,
        *,
        source: str | None,
        source_type: str | None,
        tags_any: set[str] | None,
        tags_all: set[str] | None,
        metadata_equals: Mapping[str, Any],
        metadata_exists: Mapping[str, bool],
        metadata_prefix: Mapping[str, Any],
    ) -> bool:
        if source is not None and self._normalize_optional_string(self._record_value(issue, "source")) != source:
            return False
        if source_type is not None and self._normalize_optional_string(
            self._record_value(issue, "source_type")
        ) != source_type:
            return False

        issue_tags = self._coerce_string_set(self._record_value(issue, "tags")) or set()
        if tags_any is not None and not (issue_tags & tags_any):
            return False
        if tags_all is not None and not tags_all.issubset(issue_tags):
            return False

        metadata = self._record_value(issue, "metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}

        for selector, expected in metadata_equals.items():
            exists, value = self._metadata_value(metadata, selector)
            if not exists or value != expected:
                return False

        for selector, should_exist in metadata_exists.items():
            exists, _value = self._metadata_value(metadata, selector)
            if exists != should_exist:
                return False

        for selector, expected_prefix in metadata_prefix.items():
            exists, value = self._metadata_value(metadata, selector)
            if not exists:
                return False
            prefix = str(expected_prefix)
            if isinstance(value, str):
                if not value.startswith(prefix):
                    return False
                continue
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                if not any(str(item).startswith(prefix) for item in value):
                    return False
                continue
            return False

        return True

    def _issue_payload(self, issue: Any) -> dict[str, Any]:
        issue_id = self._normalize_optional_string(self._record_value(issue, "issue_id")) or ""
        issue_number = self._normalize_optional_string(self._record_value(issue, "issue_number")) or ""
        title = self._record_value(issue, "title")
        body = self._record_value(issue, "body")
        description = self._record_value(issue, "description")
        reason = self._record_value(issue, "reason")
        rejection_reason = self._record_value(issue, "rejection_reason")
        yaml_path = self._record_value(issue, "yaml_path")
        directory_path = self._record_value(issue, "directory_path")
        repo_source = self._record_value(issue, "repo_source")
        source_type = self._record_value(issue, "source_type")
        state = self._record_value(issue, "state")
        url = self._record_value(issue, "url")
        tags = self._coerce_string_list(self._record_value(issue, "tags"))
        comments = self._jsonable(self._record_value(issue, "comments"))
        source = self._record_value(issue, "source")
        metadata = self._jsonable(self._record_value(issue, "metadata"))
        created_at = self._jsonable(self._record_value(issue, "created_at"))
        updated_at = self._jsonable(self._record_value(issue, "updated_at"))
        remote_reference = self._record_value(issue, "remote_reference")

        return {
            "issue_id": issue_id,
            "issue_number": issue_number,
            "title": title,
            "body": body,
            "description": description,
            "reason": reason,
            "rejection_reason": rejection_reason,
            "yaml_path": yaml_path,
            "directory_path": directory_path,
            "repo_source": repo_source,
            "source_type": source_type,
            "state": state,
            "url": url,
            "tags": tags,
            "comments": comments,
            "source": source,
            "metadata": metadata,
            "created_at": created_at,
            "updated_at": updated_at,
            "remote_reference": self._jsonable(remote_reference) if remote_reference is not None else None,
            "can_edit": self._normalize_optional_string(source_type) == "local_manual",
            "can_delete": True,
            "can_create_vm": True,
        }

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("query:issue:list has no runtime context")
        return self.runtime_context

    @classmethod
    def _matches_directory(cls, issue: Any, directory_path: str | None) -> bool:
        if directory_path is None:
            return True
        issue_path = cls._normalize_directory_path(cls._record_value(issue, "directory_path"))
        return issue_path == directory_path or (
            issue_path is not None and issue_path.startswith(f"{directory_path}/")
        )

    @classmethod
    def _metadata_value(cls, metadata: Mapping[str, Any], selector: str) -> tuple[bool, Any]:
        if selector in metadata:
            return True, metadata[selector]
        parts = [part for part in selector.split(".") if part]
        if not parts:
            return False, None
        current: Any = metadata
        for part in parts:
            if not isinstance(current, Mapping) or part not in current:
                return False, None
            current = current[part]
        return True, current

    @staticmethod
    def _record_value(record: Any, key: str) -> Any:
        if isinstance(record, Mapping):
            return record.get(key)
        return getattr(record, key, None)

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return IssueListQueryProvider._jsonable(value.model_dump())
        if isinstance(value, dict):
            return {str(key): IssueListQueryProvider._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [IssueListQueryProvider._jsonable(item) for item in value]
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_directory_path(cls, value: Any) -> str | None:
        text = cls._normalize_optional_string(value)
        if text is None:
            return None
        return DirectoryPath(segments=text).as_posix()

    def _resolve_directory_path(self, value: Any) -> str:
        directory_path = self._normalize_directory_path(value)
        if directory_path is not None:
            return directory_path
        context = self.runtime_context
        metadata_directory_path = None
        if context is not None and isinstance(context.metadata, Mapping):
            metadata_directory_path = context.metadata.get("directory_path")
        directory_path = self._normalize_directory_path(metadata_directory_path)
        return directory_path or "root"

    @classmethod
    def _coerce_string_set(cls, value: Any) -> set[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return {text} if text else None
        if isinstance(value, Mapping):
            normalized = {str(key).strip() for key in value.keys() if str(key).strip()}
            return normalized or None
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            normalized = {str(item).strip() for item in value if str(item).strip()}
            return normalized or None
        text = str(value).strip()
        return {text} if text else None

    @staticmethod
    def _coerce_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"query:issue:list {field_name} must resolve to an object")
        return {str(key).strip(): item for key, item in value.items() if str(key).strip()}

    @staticmethod
    def _coerce_exists_selectors(value: Any) -> Mapping[str, bool]:
        if value is None:
            return {}
        if isinstance(value, str):
            return {value.strip(): True} if value.strip() else {}
        if isinstance(value, Mapping):
            return {
                str(key).strip(): IssueListQueryProvider._coerce_exists_flag(item)
                for key, item in value.items()
                if str(key).strip()
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return {str(item).strip(): True for item in value if str(item).strip()}
        raise ValueError(
            "query:issue:list metadata_exists must resolve to a string, array, or object"
        )

    @staticmethod
    def _coerce_exists_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"false", "0", "no", "off"}:
                return False
            if normalized in {"true", "1", "yes", "on"}:
                return True
        return bool(value)

    @staticmethod
    def _coerce_direct_only(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError("query:issue:list direct_only must resolve to a boolean")

    @staticmethod
    def _coerce_optional_limit(value: Any) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("query:issue:list limit must resolve to an integer") from exc
        if parsed < 1:
            raise ValueError("query:issue:list limit must be at least 1")
        return parsed

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, Mapping):
            return [str(key).strip() for key in value.keys() if str(key).strip()]
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []
