from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


class WorkflowTargetPreparationCoordinator:
    """Prepare external targets before workflow persistence.

    The existing Save operation owns its lock, revision checks, and validation.
    Provider-specific draft handling and target resolution stay behind this boundary.
    """

    def __init__(
        self,
        *,
        teams_admin: Callable[[], Any | None],
        normalize_graphs: Callable[..., Any],
    ) -> None:
        self._get_teams_admin = teams_admin
        self._normalize_graphs = normalize_graphs

    @property
    def _teams_admin(self) -> Any | None:
        return self._get_teams_admin()

    def prepare_for_save(
        self,
        *,
        config: dict[str, object],
        graphs: object,
        preflight: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        candidate, has_drafts = self.prepare_validation_config(config)
        if has_drafts:
            preflight(candidate)
        return self.resolve_config(config=config, graphs=graphs)

    def prepare_validation_config(
        self,
        config: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        candidate = json.loads(json.dumps(config))
        if not isinstance(candidate, dict):
            raise ValueError("Workflow config must be a JSON object.")
        triggers = candidate.get("triggers")
        if not isinstance(triggers, list):
            return candidate, False

        has_direct_chat_drafts = False
        for trigger in triggers:
            if not isinstance(trigger, dict):
                continue
            if str(trigger.get("type") or "").strip() != "trigger:teams:direct-message":
                continue
            trigger_config = trigger.get("config")
            if not isinstance(trigger_config, dict):
                continue
            mode = str(trigger_config.get("chat_target_mode") or "explicit").strip()
            if mode != "direct":
                continue

            display_name = str(
                trigger.get("display_name") or trigger.get("id") or "Teams direct message"
            )
            raw_upns = trigger_config.get("direct_chat_user_upns")
            if not isinstance(raw_upns, list):
                raise ValueError(f"{display_name}: direct chat users must be a list of UPNs.")
            user_upns = [str(value or "").strip() for value in raw_upns if str(value or "").strip()]
            if not user_upns:
                raise ValueError(f"{display_name}: add at least one direct chat user UPN.")

            has_direct_chat_drafts = True
            trigger_config["direct_chat_user_upns"] = user_upns
            trigger_config["sender_user_ids"] = ["pending-direct-chat-user"]
            trigger_config["targets"] = [{"target": "chat:pending-direct-chat"}]
        return candidate, has_direct_chat_drafts

    def restore_draft_fields(
        self,
        *,
        validated_config: dict[str, object],
        source_config: dict[str, object],
    ) -> dict[str, object]:
        candidate = json.loads(json.dumps(validated_config))
        if not isinstance(candidate, dict):
            raise ValueError("Validated workflow config must be a JSON object.")

        source_triggers = source_config.get("triggers")
        validated_triggers = candidate.get("triggers")
        if not isinstance(source_triggers, list) or not isinstance(validated_triggers, list):
            return candidate
        source_by_id = {
            str(trigger.get("id") or "").strip(): trigger
            for trigger in source_triggers
            if isinstance(trigger, dict) and str(trigger.get("id") or "").strip()
        }
        for trigger in validated_triggers:
            if not isinstance(trigger, dict):
                continue
            node_id = str(trigger.get("id") or "").strip()
            source_trigger = source_by_id.get(node_id)
            if not isinstance(source_trigger, dict):
                continue
            source_node_config = source_trigger.get("config")
            validated_node_config = trigger.get("config")
            if not isinstance(source_node_config, dict) or not isinstance(
                validated_node_config, dict
            ):
                continue
            mode = str(source_node_config.get("chat_target_mode") or "explicit").strip()
            if mode != "direct":
                continue

            raw_upns = source_node_config.get("direct_chat_user_upns")
            if isinstance(raw_upns, list):
                validated_node_config["direct_chat_user_upns"] = [
                    str(value or "").strip() for value in raw_upns if str(value or "").strip()
                ]
            else:
                validated_node_config["direct_chat_user_upns"] = []
            for field_name in ("sender_user_ids", "targets"):
                raw_value = source_node_config.get(field_name)
                validated_node_config[field_name] = (
                    json.loads(json.dumps(raw_value)) if isinstance(raw_value, list) else []
                )
        return candidate

    def resolve_config(
        self,
        *,
        config: dict[str, object],
        graphs: object,
    ) -> dict[str, object]:
        candidate = json.loads(json.dumps(config))
        if not isinstance(candidate, dict):
            raise ValueError("Workflow config must be a JSON object.")
        triggers = candidate.get("triggers")
        if not isinstance(triggers, list):
            return candidate

        normalized_graphs = self._normalize_graphs(
            config=candidate,
            graphs=graphs,
            strict=False,
        )
        directory_by_node_id = {
            node_id: graph.directory_path
            for graph in normalized_graphs
            for node_id in graph.node_ids
        }
        for trigger in triggers:
            if not isinstance(trigger, dict):
                continue
            if str(trigger.get("type") or "").strip() != "trigger:teams:direct-message":
                continue
            trigger_config = trigger.get("config")
            if not isinstance(trigger_config, dict):
                continue
            mode = str(trigger_config.get("chat_target_mode") or "explicit").strip()
            if mode != "direct":
                continue

            node_id = str(trigger.get("id") or "").strip()
            display_name = str(trigger.get("display_name") or node_id or "Teams direct message")
            raw_upns = trigger_config.get("direct_chat_user_upns")
            if not isinstance(raw_upns, list):
                raise ValueError(f"{display_name}: direct chat users must be a list of UPNs.")
            user_upns = [str(value or "").strip() for value in raw_upns if str(value or "").strip()]
            if not user_upns:
                raise ValueError(f"{display_name}: add at least one direct chat user UPN.")
            if self._teams_admin is None:
                raise ValueError("Teams direct-chat resolution is unavailable in this runtime.")

            resolutions = self._teams_admin.resolve_direct_chat_targets(
                directory=directory_by_node_id.get(node_id, "root"),
                user_refs=user_upns,
            )
            if not resolutions:
                raise ValueError(f"{display_name}: no Teams direct chats were resolved.")
            trigger_config["direct_chat_user_upns"] = [
                resolution["user_principal_name"] for resolution in resolutions
            ]
            trigger_config["sender_user_ids"] = [
                resolution["user_id"] for resolution in resolutions
            ]
            trigger_config["targets"] = [
                {"target": resolution["chat_target"]} for resolution in resolutions
            ]
        return candidate
