from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from evander.models import DirectoryPath

from .base import QueryProviderBase


class SessionListQueryProvider(QueryProviderBase):
    """List sessions by generic manifest metadata."""

    NODE_TYPE = "query:session:list"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = self._resolve_directory_path(payload.get("directory_path"))
        metadata_equals = self._coerce_mapping(payload.get("metadata_equals"), "metadata_equals")
        metadata_prefix = self._coerce_mapping(payload.get("metadata_prefix"), "metadata_prefix")
        metadata_exists = self._coerce_exists_selectors(payload.get("metadata_exists"))
        active_only = self._coerce_optional_bool(payload.get("active_only"))
        state = self._normalize_optional_string(payload.get("state"))
        limit = self._coerce_optional_limit(payload.get("limit"))

        sessions = [
            session
            for session in self._list_sessions()
            if self._matches_directory(session, directory_path)
            and self._matches_state(session, state=state, active_only=active_only)
            and self._matches_metadata(
                session,
                metadata_equals=metadata_equals,
                metadata_exists=metadata_exists,
                metadata_prefix=metadata_prefix,
            )
        ]
        sessions.sort(
            key=lambda session: (
                self._datetime_sort_value(getattr(session, "created_at", None)),
                self._datetime_sort_value(getattr(session, "expires_at", None)),
                str(getattr(session, "session_id", "") or ""),
            )
        )
        if limit is not None:
            sessions = sessions[:limit]

        return {
            "sessions": [self._session_payload(session) for session in sessions],
            "count": len(sessions),
        }

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in (
            "directory_path",
            "metadata_equals",
            "metadata_exists",
            "metadata_prefix",
            "state",
            "active_only",
            "limit",
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _list_sessions(self) -> list[Any]:
        context = self._context()
        read_all = getattr(context.session_lookup, "read_all_sessions", None)
        if not callable(read_all):
            raise RuntimeError("SessionListQueryProvider requires session_lookup")
        sessions = read_all()
        if isinstance(sessions, Mapping):
            return list(sessions.values())
        if isinstance(sessions, Sequence) and not isinstance(sessions, (str, bytes, bytearray)):
            return list(sessions)
        raise RuntimeError("session_lookup.read_all_sessions must return a mapping or sequence")

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("SessionListQueryProvider has no runtime context")
        return self.runtime_context

    @classmethod
    def _matches_directory(cls, session: Any, directory_path: str | None) -> bool:
        if directory_path is None:
            return True
        session_path = cls._normalize_directory_path(getattr(session, "directory_path", None))
        return session_path == directory_path or (
            session_path is not None and session_path.startswith(f"{directory_path}/")
        )

    @classmethod
    def _matches_state(
        cls,
        session: Any,
        *,
        state: str | None,
        active_only: bool | None,
    ) -> bool:
        active = cls._is_active(session)
        if active_only is True and not active:
            return False
        if state is None or state in {"", "all", "any"}:
            return True
        normalized_state = state.strip().lower()
        if normalized_state == "active":
            return active
        if normalized_state in {"expired", "inactive"}:
            return not active
        raise ValueError("query:session:list state must be active, expired, inactive, all, or any")

    @classmethod
    def _matches_metadata(
        cls,
        session: Any,
        *,
        metadata_equals: Mapping[str, Any],
        metadata_exists: Mapping[str, bool],
        metadata_prefix: Mapping[str, Any],
    ) -> bool:
        metadata = getattr(session, "metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}

        for selector, expected in metadata_equals.items():
            exists, value = cls._metadata_value(metadata, selector)
            if not exists or value != expected:
                return False

        for selector, should_exist in metadata_exists.items():
            exists, _value = cls._metadata_value(metadata, selector)
            if exists != should_exist:
                return False

        for selector, expected_prefix in metadata_prefix.items():
            exists, value = cls._metadata_value(metadata, selector)
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

    @staticmethod
    def _metadata_value(metadata: Mapping[str, Any], selector: str) -> tuple[bool, Any]:
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
    def _session_payload(session: Any) -> dict[str, Any]:
        metadata = getattr(session, "metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}
        created_at = getattr(session, "created_at", None)
        expires_at = getattr(session, "expires_at", None)
        return {
            "session_id": str(getattr(session, "session_id", "") or ""),
            "vm_name": str(getattr(session, "vm_id", "") or ""),
            "directory_path": str(getattr(session, "directory_path", "") or "root"),
            "created_at": SessionListQueryProvider._isoformat(created_at),
            "expires_at": SessionListQueryProvider._isoformat(expires_at),
            "model": getattr(session, "model", None),
            "metadata": dict(metadata),
        }

    @staticmethod
    def _is_active(session: Any) -> bool:
        expires_at = getattr(session, "expires_at", None)
        if not isinstance(expires_at, datetime):
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > datetime.now(UTC)

    @staticmethod
    def _datetime_sort_value(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return datetime.min.replace(tzinfo=UTC)

    @staticmethod
    def _isoformat(value: Any) -> str | None:
        if isinstance(value, datetime):
            return value.isoformat()
        if value is None:
            return None
        return str(value)

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

    @staticmethod
    def _coerce_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"query:session:list {field_name} must resolve to an object")
        return {str(key).strip(): item for key, item in value.items() if str(key).strip()}

    @staticmethod
    def _coerce_exists_selectors(value: Any) -> Mapping[str, bool]:
        if value is None:
            return {}
        if isinstance(value, str):
            return {value.strip(): True} if value.strip() else {}
        if isinstance(value, Mapping):
            return {
                str(key).strip(): SessionListQueryProvider._coerce_exists_flag(item)
                for key, item in value.items()
                if str(key).strip()
            }
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return {str(item).strip(): True for item in value if str(item).strip()}
        raise ValueError(
            "query:session:list metadata_exists must resolve to a string, array, or object"
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
    def _coerce_optional_bool(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError("query:session:list active_only must resolve to a boolean")

    @staticmethod
    def _coerce_optional_limit(value: Any) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("query:session:list limit must resolve to an integer") from exc
        if parsed < 1:
            raise ValueError("query:session:list limit must be at least 1")
        return parsed
