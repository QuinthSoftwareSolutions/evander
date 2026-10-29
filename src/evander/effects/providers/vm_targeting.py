from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Literal

from .base import EffectRuntimeContext

VmTargetSource = Literal[
    "session_id",
    "vm_name",
    "target_session_id",
    "target_vm_name",
]


@dataclass(frozen=True, slots=True)
class ResolvedVmTarget:
    vm_name: str
    session_id: str
    operation_target: str
    source: VmTargetSource


def resolve_vm_target(
    context: EffectRuntimeContext,
    payload: Mapping[str, Any],
    *,
    effect_type: str,
) -> ResolvedVmTarget:
    session_id = _normalize_optional_string(payload.get("session_id"))
    vm_name = _normalize_optional_string(payload.get("vm_name"))
    target = _normalize_optional_string(payload.get("target"))

    if session_id:
        return ResolvedVmTarget(
            vm_name=resolve_vm_name_from_session(context, session_id, effect_type=effect_type),
            session_id=session_id,
            operation_target=session_id,
            source="session_id",
        )
    if vm_name:
        return ResolvedVmTarget(
            vm_name=vm_name,
            session_id=resolve_session_id_from_vm_name(context, vm_name, effect_type=effect_type),
            operation_target=vm_name,
            source="vm_name",
        )
    if target:
        session = read_all_sessions(context, effect_type=effect_type).get(target)
        if session is not None:
            resolved_vm_name = _normalize_optional_string(getattr(session, "vm_id", None))
            if not resolved_vm_name:
                raise RuntimeError(f"Session {target!r} does not have an associated VM")
            return ResolvedVmTarget(
                vm_name=resolved_vm_name,
                session_id=target,
                operation_target=target,
                source="target_session_id",
            )
        return ResolvedVmTarget(
            vm_name=target,
            session_id=resolve_session_id_from_vm_name(context, target, effect_type=effect_type),
            operation_target=target,
            source="target_vm_name",
        )
    raise RuntimeError(f"{effect_type} requires one of target, session_id, or vm_name")


def resolve_vm_name_from_session(
    context: EffectRuntimeContext,
    session_id: str,
    *,
    effect_type: str = "agent effect",
) -> str:
    session = read_all_sessions(context, effect_type=effect_type).get(session_id)
    if session is None:
        raise RuntimeError(f"Unknown session: {session_id}")
    vm_name = _normalize_optional_string(getattr(session, "vm_id", None))
    if not vm_name:
        raise RuntimeError(f"Session {session_id!r} does not have an associated VM")
    return vm_name


def resolve_session_id_from_vm_name(
    context: EffectRuntimeContext,
    vm_name: str,
    *,
    effect_type: str = "agent effect",
) -> str:
    matches = [
        session_id
        for session_id, session in read_all_sessions(context, effect_type=effect_type).items()
        if _normalize_optional_string(getattr(session, "vm_id", None)) == vm_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(
            f"VM {vm_name!r} is associated with multiple sessions: {', '.join(sorted(matches))}"
        )
    return f"session-{vm_name}"


def read_all_sessions(
    context: EffectRuntimeContext,
    *,
    effect_type: str = "agent effect",
) -> Mapping[str, Any]:
    if context.session_lookup is None:
        raise RuntimeError(f"{effect_type} requires session_lookup")
    return context.session_lookup.read_all_sessions()


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class VmTargetingMixin:
    def _resolve_target(self, payload: Mapping[str, Any], *, effect_type: str) -> tuple[str, str]:
        resolved = resolve_vm_target(self._context(), payload, effect_type=effect_type)
        return resolved.vm_name, resolved.session_id

    def _resolve_vm_name_from_session(self, session_id: str) -> str:
        return resolve_vm_name_from_session(
            self._context(), session_id, effect_type=self._effect_type()
        )

    def _resolve_session_id_from_vm_name(self, vm_name: str) -> str:
        return resolve_session_id_from_vm_name(
            self._context(), vm_name, effect_type=self._effect_type()
        )

    def _lookup_session(self, session_id: str) -> Any | None:
        return read_all_sessions(self._context(), effect_type=self._effect_type()).get(session_id)

    def _read_all_sessions(self) -> Mapping[str, Any]:
        return read_all_sessions(self._context(), effect_type=self._effect_type())

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        return _normalize_optional_string(value)

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
