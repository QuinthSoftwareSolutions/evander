from __future__ import annotations

from pathlib import Path


def normalize_approval_notification_target_ids(
    value: object, *, field_name: str
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a JSON array of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        target_id = str(item or "").strip().lower()
        if not target_id or target_id in seen:
            continue
        seen.add(target_id)
        normalized.append(target_id)
    return normalized


def resolve_approval_notification_target_ids_for_directory(
    *,
    runtime_dir: Path,
    directory_store: object,
    directory_path: str,
    target_ids: object,
    field_name: str = "approval_notification_target_ids",
) -> list[str]:
    from evander.messaging.targets import load_communication_targets_config

    normalized_target_ids = normalize_approval_notification_target_ids(
        target_ids,
        field_name=field_name,
    )
    targets_by_id = load_communication_targets_config(
        runtime_dir=runtime_dir,
        directory_store=directory_store,
        directory_path=directory_path,
    ).by_id()

    missing_target_ids: list[str] = []
    non_notify_target_ids: list[str] = []
    resolved_target_ids: list[str] = []
    seen_resolved_target_ids: set[str] = set()
    for target_id in normalized_target_ids:
        target = targets_by_id.get(target_id)
        if target is None:
            missing_target_ids.append(target_id)
            continue
        if "notify" not in set(target.policy.capabilities):
            non_notify_target_ids.append(target.id)
            continue
        if target.id in seen_resolved_target_ids:
            continue
        seen_resolved_target_ids.add(target.id)
        resolved_target_ids.append(target.id)

    if missing_target_ids or non_notify_target_ids:
        message_parts: list[str] = []
        if missing_target_ids:
            message_parts.append(
                "unknown communication target(s): " + ", ".join(missing_target_ids)
            )
        if non_notify_target_ids:
            message_parts.append(
                "communication target(s) without notify capability: "
                + ", ".join(non_notify_target_ids)
            )
        raise ValueError(
            f"{field_name} for directory {directory_path!r} is invalid: "
            + "; ".join(message_parts)
        )

    return resolved_target_ids
