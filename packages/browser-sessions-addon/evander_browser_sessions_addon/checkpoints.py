"""Bounded observation predicates. No scripts, inferred values or browser input."""

from __future__ import annotations

from .models import BrowserError


def matches(element, target):
    return all(
        getattr(target, key) is None or element.get(key) == getattr(target, key)
        for key in ("ref", "role", "name")
    ) and (
        target.context is None
        or any(item.get("name") == target.context for item in element.get("context", []))
    )


def evaluate(condition, observation, values):
    """Return metadata-only evidence, or None while the condition is unsatisfied."""
    if observation.get("truncated"):
        raise BrowserError(
            "A truncated observation cannot establish a checkpoint", "checkpoint_incomplete", 409
        )
    if condition.kind == "text":
        return {} if condition.text in observation.get("text", "") else None
    elements = [e for e in observation.get("elements", []) if matches(e, condition.target)]
    count = len(elements)
    if condition.kind == "count":
        return (
            {"matched_count": count}
            if count >= condition.minimum
            and (condition.maximum is None or count <= condition.maximum)
            else None
        )
    if condition.kind == "absent":
        return {"matched_count": 0} if not elements else None
    if count > 1:
        raise BrowserError("Checkpoint target is ambiguous", "checkpoint_ambiguous", 409)
    if not elements:
        return None
    element = elements[0]
    evidence = {"matched_count": 1, "ref": element["ref"]}
    if condition.kind == "present":
        return evidence
    if condition.kind == "value":
        value = element.get("value")
        # shared/observation.ts clips with JavaScript String.slice(0, 1000).
        # At that boundary even a matching prefix cannot prove exact equality.
        if not isinstance(value, str) or len(value.encode("utf-16-le", "surrogatepass")) >= 2000:
            return None
        return evidence if value == values[condition.input].value else None
    # Enabled AX controls omit disabled rather than emitting false. This matches
    # the action validator's interpretation; other missing state is not evidence.
    state = element.get(condition.kind, False if condition.kind == "disabled" else None)
    if state in ("true", "false"):
        state = state == "true"
    return evidence if isinstance(state, bool) and state == condition.expected else None


def triggered(checkpoint, action, ref, input_id, key):
    trigger = checkpoint.after
    return (
        trigger.action == action
        and (trigger.ref is None or trigger.ref == ref)
        and (trigger.input is None or trigger.input == input_id)
        and (trigger.key is None or trigger.key == key)
    )
