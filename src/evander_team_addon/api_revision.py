"""Api revision."""

from __future__ import annotations

import hashlib
import json
from collections.abc import (
    Mapping,
)
from typing import (
    Any,
)

from .api_constants import (
    TEAM_RESOURCE_TOKEN_COLLECTIONS,
)
from .models import (
    TeamState,
    project_assignments_for_agent,
)
from .services.admin_service import team_copy_configuration_token


def _trim_resource_tokens(payload: dict[str, Any], collection: str, keep_ids: set[str]) -> None:
    tokens = payload.get("resource_tokens")
    if not isinstance(tokens, dict):
        return
    collection_tokens = tokens.get(collection)
    if not isinstance(collection_tokens, Mapping):
        return
    tokens[collection] = {
        item_id: token for item_id, token in collection_tokens.items() if str(item_id) in keep_ids
    }


def _add_team_resource_tokens(payload: dict[str, Any], state: TeamState) -> None:
    tokens = _team_resource_tokens(state)
    payload["resource_tokens"] = tokens
    for collection, collection_tokens in tokens.items():
        rows = payload.get(collection)
        if not isinstance(rows, dict):
            continue
        for item_id, token in collection_tokens.items():
            row = rows.get(item_id)
            if isinstance(row, dict):
                row["resource_token"] = token


def _add_team_copy_configuration_tokens(payload: dict[str, Any], state: TeamState) -> None:
    teams = payload.get("teams")
    if not isinstance(teams, dict):
        return
    for team_id, team_payload in teams.items():
        if not isinstance(team_payload, dict):
            continue
        try:
            token = team_copy_configuration_token(state, str(team_id))
        except ValueError:
            continue
        team_payload["copy_configuration_token"] = token


def _add_team_resource_tokens_for_collections(
    payload: dict[str, Any],
    state: TeamState,
    *,
    collections: set[str],
    ids_by_collection: Mapping[str, set[str]],
) -> None:
    tokens = _team_resource_tokens_for_collections(
        state,
        collections,
        ids_by_collection=ids_by_collection,
    )
    payload["resource_tokens"] = tokens
    for collection, collection_tokens in tokens.items():
        rows = payload.get(collection)
        if not isinstance(rows, dict):
            continue
        for item_id, token in collection_tokens.items():
            row = rows.get(item_id)
            if isinstance(row, dict):
                row["resource_token"] = token


def _team_resource_tokens(state: TeamState) -> dict[str, dict[str, str]]:
    return {
        collection: {
            item_id: _team_resource_token(
                _team_resource_value_for_token(state, collection, item_id, item)
            )
            for item_id, item in _team_state_collection(state, collection).items()
        }
        for collection in TEAM_RESOURCE_TOKEN_COLLECTIONS
    }


def _team_resource_tokens_for_collections(
    state: TeamState,
    collections: set[str],
    *,
    ids_by_collection: Mapping[str, set[str]] | None = None,
) -> dict[str, dict[str, str]]:
    ids_by_collection = ids_by_collection or {}
    tokens: dict[str, dict[str, str]] = {}
    for collection in TEAM_RESOURCE_TOKEN_COLLECTIONS:
        if collection not in collections:
            continue
        allowed_ids = ids_by_collection.get(collection)
        rows = _team_state_collection(state, collection)
        tokens[collection] = {
            item_id: _team_resource_token(
                _team_resource_value_for_token(state, collection, item_id, item)
            )
            for item_id, item in rows.items()
            if allowed_ids is None or item_id in allowed_ids
        }
    return tokens


def _team_resource_value_for_token(
    state: TeamState,
    collection: str,
    item_id: str,
    item: Any,
) -> Any:
    if collection != "agents":
        return item
    runtime = state.agent_runtimes.get(item_id)
    return {
        "agent": item.model_dump(mode="json") if hasattr(item, "model_dump") else item,
        "project_assignments": [
            assignment.model_dump(mode="json")
            for assignment in project_assignments_for_agent(state, item_id)
        ],
        "direct_stand_downs": [
            stand_down.model_dump(mode="json")
            for stand_down in sorted(
                (
                    stand_down
                    for stand_down in state.stand_downs.values()
                    if stand_down.agent_id == item_id
                ),
                key=lambda stand_down: stand_down.id,
            )
        ],
        "runtime": (
            {
                "context_clearing_policy": getattr(
                    runtime.context_clearing_policy,
                    "value",
                    runtime.context_clearing_policy,
                ),
                "auto_context_clearing_enabled": runtime.auto_context_clearing_enabled,
                "context_clearing_idle_grace_seconds": (
                    runtime.context_clearing_idle_grace_seconds
                ),
            }
            if runtime is not None
            else None
        ),
    }


def _team_resource_token(value: Any) -> str:
    if hasattr(value, "model_dump"):
        serializable = value.model_dump(mode="json")
    else:
        serializable = value
    encoded = json.dumps(
        serializable,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _team_resource_token_for(
    state: TeamState,
    collection: str | None,
    item_id: str | None,
) -> str | None:
    if not collection or not item_id:
        return None
    item = _team_state_collection(state, collection).get(item_id)
    if item is None:
        return None
    return _team_resource_token(_team_resource_value_for_token(state, collection, item_id, item))


def _team_state_collection(state: TeamState, collection: str) -> Mapping[str, Any]:
    if collection not in TEAM_RESOURCE_TOKEN_COLLECTIONS:
        return {}
    value = getattr(state, collection, {})
    return value if isinstance(value, Mapping) else {}


def _can_rebase_ui_mutation(
    payload: Mapping[str, Any],
    current: TeamState,
    *,
    target_collection: str | None = None,
    target_id: str | None = None,
    create_collection: str | None = None,
    create_id: str | None = None,
) -> bool:
    if str(payload.get("conflict_policy") or "").strip() != "target_unchanged":
        return False
    if target_collection and target_id:
        base_token = str(payload.get("base_resource_token") or "").strip()
        if not base_token:
            return False
        return _team_resource_token_for(current, target_collection, target_id) == base_token
    if create_collection:
        return bool(create_id) and create_id not in _team_state_collection(
            current,
            create_collection,
        )
    return False
