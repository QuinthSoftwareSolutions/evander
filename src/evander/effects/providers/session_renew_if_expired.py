from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from evander.events.provider_models import SessionRenewIfExpiredEffectDefinition
from evander.gate_runtime import GateRuntimeError, parse_iso8601_duration
from evander.session_expiry import ensure_utc_datetime
from evander.subscription_profile_bindings import save_session_with_subscription_profile

from .agent_session import (
    effective_config_for_directory,
    effective_config_for_session,
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
            "renew_if_expires_within": effect.renew_if_expires_within,
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
        renew_if_expires_within = parse_session_renew_if_expires_within(
            payload.get("renew_if_expires_within")
        )
        renew_if_expires_within_seconds = (
            _duration_to_seconds(renew_if_expires_within)
            if renew_if_expires_within is not None
            else None
        )
        new_expires_at = previous_expires_at
        was_expired = previous_expires_at <= now
        renewal_reason = "outside_window"
        extended = False
        if was_expired:
            extended = True
            renewal_reason = "expired"
        elif (
            renew_if_expires_within is not None
            and previous_expires_at <= now + renew_if_expires_within
        ):
            extended = True
            renewal_reason = "within_window"

        if extended:
            new_expires_at = now + duration
            session = session.model_copy(update={"expires_at": new_expires_at})
            save_session_with_subscription_profile(
                config=effective_config_for_session(
                    context, session.session_id, fallback_directory_path=session.directory_path
                ),
                repository=repository,
                session=session,
            )

        vm_name = normalize_optional_string(getattr(session, "vm_id", None))
        return {
            "success": True,
            "action": "renew_if_expired",
            "status": "extended" if extended else "active",
            "extended": extended,
            "renewed": extended,
            "was_expired": was_expired,
            "renewal_reason": renewal_reason,
            "target": target,
            "session_id": session.session_id,
            "vm_name": vm_name,
            "directory_path": session.directory_path,
            "extend_by": _duration_to_seconds(duration),
            "renew_if_expires_within": renew_if_expires_within_seconds,
            "previous_expires_at": previous_expires_at.isoformat(),
            "new_expires_at": new_expires_at.isoformat(),
            "expires_at": new_expires_at.isoformat(),
        }

    def _target_from_payload(self, payload: Mapping[str, Any]) -> str:
        selectors = {
            field_name: normalize_optional_string(payload.get(field_name))
            for field_name in ("target", "session_id", "vm_name")
        }
        chosen = [value for value in selectors.values() if value is not None]
        if len(chosen) != 1:
            raise ValueError(
                "effect:session:renew-if-expired requires exactly one of target, session_id, or vm_name"
            )
        return chosen[0]

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
    return _parse_session_duration(
        value,
        field_name="extend_by",
        allow_blank=False,
    )


def parse_session_renew_if_expires_within(value: Any) -> timedelta | None:
    return _parse_session_duration(
        value,
        field_name="renew_if_expires_within",
        allow_blank=True,
    )


def _parse_session_duration(
    value: Any,
    *,
    field_name: str,
    allow_blank: bool,
) -> timedelta | None:
    duration_type_error = f"effect:session:renew-if-expired {field_name} must be a duration string such as 8h, 2d, or PT8H"
    if value is None:
        if allow_blank:
            return None
        raise ValueError(duration_type_error)
    if not isinstance(value, str):
        raise ValueError(duration_type_error)
    normalized = value.strip()
    if not normalized:
        if allow_blank:
            return None
        raise ValueError(duration_type_error)
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
            raise ValueError(
                f"effect:session:renew-if-expired {field_name} must be greater than zero"
            )
        return timedelta(seconds=seconds)

    try:
        duration = parse_iso8601_duration(
            normalized.upper(),
            field_name=field_name,
            gate_type="effect:session:renew-if-expired",
        )
    except GateRuntimeError as exc:
        raise ValueError(str(exc)) from exc
    if duration.total_seconds() <= 0:
        raise ValueError(f"effect:session:renew-if-expired {field_name} must be greater than zero")
    return duration


def _duration_to_seconds(value: timedelta) -> int:
    return int(value.total_seconds())
