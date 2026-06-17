#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_sandbox.storage import JsonObjectStore
from truman_team_addon.models import SCHEMA_VERSION, TeamState


DEFAULT_STATE_PATH = Path("runtime/addons/team-addon/state.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Team Add-on state from raw role strings to role records."
    )
    parser.add_argument(
        "state_path",
        nargs="?",
        default=DEFAULT_STATE_PATH,
        type=Path,
        help=f"Team Add-on state JSON path. Defaults to {DEFAULT_STATE_PATH}.",
    )
    args = parser.parse_args()

    state_path = args.state_path
    if not state_path.exists():
        raise SystemExit(f"{state_path}: no such file")

    backup_path: Path | None = None
    result: dict[str, bool] = {"changed": False}
    store = JsonObjectStore(state_path, {})

    def _update(payload: dict[str, Any]) -> None:
        nonlocal backup_path
        changed = migrate_payload(payload)
        TeamState.model_validate(payload)
        result["changed"] = changed
        if changed:
            backup_path = _backup_path(state_path)
            shutil.copy2(state_path, backup_path)

    store.update_object(_update)

    if not result["changed"]:
        print(f"{state_path}: already migrated")
        return 0

    print(f"{state_path}: migrated to schema_version {SCHEMA_VERSION}")
    print(f"backup: {backup_path}")
    return 0


def migrate_payload(payload: dict[str, Any]) -> bool:
    changed = False
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    roles = _object_map(payload, "roles")
    teams = _object_map(payload, "teams")
    projects = _object_map(payload, "projects")
    agents = _object_map(payload, "agents")

    for agent_id, agent in agents.items():
        if not isinstance(agent, dict):
            continue
        raw_role = agent.pop("role", None)
        if raw_role is not None:
            changed = True
        role_id = _text(agent.get("role_id"))
        if not role_id:
            role_key = _text(raw_role) or "agent"
            role_id = _default_role_id(
                team_id=_text(agent.get("team_id")) or "team",
                project_id=_text(agent.get("project_id")),
                role_key=role_key,
            )
            agent["role_id"] = role_id
            changed = True
        role_key = _role_key_from_id(role_id)
        if raw_role is not None:
            role_key = _text(raw_role) or role_key
        if role_id not in roles:
            roles[role_id] = _role_record(
                role_id=role_id,
                team_id=_text(agent.get("team_id")) or "team",
                project_id=_text(agent.get("project_id")),
                role_key=role_key,
                ephemeral_policy=agent.get("ephemeral_policy"),
                now=now,
            )
            changed = True
        if not _text(agent.get("team_id")):
            raise ValueError(f"agent {agent_id} has no team_id")

    review_profiles = _object_map(payload, "review_profiles")
    for profile_id, profile in review_profiles.items():
        if not isinstance(profile, dict):
            continue
        raw_roles = profile.pop("applicable_roles", None)
        if raw_roles is None:
            continue
        changed = True
        role_ids: list[str] = []
        for raw_role in _text_list(raw_roles):
            role_id = _role_id_for_filter(
                roles,
                team_id=_text(profile.get("team_id")) or "team",
                project_id=_text(profile.get("project_id")),
                role_key=raw_role,
                now=now,
            )
            if role_id not in role_ids:
                role_ids.append(role_id)
        profile["applicable_role_ids"] = role_ids
        _ = profile_id

    if payload.get("schema_version") != SCHEMA_VERSION:
        payload["schema_version"] = SCHEMA_VERSION
        changed = True
    if changed:
        payload["updated_at"] = now

    # Force creation of manager/project-lead role records for already mixed
    # state if the agents were converted before this script ran.
    changed = _ensure_core_roles(payload, roles, teams, projects, agents, now) or changed
    return changed


def _ensure_core_roles(
    payload: dict[str, Any],
    roles: dict[str, Any],
    teams: dict[str, Any],
    projects: dict[str, Any],
    agents: dict[str, Any],
    now: str,
) -> bool:
    changed = False
    for team in teams.values():
        if not isinstance(team, dict):
            continue
        manager_agent_id = _text(team.get("manager_agent_id"))
        manager = agents.get(manager_agent_id) if manager_agent_id else None
        if isinstance(manager, dict) and _text(manager.get("role_id")) not in roles:
            role_id = _text(manager.get("role_id")) or _default_role_id(
                team_id=_text(team.get("id")) or "team",
                project_id=None,
                role_key="manager",
            )
            manager["role_id"] = role_id
            roles[role_id] = _role_record(
                role_id=role_id,
                team_id=_text(team.get("id")) or "team",
                project_id=None,
                role_key="manager",
                ephemeral_policy=manager.get("ephemeral_policy"),
                now=now,
            )
            changed = True
    for project in projects.values():
        if not isinstance(project, dict):
            continue
        lead_agent_id = _text(project.get("lead_agent_id"))
        lead = agents.get(lead_agent_id) if lead_agent_id else None
        if isinstance(lead, dict) and _text(lead.get("role_id")) not in roles:
            role_id = _text(lead.get("role_id")) or _default_role_id(
                team_id=_text(project.get("team_id")) or "team",
                project_id=_text(project.get("id")),
                role_key="project_lead",
            )
            lead["role_id"] = role_id
            roles[role_id] = _role_record(
                role_id=role_id,
                team_id=_text(project.get("team_id")) or "team",
                project_id=_text(project.get("id")),
                role_key="project_lead",
                ephemeral_policy=lead.get("ephemeral_policy"),
                now=now,
            )
            changed = True
    if changed:
        payload["updated_at"] = now
    return changed


def _role_id_for_filter(
    roles: dict[str, Any],
    *,
    team_id: str,
    project_id: str | None,
    role_key: str,
    now: str,
) -> str:
    project_role_id = _default_role_id(
        team_id=team_id,
        project_id=project_id,
        role_key=role_key,
    )
    if project_role_id in roles:
        return project_role_id
    team_role_id = _default_role_id(team_id=team_id, project_id=None, role_key=role_key)
    if team_role_id in roles:
        return team_role_id
    roles[project_role_id] = _role_record(
        role_id=project_role_id,
        team_id=team_id,
        project_id=project_id,
        role_key=role_key,
        ephemeral_policy=None,
        now=now,
    )
    return project_role_id


def _role_record(
    *,
    role_id: str,
    team_id: str,
    project_id: str | None,
    role_key: str,
    ephemeral_policy: Any,
    now: str,
) -> dict[str, Any]:
    normalized_key = _role_id_part(role_key)
    return {
        "id": role_id,
        "team_id": team_id,
        "project_id": project_id,
        "name": _role_name(role_key),
        "description": "",
        "permissions": (
            ["request_release"] if normalized_key == "release-operator" else []
        ),
        "ephemeral_policy": (
            ephemeral_policy
            if isinstance(ephemeral_policy, dict)
            else {"max_concurrent": 0}
        ),
        "archived": False,
        "created_at": now,
        "updated_at": now,
    }


def _default_role_id(*, team_id: str, project_id: str | None, role_key: str) -> str:
    scope = project_id or team_id
    return f"{scope}__{_role_id_part(role_key)}"


def _role_id_part(value: str) -> str:
    return value.strip().replace("_", "-")


def _role_key_from_id(role_id: str) -> str:
    return role_id.rsplit("__", 1)[-1].replace("-", "_")


def _role_name(value: str) -> str:
    return " ".join(
        part.capitalize()
        for part in value.replace("_", " ").replace("-", " ").split()
    )


def _object_map(payload: dict[str, Any], field: str) -> dict[str, Any]:
    value = payload.get(field)
    if value is None:
        payload[field] = {}
        return payload[field]
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("role filter must be a list")
    result: list[str] = []
    for item in value:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return result


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.{stamp}.bak")


if __name__ == "__main__":
    raise SystemExit(main())
