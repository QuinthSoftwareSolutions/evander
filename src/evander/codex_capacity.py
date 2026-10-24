"""Read-only capacity decisions shared by subscription admission and queue recovery."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation


def _has_purchased_capacity(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("unlimited") is True:
        return True
    if value.get("has_credits") is not True:
        return False
    # The provider's availability flag is authoritative when its optional balance
    # is absent. An exposed balance must not contradict that flag.
    balance = value.get("balance")
    if balance is None:
        return True
    if not isinstance(balance, str):
        return False
    try:
        amount = Decimal(balance)
    except InvalidOperation:
        return False
    return amount.is_finite() and amount > 0


def subscription_rate_limit_state(
    value: object, *, now: datetime | None = None
) -> tuple[bool, datetime | None]:
    """Return a capacity deferral and its latest known ordinary-window reset.

    Purchased usage capacity covers exhausted included windows only in its own
    bucket. Explicit provider blocks still win. No reset-credit data is read and
    no billing, identity, or capacity mutation is performed here.
    """
    if not isinstance(value, dict):
        return False, None
    buckets = []
    default = value.get("default")
    if isinstance(default, dict):
        buckets.append(default)
    by_id = value.get("by_limit_id")
    if isinstance(by_id, dict):
        buckets.extend(bucket for bucket in by_id.values() if isinstance(bucket, dict))

    reset_times: list[datetime] = []
    reached = False
    for bucket in buckets:
        blocked = bool(bucket.get("rate_limit_reached_type")) or (
            bucket.get("spend_control_reached") is True
        )
        if not blocked and _has_purchased_capacity(bucket.get("credits")):
            continue
        reached = reached or blocked
        for window_name in ("primary", "secondary"):
            window = bucket.get(window_name)
            if not isinstance(window, dict):
                continue
            try:
                window_reached = float(window.get("used_percent")) >= 100
            except (TypeError, ValueError):
                window_reached = False
            if not window_reached:
                continue
            reached = True
            reset_text = str(window.get("resets_at") or "").strip()
            if not reset_text:
                continue
            try:
                parsed = datetime.fromisoformat(reset_text.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            reset_times.append(parsed.astimezone(UTC))
    if not reached:
        return False, None
    observed_at = now or datetime.now(UTC)
    future = [reset for reset in reset_times if reset > observed_at]
    # Any still-exhausted window prevents admission, so avoid an early wake that
    # would guarantee another deferral.
    return True, max(future) if future else None
