from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class VmTargetingMixin:
    def _resolve_target(self, payload: Mapping[str, Any], *, effect_type: str) -> tuple[str, str]:
        session_id = self._normalize_optional_string(payload.get("session_id"))
        vm_name = self._normalize_optional_string(payload.get("vm_name"))
        target = self._normalize_optional_string(payload.get("target"))

        if session_id:
            return self._resolve_vm_name_from_session(session_id), session_id
        if vm_name:
            return vm_name, self._resolve_session_id_from_vm_name(vm_name)
        if target:
            session = self._lookup_session(target)
            if session is not None:
                resolved_vm_name = self._normalize_optional_string(getattr(session, "vm_id", None))
                if not resolved_vm_name:
                    raise RuntimeError(f"Session {target!r} does not have an associated VM")
                return resolved_vm_name, target
            return target, self._resolve_session_id_from_vm_name(target)
        raise RuntimeError(f"{effect_type} requires one of target, session_id, or vm_name")

    def _resolve_vm_name_from_session(self, session_id: str) -> str:
        session = self._lookup_session(session_id)
        if session is None:
            raise RuntimeError(f"Unknown session: {session_id}")
        vm_name = self._normalize_optional_string(getattr(session, "vm_id", None))
        if not vm_name:
            raise RuntimeError(f"Session {session_id!r} does not have an associated VM")
        return vm_name

    def _resolve_session_id_from_vm_name(self, vm_name: str) -> str:
        sessions = self._read_all_sessions()
        matches = [
            session_id
            for session_id, session in sessions.items()
            if self._normalize_optional_string(getattr(session, "vm_id", None)) == vm_name
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                f"VM {vm_name!r} is associated with multiple sessions: {', '.join(sorted(matches))}"
            )
        return f"session-{vm_name}"

    def _lookup_session(self, session_id: str) -> Any | None:
        return self._read_all_sessions().get(session_id)

    def _read_all_sessions(self) -> Mapping[str, Any]:
        context = self._context()
        if context.session_lookup is None:
            raise RuntimeError(f"{self._effect_type()} requires session_lookup")
        return context.session_lookup.read_all_sessions()

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _required_string(cls, value: Any, error_message: str) -> str:
        text = cls._normalize_optional_string(value)
        if text is None:
            raise ValueError(error_message)
        return text

    @staticmethod
    def _as_string_list(value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return None
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized or None
