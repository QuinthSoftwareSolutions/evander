from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_sandbox.events.provider_models import SessionRenewIfExpiredEffectDefinition
from agent_sandbox.gate_runtime import GateRuntimeError, parse_iso8601_duration
from agent_sandbox.session_expiry import ensure_utc_datetime

from .agent_session import (
    effective_config_for_directory,
    normalize_optional_string,
    runtime_session_repository,
)
from .base import EffectProviderBase, EffectRuntimeContext

_SIMPLE_DURATION_PATTERN = re.compile(r"^(?P<amount>\d+(?:\.\d+)?)(?P<unit>[smhdw])$")


class SessionRenewIfExpiredEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "extend_by": effect.extend_by,
            "directory_path": effect.directory_path,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        target = self._target_from_payload(payload)
        directory_path = normalize_optional_string(payload.get("directory_path")) or str(
            context.metadata.get("directory_path") or "root"
        )
        repository = runtime_session_repository(
            context,
            effective_config_for_directory(context, directory_path),
        )
        session = repository.resolve_session_target(target)
        if session is None:
            raise ValueError(f"Unknown session target: {target}")

        now = datetime.now(UTC)
        previous_expires_at = ensure_utc_datetime(session.expires_at)
        duration = parse_session_extend_by(payload.get("extend_by"))
        new_expires_at = previous_expires_at
        extended = previous_expires_at <= now
        if extended:
            new_expires_at = now + duration
            session.expires_at = new_expires_at
            repository.upsert_session(session)

        vm_name = normalize_optional_string(getattr(session, "vm_id", None))
        return {
            "success": True,
            "action": "renew_if_expired",
            "status": "extended" if extended else "active",
            "extended": extended,
            "was_expired": extended,
            "target": target,
            "session_id": session.session_id,
            "vm_name": vm_name,
            "directory_path": session.directory_path,
            "extend_by": _duration_to_seconds(duration),
            "previous_expires_at": previous_expires_at.isoformat(),
            "new_expires_at": new_expires_at.isoformat(),
            "expires_at": new_expires_at.isoformat(),
        }

    def _target_from_payload(self, payload: Mapping[str, Any]) -> str:
        for field_name in ("target", "session_id", "vm_name"):
            value = normalize_optional_string(payload.get(field_name))
            if value:
                return value
        raise ValueError("effect:session:renew-if-expired requires target, session_id, or vm_name")

    def _effect(self) -> SessionRenewIfExpiredEffectDefinition:
        if not isinstance(self.effect_definition, SessionRenewIfExpiredEffectDefinition):
            raise RuntimeError(
                "SessionRenewIfExpiredEffectProvider initialized with a non-session-renew-if-expired effect definition"
            )
        return self.effect_definition

    def _context(self) -> EffectRuntimeContext:
        if self.runtime_context is None:
            raise RuntimeError("SessionRenewIfExpiredEffectProvider has no runtime context")
        return self.runtime_context


def parse_session_extend_by(value: Any) -> timedelta:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "effect:session:renew-if-expired extend_by must be a duration string such as 8h, 2d, or PT8H"
        )
    normalized = value.strip()
    simple_match = _SIMPLE_DURATION_PATTERN.fullmatch(normalized.lower())
    if simple_match is not None:
        amount = float(simple_match.group("amount"))
        unit = simple_match.group("unit")
        seconds_by_unit = {
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400,
            "w": 604800,
        }
        seconds = amount * seconds_by_unit[unit]
        if seconds <= 0:
            raise ValueError("effect:session:renew-if-expired extend_by must be greater than zero")
        return timedelta(seconds=seconds)

    try:
        return parse_iso8601_duration(
            normalized.upper(),
            field_name="extend_by",
            gate_type="effect:session:renew-if-expired",
        )
    except GateRuntimeError as exc:
        raise ValueError(str(exc)) from exc


def _duration_to_seconds(value: timedelta) -> int:
    return int(value.total_seconds())
