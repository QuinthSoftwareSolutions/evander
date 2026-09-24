"""Seed and track versioned agent guidance in execution sessions."""

from __future__ import annotations

import re
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from evander.effects.providers.agent_copy_files import (
    AgentCopyFilesEffectProvider,
)
from evander.effects.providers.agent_session import effective_config_for_session
from evander.events.provider_models import (
    AgentCopyFilesEffectDefinition,
)

from .provider_constants import (
    LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS,
    TEAM_AGENT_GUIDANCE_PATH,
    TEAM_AGENT_GUIDANCE_SECTION_ID,
    TEAM_AGENT_GUIDANCE_VERSION,
    TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
    TEAM_GOVERNANCE_GUIDANCE_VERSION,
)
from .provider_prompts import (
    _agent_guidance_identity,
    _team_agent_guidance_content,
    _team_agent_guidance_inactive_content,
    _team_governance_guidance_content,
)
from .provider_values import (
    _optional_text,
    _required_text,
)


def _legacy_agent_guidance_section_ids(context: Any, session_id: str) -> list[str]:
    config = effective_config_for_session(context, session_id)
    team_state = config.provider_state.get("team_addon", {})
    if not isinstance(team_state, Mapping):
        raise ValueError("provider_state.team_addon must be a JSON object")
    configured = team_state.get("legacy_guidance_section_ids", [])
    if not isinstance(configured, list):
        raise ValueError("legacy guidance section IDs must be a JSON array")
    result: list[str] = []
    for value in (*LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS, *configured):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
            raise ValueError("legacy guidance section IDs must be valid section names")
        if value == TEAM_AGENT_GUIDANCE_SECTION_ID:
            raise ValueError("current guidance section cannot be removed as legacy")
        if value not in result:
            result.append(value)
    return result


def _seed_team_agent_guidance(
    context: Any,
    *,
    agent: Mapping[str, Any],
) -> dict[str, Any]:
    session_id = _required_text(agent.get("session_id"), "session_id")
    identity = _agent_guidance_identity(agent)
    metadata = _session_metadata(context, session_id=session_id)
    if _metadata_has_current_agent_guidance(metadata, agent=agent):
        return {
            "seeded": False,
            "reason": "already_seeded",
            "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
            **identity,
        }

    legacy_section_ids = _legacy_agent_guidance_section_ids(context, session_id)
    provider = AgentCopyFilesEffectProvider()
    provider.initialize(
        AgentCopyFilesEffectDefinition(
            id="team-seed-agent-guidance",
            display_name="Seed Team agent guidance",
            type="effect:agent:copy-files",
            session_id=session_id,
            vm_user="agent",
            files=[
                {
                    "content": _team_agent_guidance_content(agent=agent),
                    "destination_path": TEAM_AGENT_GUIDANCE_PATH,
                    "mode": "0644",
                    "overwrite": True,
                    "merge_strategy": "managed_section",
                    "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
                    **({"remove_section_ids": legacy_section_ids} if legacy_section_ids else {}),
                }
            ],
        ),
        context,
    )
    copy_result = provider.execute()
    if not bool(copy_result.get("success")):
        message = str(copy_result.get("message") or copy_result.get("error") or "").strip()
        raise RuntimeError(message or "failed to seed Team agent guidance")

    copied_vm_ip = _optional_text(copy_result.get("vm_ip"))
    _mark_agent_guidance_seeded(
        context,
        session_id=session_id,
        agent=agent,
        copy_result=copy_result,
        vm_ip=copied_vm_ip,
    )
    return {
        "seeded": True,
        "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
        "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
        "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
        **identity,
        "vm_ip": copied_vm_ip,
        "copied_paths": list(copy_result.get("copied_paths") or []),
    }


def _seed_team_governance_guidance(
    context: Any,
    *,
    agent: Mapping[str, Any],
) -> dict[str, Any]:
    session_id = _required_text(agent.get("session_id"), "session_id")
    identity = _agent_guidance_identity(agent)
    metadata = _session_metadata(context, session_id=session_id)
    if _metadata_has_current_governance_guidance(metadata, agent=agent):
        return {
            "seeded": False,
            "reason": "already_seeded",
            "guidance_version": TEAM_GOVERNANCE_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
            **identity,
        }

    files: list[dict[str, Any]] = []
    inactive_team_guidance = _optional_text(agent.get("team_id")) is None
    if inactive_team_guidance:
        legacy_section_ids = _legacy_agent_guidance_section_ids(context, session_id)
        files.append(
            {
                "content": _team_agent_guidance_inactive_content(agent=agent),
                "destination_path": TEAM_AGENT_GUIDANCE_PATH,
                "mode": "0644",
                "overwrite": True,
                "merge_strategy": "managed_section",
                "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
                **({"remove_section_ids": legacy_section_ids} if legacy_section_ids else {}),
            }
        )
    files.append(
        {
            "content": _team_governance_guidance_content(agent=agent),
            "destination_path": TEAM_AGENT_GUIDANCE_PATH,
            "mode": "0644",
            "overwrite": True,
            "merge_strategy": "managed_section",
            "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
        }
    )

    provider = AgentCopyFilesEffectProvider()
    provider.initialize(
        AgentCopyFilesEffectDefinition(
            id="team-seed-governance-guidance",
            display_name="Seed Team governance guidance",
            type="effect:agent:copy-files",
            session_id=session_id,
            vm_user="agent",
            files=files,
        ),
        context,
    )
    copy_result = provider.execute()
    if not bool(copy_result.get("success")):
        message = str(copy_result.get("message") or copy_result.get("error") or "").strip()
        raise RuntimeError(message or "failed to seed Team governance guidance")

    copied_vm_ip = _optional_text(copy_result.get("vm_ip"))
    _mark_governance_guidance_seeded(
        context,
        session_id=session_id,
        agent=agent,
        copy_result=copy_result,
        vm_ip=copied_vm_ip,
        inactive_team_guidance=inactive_team_guidance,
    )
    return {
        "seeded": True,
        "guidance_version": TEAM_GOVERNANCE_GUIDANCE_VERSION,
        "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
        "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
        **identity,
        "vm_ip": copied_vm_ip,
        "copied_paths": list(copy_result.get("copied_paths") or []),
    }


def _metadata_has_current_agent_guidance(
    metadata: Mapping[str, Any],
    *,
    agent: Mapping[str, Any],
) -> bool:
    team_addon = metadata.get("team_addon")
    if not isinstance(team_addon, Mapping):
        return False
    guidance = team_addon.get("agent_guidance")
    if not isinstance(guidance, Mapping):
        return False
    if guidance.get("inactive") is True:
        return False
    identity = _agent_guidance_identity(agent)
    return (
        int(guidance.get("guidance_version") or 0) == TEAM_AGENT_GUIDANCE_VERSION
        and guidance.get("guidance_path") == TEAM_AGENT_GUIDANCE_PATH
        and guidance.get("section_id") == TEAM_AGENT_GUIDANCE_SECTION_ID
        and guidance.get("agent_id") == identity["agent_id"]
        and guidance.get("display_name") == identity["display_name"]
        and guidance.get("seeded") is True
    )


def _metadata_has_current_governance_guidance(
    metadata: Mapping[str, Any],
    *,
    agent: Mapping[str, Any],
) -> bool:
    team_addon = metadata.get("team_addon")
    if not isinstance(team_addon, Mapping):
        return False
    guidance = team_addon.get("governance_agent_guidance")
    if not isinstance(guidance, Mapping):
        return False
    identity = _agent_guidance_identity(agent)
    governance_guidance_is_current = (
        int(guidance.get("guidance_version") or 0) == TEAM_GOVERNANCE_GUIDANCE_VERSION
        and guidance.get("guidance_path") == TEAM_AGENT_GUIDANCE_PATH
        and guidance.get("section_id") == TEAM_GOVERNANCE_GUIDANCE_SECTION_ID
        and guidance.get("agent_id") == identity["agent_id"]
        and guidance.get("display_name") == identity["display_name"]
        and guidance.get("seeded") is True
    )
    if not governance_guidance_is_current:
        return False
    if _optional_text(agent.get("team_id")) is None:
        return _metadata_has_current_inactive_agent_guidance(metadata, agent=agent)
    return True


def _metadata_has_current_inactive_agent_guidance(
    metadata: Mapping[str, Any],
    *,
    agent: Mapping[str, Any],
) -> bool:
    team_addon = metadata.get("team_addon")
    if not isinstance(team_addon, Mapping):
        return False
    guidance = team_addon.get("agent_guidance")
    if not isinstance(guidance, Mapping):
        return False
    identity = _agent_guidance_identity(agent)
    return (
        int(guidance.get("guidance_version") or 0) == TEAM_AGENT_GUIDANCE_VERSION
        and guidance.get("guidance_path") == TEAM_AGENT_GUIDANCE_PATH
        and guidance.get("section_id") == TEAM_AGENT_GUIDANCE_SECTION_ID
        and guidance.get("agent_id") == identity["agent_id"]
        and guidance.get("display_name") == identity["display_name"]
        and guidance.get("seeded") is True
        and guidance.get("inactive") is True
        and guidance.get("inactive_reason") == "no_team_assignment"
    )


def _session_metadata(context: Any, *, session_id: str) -> dict[str, Any]:
    lookup = getattr(context, "session_lookup", None)
    read_all = getattr(lookup, "read_all_sessions", None)
    if not callable(read_all):
        return {}
    sessions = read_all()
    if not isinstance(sessions, Mapping):
        return {}
    session = sessions.get(session_id)
    if session is None:
        return {}
    metadata = None
    if isinstance(session, Mapping):
        metadata = session.get("metadata")
    else:
        metadata = getattr(session, "metadata", None)
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _mark_agent_guidance_seeded(
    context: Any,
    *,
    session_id: str,
    agent: Mapping[str, Any],
    copy_result: Mapping[str, Any],
    vm_ip: str | None,
) -> None:
    updater = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(updater, "session_update_metadata", None)
    if not callable(update_fn):
        return
    team_metadata: dict[str, Any] = {
        # Stable identity lets generic session consumers recognize Team-owned
        # sessions without caching mutable tasks, permissions, or runtime state.
        "agent_id": _required_text(agent.get("id"), "agent.id"),
        "team_id": _required_text(agent.get("team_id"), "agent.team_id"),
        "agent_guidance": {
            "seeded": True,
            "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
            **_agent_guidance_identity(agent),
            "inactive": False,
            "inactive_reason": None,
            "copied_paths": list(copy_result.get("copied_paths") or []),
        },
    }
    if vm_ip:
        team_metadata["last_known_vm_ip"] = vm_ip
    update_fn(
        target=session_id,
        metadata={"team_addon": team_metadata},
    )


def _mark_governance_guidance_seeded(
    context: Any,
    *,
    session_id: str,
    agent: Mapping[str, Any],
    copy_result: Mapping[str, Any],
    vm_ip: str | None,
    inactive_team_guidance: bool,
) -> None:
    updater = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(updater, "session_update_metadata", None)
    if not callable(update_fn):
        return
    team_metadata: dict[str, Any] = {
        "governance_agent_guidance": {
            "seeded": True,
            "guidance_version": TEAM_GOVERNANCE_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
            **_agent_guidance_identity(agent),
            "copied_paths": list(copy_result.get("copied_paths") or []),
        }
    }
    if inactive_team_guidance:
        team_metadata["agent_guidance"] = {
            "seeded": True,
            "guidance_version": TEAM_AGENT_GUIDANCE_VERSION,
            "guidance_path": TEAM_AGENT_GUIDANCE_PATH,
            "section_id": TEAM_AGENT_GUIDANCE_SECTION_ID,
            **_agent_guidance_identity(agent),
            "inactive": True,
            "inactive_reason": "no_team_assignment",
            "copied_paths": list(copy_result.get("copied_paths") or []),
        }
    if vm_ip:
        team_metadata["last_known_vm_ip"] = vm_ip
    update_fn(
        target=session_id,
        metadata={"team_addon": team_metadata},
    )


def _mark_project_repo_prepared(
    context: Any,
    *,
    session_id: str,
    project_repo: Mapping[str, Any],
) -> None:
    updater = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(updater, "session_update_metadata", None)
    if not callable(update_fn):
        return
    update_fn(
        target=session_id,
        metadata={"team_addon": {"project_repo": dict(project_repo)}},
    )


def _mark_project_repos_prepared(
    context: Any,
    *,
    session_id: str,
    project_repos: Sequence[Mapping[str, Any]],
) -> None:
    updater = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(updater, "session_update_metadata", None)
    if not callable(update_fn):
        return
    metadata: dict[str, Any] = {
        "project_repos": [dict(project_repo) for project_repo in project_repos],
    }
    if len(project_repos) == 1:
        metadata["project_repo"] = dict(project_repos[0])
    update_fn(
        target=session_id,
        metadata={"team_addon": metadata},
    )
