from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter, ValidationError

from evander.admin.payloads import WorkflowEditorGraphMetadata
from evander.admin.services import vm_agent_service
from evander.events.models import (
    EdgeDefinition,
    EffectDefinitionDiscriminated,
    GateDefinitionDiscriminated,
    QueryDefinitionDiscriminated,
    TriggerDefinitionDiscriminated,
    materialize_node_definition,
)
from evander.models import ToolName
from evander.services import parse_teams_target
from evander.workflow.catalog import (
    WorkflowEditorFieldDefinition,
    WorkflowEditorFieldOption,
    WorkflowEditorNodeCatalogEntry,
    WorkflowEditorNodePortDefinition,
)
from evander.workflow.migrations import (
    AGENT_PROMPT_COMPLETED_PORT,
    AGENT_PROMPT_ERROR_PORT,
    AGENT_PROMPT_STARTED_PORT,
)
from evander.workflow.validation import (
    WorkflowValidationDiagnostic,
    validate_workflow_binding_diagnostics,
)
from evander.workflow.system_nodes import is_system_managed_workflow_node_id

_TRIGGER_DEFINITION_ADAPTER = TypeAdapter(TriggerDefinitionDiscriminated)
_GATE_DEFINITION_ADAPTER = TypeAdapter(GateDefinitionDiscriminated)
_EFFECT_DEFINITION_ADAPTER = TypeAdapter(EffectDefinitionDiscriminated)
_QUERY_DEFINITION_ADAPTER = TypeAdapter(QueryDefinitionDiscriminated)
_EDGE_DEFINITION_ADAPTER = TypeAdapter(EdgeDefinition)


def _strip_default_edge_fields(payload: dict[str, object]) -> dict[str, object]:
    edges = payload.get("edges")
    if not isinstance(edges, list):
        return payload
    normalized_edges: list[object] = []
    for entry in edges:
        if not isinstance(entry, dict):
            normalized_edges.append(entry)
            continue
        normalized = dict(entry)
        if normalized.get("source_port") is None:
            normalized.pop("source_port", None)
        if normalized.get("allow_cycle") is False:
            normalized.pop("allow_cycle", None)
        normalized_edges.append(normalized)
    payload["edges"] = normalized_edges
    return payload


def _workflow_binding_diagnostics_for_graphs(
    config: dict[str, object],
    graphs: list[WorkflowEditorGraphMetadata],
) -> list[WorkflowValidationDiagnostic]:
    if not graphs:
        return validate_workflow_binding_diagnostics(config)

    diagnostics: list[WorkflowValidationDiagnostic] = []
    seen: set[tuple[str, str | None, str | None, str | None, str | None, str]] = set()
    for graph in graphs:
        graph_node_ids = set(graph.node_ids)
        if not graph_node_ids:
            continue
        graph_config = _workflow_config_for_graph_node_ids(config, graph_node_ids)
        for diagnostic in validate_workflow_binding_diagnostics(graph_config):
            key = (
                diagnostic.code,
                diagnostic.node_id,
                diagnostic.node_type,
                diagnostic.field_path,
                diagnostic.reference,
                diagnostic.message,
            )
            if key in seen:
                continue
            seen.add(key)
            diagnostics.append(diagnostic)
    return diagnostics


def _workflow_agent_create_diagnostics(
    config: dict[str, object],
) -> list[WorkflowValidationDiagnostic]:
    effects = config.get("effects")
    if not isinstance(effects, list):
        return []

    diagnostics: list[WorkflowValidationDiagnostic] = []
    for effect in effects:
        if not isinstance(effect, dict) or effect.get("type") != "effect:agent:create":
            continue
        effect_config = effect.get("config")
        execution_backend_value = effect.get("execution_backend")
        if not execution_backend_value and isinstance(effect_config, dict):
            execution_backend_value = effect_config.get("execution_backend")
        execution_backend = str(execution_backend_value or "").strip()
        if execution_backend:
            continue
        diagnostics.append(
            WorkflowValidationDiagnostic(
                code="agent_create_missing_execution_backend",
                severity="error",
                node_id=str(effect.get("id") or "") or None,
                node_type="effect:agent:create",
                field_path="execution_backend",
                message="effect:agent:create requires an execution backend.",
            )
        )
    return diagnostics


def _workflow_config_for_graph_node_ids(
    config: dict[str, object], node_ids: set[str]
) -> dict[str, object]:
    graph_config = dict(config)
    for collection_name in ("triggers", "gates", "effects", "queries"):
        entries = config.get(collection_name)
        if not isinstance(entries, list):
            graph_config[collection_name] = []
            continue
        graph_config[collection_name] = [
            entry
            for entry in entries
            if isinstance(entry, dict) and str(entry.get("id") or "").strip() in node_ids
        ]
    edges = config.get("edges")
    if isinstance(edges, list):
        graph_config["edges"] = [
            edge
            for edge in edges
            if isinstance(edge, dict)
            and str(edge.get("source_id") or "").strip() in node_ids
            and str(edge.get("target_id") or "").strip() in node_ids
        ]
    else:
        graph_config["edges"] = []
    return graph_config


def _flatten_workflow_node_for_legacy_response(
    entry: dict[str, object], adapter: TypeAdapter
) -> dict[str, object]:
    try:
        materialized = materialize_node_definition(adapter.validate_python(entry))
        payload = materialized.model_dump(mode="json", exclude_none=True)
    except ValidationError:
        payload = dict(entry)
    config = payload.pop("config", None)
    if isinstance(config, dict):
        payload.update(config)
    return payload


def _workflow_config_for_legacy_mutation_response(config: dict[str, object]) -> dict[str, object]:
    response = json.loads(json.dumps(config))
    if not isinstance(response, dict):
        return config
    adapters = {
        "triggers": _TRIGGER_DEFINITION_ADAPTER,
        "gates": _GATE_DEFINITION_ADAPTER,
        "effects": _EFFECT_DEFINITION_ADAPTER,
        "queries": _QUERY_DEFINITION_ADAPTER,
    }
    for collection_name, adapter in adapters.items():
        entries = response.get(collection_name)
        if not isinstance(entries, list):
            continue
        response[collection_name] = [
            _flatten_workflow_node_for_legacy_response(entry, adapter)
            if isinstance(entry, dict)
            else entry
            for entry in entries
        ]
    return response


def _looks_like_declared_output_schema(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    return all(isinstance(entry, Mapping) for entry in value.values())


def _is_system_managed_workflow_node_id(node_id: str) -> bool:
    return is_system_managed_workflow_node_id(node_id)


def _field_option(
    value: str,
    label: str | None = None,
    *,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkflowEditorFieldOption:
    return WorkflowEditorFieldOption(
        value=value,
        label=label or value,
        description=description,
        metadata=metadata or {},
    )


def _communication_target_capabilities(target: dict[str, Any]) -> list[str]:
    policy = target.get("policy")
    raw_capabilities = policy.get("capabilities") if isinstance(policy, dict) else []
    if not isinstance(raw_capabilities, list):
        return []
    capabilities = {
        str(capability).strip() for capability in raw_capabilities if str(capability).strip()
    }
    return sorted(capabilities)


def _communication_target_shape(target: dict[str, Any]) -> str | None:
    provider = str(target.get("provider") or "").strip()
    address = str(target.get("address") or "").strip()
    if provider == "teams" and address:
        parsed_target = parse_teams_target(address)
        return f"teams_{parsed_target.kind}"
    if provider == "discord" and address:
        return "discord_channel"
    if provider == "email" and address:
        return "email_recipient"
    return None


def _communication_target_option_metadata(
    target: dict[str, Any], *, purpose: str
) -> dict[str, Any] | None:
    target_id = str(target.get("id") or "").strip()
    provider = str(target.get("provider") or "").strip()
    address = str(target.get("address") or "").strip()
    if not target_id or not provider or not address:
        return None

    capabilities = _communication_target_capabilities(target)
    target_shape = _communication_target_shape(target)
    if target_shape is None:
        return None

    metadata: dict[str, Any] = {
        "provider": provider,
        "address": address,
        "target_shape": target_shape,
        "policy_capabilities": capabilities,
    }
    if provider == "teams":
        parsed_target = parse_teams_target(address)
        metadata["teams_target_kind"] = parsed_target.kind
        metadata["teams_target_normalized"] = parsed_target.normalized
    if purpose == "read-message":
        metadata["required_capability"] = "read"
    elif purpose == "team-chat-bridge":
        metadata["required_capabilities"] = ["read", "write or reply"]
        metadata["thread_id"] = {
            "top_level": {
                "supported": "write" in capabilities,
                "requires_capability": "write",
            },
            "reply": {
                "supported": provider in {"teams", "email"} and "reply" in capabilities,
                "requires_capability": "reply",
            },
        }
    elif purpose == "agent-session":
        metadata["supported_capabilities"] = ["read", "write", "reply"]
        if provider == "email":
            metadata["required_capabilities"] = ["write"]
    elif purpose == "send-message":
        metadata["thread_id"] = {
            "top_level": {
                "supported": "write" in capabilities,
                "requires_capability": "write",
            },
            "reply": {
                "supported": provider == "teams"
                and target_shape == "teams_channel"
                and "reply" in capabilities,
                "requires_capability": "reply",
                "supported_target_shapes": ["teams_channel"],
                "unsupported_target_shapes": [
                    "teams_chat",
                    "teams_user",
                    "discord_channel",
                    "email_recipient",
                ],
            },
        }
    elif purpose == "notification":
        metadata["thread_id"] = {
            "top_level": {
                "supported": "notify" in capabilities,
                "requires_capability": "notify",
            },
            "reply_hint": {
                "supported": provider == "teams" and target_shape == "teams_channel",
                "supported_target_shapes": ["teams_channel"],
                "unsupported_target_shapes": [
                    "teams_chat",
                    "teams_user",
                    "discord_channel",
                    "email_recipient",
                ],
            },
        }
    else:
        return None
    return metadata


def _communication_target_option(
    target: dict[str, Any], *, purpose: str
) -> WorkflowEditorFieldOption | None:
    target_id = str(target.get("id") or "").strip()
    provider = str(target.get("provider") or "").strip()
    metadata = _communication_target_option_metadata(target, purpose=purpose)
    if not target_id or not provider or metadata is None:
        return None

    capabilities = metadata["policy_capabilities"]
    capability_suffix = f", {', '.join(capabilities)}" if capabilities else ""
    return _field_option(
        target_id,
        f"{target_id} ({provider}{capability_suffix})",
        metadata=metadata,
    )


def _tool_field_options(
    addon_tools: list[Mapping[str, str]] | None = None,
) -> list[WorkflowEditorFieldOption]:
    options = [
        _field_option(tool.value, tool.value.replace("_", " "))
        for tool in sorted(ToolName, key=lambda item: item.value)
    ]
    for tool in addon_tools or []:
        options.append(
            _field_option(
                tool["value"],
                tool["label"],
                description=tool.get("description"),
                metadata={
                    "tool_group": tool["group"],
                    "provider": tool["provider"],
                    "addon_id": tool["addon_id"],
                },
            )
        )
    return options


def _port(
    key: str, label: str, *, description: str | None = None
) -> WorkflowEditorNodePortDefinition:
    return WorkflowEditorNodePortDefinition(key=key, label=label, description=description)


def _field(
    key: str,
    label: str,
    kind: str,
    *,
    required: bool = False,
    bindable: bool = False,
    read_only: bool = False,
    multiline: bool = False,
    description: str | None = None,
    placeholder: str | None = None,
    json_value_type: str | None = None,
    default_value: Any = None,
    options: list[WorkflowEditorFieldOption] | None = None,
    item_fields: list[WorkflowEditorFieldDefinition] | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkflowEditorFieldDefinition:
    return WorkflowEditorFieldDefinition(
        key=key,
        label=label,
        kind=kind,
        required=required,
        bindable=bindable,
        read_only=read_only,
        multiline=multiline,
        description=description,
        placeholder=placeholder,
        json_value_type=json_value_type,
        default_value=default_value,
        options=options or [],
        item_fields=item_fields or [],
        metadata=metadata or {},
    )


def _pull_request_trigger_fields(
    *, include_agent_identity_fields: bool = False
) -> list[WorkflowEditorFieldDefinition]:
    fields = [
        _field(
            "provider",
            "Provider",
            "enum",
            required=True,
            options=[
                _field_option("local", "Local"),
                _field_option("github", "GitHub"),
                _field_option("ado", "Azure DevOps"),
            ],
        ),
        _field(
            "repo",
            "Repo",
            "string",
            description=(
                "Set a repo to enable explicit Azure DevOps PR polling. Leave blank to use "
                "legacy pr-map compatibility."
            ),
        ),
        _field("directory_path", "Directory path", "string"),
        _field("ado_organization", "ADO organization", "string"),
        _field("project", "Project", "string"),
        _field("status", "Status", "string"),
        _field("creator_id", "Creator ID", "string"),
        _field("reviewer_id", "Reviewer ID", "string"),
        _field("source_ref_name", "Source ref", "string"),
        _field("target_ref_name", "Target ref", "string"),
        _field("source_repository_id", "Source repository ID", "string"),
        _field("repository_id", "Repository ID", "string"),
        _field("min_time", "Min time", "string"),
        _field("max_time", "Max time", "string"),
        _field("query_time_range_type", "Time range type", "string"),
        _field("include_links", "Include links", "boolean"),
        _field("max_comment_length", "Max comment length", "number"),
        _field(
            "tags",
            "Tags",
            "string",
            description="Tag expression with and, or, not, and parentheses.",
            placeholder="(TagA or TagB) and not TagC",
        ),
        _field("top", "Top", "number"),
        _field("skip", "Skip", "number"),
        _field("cursor", "Cursor", "string"),
    ]
    if include_agent_identity_fields:
        fields.extend(
            [
                _field(
                    "agent_author_names",
                    "Agent author names",
                    "string_list",
                    description=(
                        "Feedback author display names that should mark a PR feedback event "
                        "as agent-directed."
                    ),
                ),
                _field(
                    "agent_mention_names",
                    "Agent mention names",
                    "string_list",
                    description=(
                        "Mention display names that should mark a PR feedback event as agent-directed."
                    ),
                ),
                _field(
                    "agent_author_ids",
                    "Agent author IDs",
                    "string_list",
                    description=(
                        "Feedback author identity IDs that should mark same-thread PR feedback "
                        "as agent-directed."
                    ),
                ),
                _field(
                    "agent_mention_ids",
                    "Agent mention IDs",
                    "string_list",
                    description=(
                        "Mention identity IDs that should mark a PR feedback event as agent-directed."
                    ),
                ),
                _field(
                    "suppress_agent_authored_feedback",
                    "Suppress agent-authored feedback",
                    "boolean",
                    description=(
                        "When enabled, PR feedback authored by the configured agent identity is "
                        "stored but not published as a workflow event."
                    ),
                ),
            ]
        )
    return fields


def _remote_issue_provider_field(*, bindable: bool = False) -> WorkflowEditorFieldDefinition:
    return _field(
        "provider",
        "Provider",
        "enum",
        required=True,
        bindable=bindable,
        options=[
            _field_option("azure_devops", "Azure DevOps"),
            _field_option("github", "GitHub"),
        ],
        description="Selects the remote issue provider and which provider-specific fields apply.",
    )


def _ado_query_mode_field(*, label: str = "Query mode") -> WorkflowEditorFieldDefinition:
    return _field(
        "query_mode",
        label,
        "enum",
        default_value="structured_filters",
        options=[
            _field_option("structured_filters", "Structured filters"),
            _field_option("manual_wiql", "Manual WIQL"),
        ],
        description=(
            "Structured filters uses the filter fields. Manual WIQL uses only Project, WIQL, "
            "Top, and Skip."
        ),
    )


def _remote_issue_base_fields(*, bindable: bool = False) -> list[WorkflowEditorFieldDefinition]:
    return [
        _remote_issue_provider_field(bindable=bindable),
        _field("directory_path", "Directory path", "string", bindable=bindable),
        _field(
            "project",
            "ADO project",
            "string",
            bindable=bindable,
            description="Azure DevOps project. Ignored for GitHub unless repo is omitted.",
        ),
        _field(
            "repo",
            "GitHub repo",
            "string",
            bindable=bindable,
            placeholder="owner/name",
            description="GitHub repository in owner/name form.",
        ),
        _ado_query_mode_field(label="ADO query mode"),
        _field(
            "wiql",
            "ADO WIQL",
            "string",
            bindable=bindable,
            multiline=True,
            description="Azure DevOps Manual WIQL mode only. Structured filter fields are not applied.",
        ),
        _field(
            "current_iteration",
            "ADO current iteration",
            "boolean",
            bindable=bindable,
            description="Azure DevOps only. Resolve the team's current iteration before querying.",
        ),
        _field("team", "ADO team", "string", bindable=bindable),
        _field("iteration", "ADO iteration path", "string", bindable=bindable),
        _field("area_path", "ADO area path", "string", bindable=bindable),
        _field(
            "states",
            "ADO states",
            "string_list",
            bindable=bindable,
            description="Azure DevOps Structured filters mode only.",
        ),
        _field(
            "types",
            "ADO types",
            "string_list",
            bindable=bindable,
            description="Azure DevOps Structured filters mode only.",
        ),
        _field(
            "tags_any",
            "ADO tags any",
            "string_list",
            bindable=bindable,
            description="Azure DevOps Structured filters mode only.",
        ),
        _field(
            "state",
            "GitHub state",
            "string",
            bindable=bindable,
            placeholder="open",
            description="GitHub issue state such as open, closed, or all.",
        ),
        _field("labels_any", "GitHub labels any", "string_list", bindable=bindable),
        _field("labels_all", "GitHub labels all", "string_list", bindable=bindable),
        _field("assignee", "GitHub assignee", "string", bindable=bindable),
        _field("author", "GitHub author", "string", bindable=bindable),
        _field("mentioned", "GitHub mentioned", "string", bindable=bindable),
        _field("milestone", "GitHub milestone", "string", bindable=bindable),
        _field("since", "GitHub since", "string", bindable=bindable),
        _field("text_search", "Text search", "string", bindable=bindable),
        _field("top", "Top", "number", bindable=bindable),
    ]


def _remote_issue_comment_fields() -> list[WorkflowEditorFieldDefinition]:
    return [
        _field(
            "comment_contains",
            "Comment contains",
            "string",
            description="Only emit comments whose body contains this text.",
        ),
        _field(
            "comment_contains_any",
            "Comment contains any",
            "string_list",
            default_value=[],
            description="Only emit comments whose body contains any listed text.",
        ),
        _field(
            "comment_author_exclude_names",
            "Comment author exclude names",
            "string_list",
            default_value=[],
        ),
        _field(
            "comment_author_exclude_ids",
            "Comment author exclude IDs",
            "string_list",
            default_value=[],
        ),
        _field("comment_case_sensitive", "Comment case sensitive", "boolean", default_value=False),
        _field("include_existing", "Include existing", "boolean", default_value=False),
    ]


_WORKFLOW_EDITOR_NODE_DESCRIPTIONS: dict[str, str] = {
    "trigger:cron": "Start a workflow on a recurring cron schedule.",
    "trigger:event": "Start when an internal workflow event is emitted.",
    "trigger:http:webhook": "Start from a signed HTTP webhook delivered to a public endpoint.",
    "trigger:ticket:created": "Start when a tracked ticket is created.",
    "trigger:ticket:changed": "Start when a tracked ticket changes state or metadata.",
    "trigger:issue:comment-created": "Start when a tracked issue receives a new comment.",
    "trigger:ado:work-item-matching": (
        "Start when an Azure DevOps work item first matches or advances its changed date."
    ),
    "trigger:ado:work-item-comment-created": (
        "Start when a matching Azure DevOps work item receives a new matching comment."
    ),
    "trigger:remote-issue-matching": (
        "Start when a provider remote issue first matches or advances its update marker."
    ),
    "trigger:remote-issue-comment-created": (
        "Start when a matching provider remote issue receives a new matching comment."
    ),
    "trigger:script-run-request": "Start from an approved run_script request submitted by a session.",
    "trigger:host-directory:file-added": (
        "Start when a new regular file becomes stable in a configured host directory."
    ),
    "trigger:discord:new-message": (
        "Start from new Discord messages that match the configured filters."
    ),
    "trigger:communication:new-message": (
        "Start from new Teams, Discord, or email messages on declared communication targets."
    ),
    "trigger:teams:new-message": (
        "Start from new Microsoft Teams messages that match the configured targets."
    ),
    "trigger:teams:direct-message": (
        "Start from allowlisted one-on-one Microsoft Teams direct messages."
    ),
    "trigger:email:new-message": ("Start from new messages in configured email mailboxes."),
    "trigger:pull-request:new-feedback": (
        "Start when new pull-request feedback arrives from the configured provider."
    ),
    "trigger:pull-request:completed": (
        "Start when a tracked pull request reaches a terminal state."
    ),
    "trigger:ci:run:completed": (
        "Start when a CI run completes on GitHub Actions or Azure DevOps."
    ),
    "trigger:review:new-request": "Start when a review request is queued for orchestration.",
    "trigger:approval:pending-created": "Start when a pending approval request is created.",
    "trigger:git:push": "Start from Git push events captured by the host-side bare-repo hook.",
    "trigger:agent:run:completed": "Start when an agent run completion event is recorded.",
    "gate:filter": "Allow only matching events to continue downstream.",
    "gate:switch": "Route each activation to the first matching branch or a default route.",
    "gate:debounce": "Wait for a quiet period, then forward the latest activation.",
    "gate:buffer": "Collect activations by key and release them as a batch by count or timeout.",
    "gate:map": "Project upstream outputs into a new payload shape.",
    "gate:aggregate": "Aggregate numeric fields from an array into flat summary outputs.",
    "gate:deserialize": (
        "Parse a JSON or YAML string into object properties for downstream bindings."
    ),
    "gate:serialize": "Serialize a bindable value as JSON or YAML text for downstream nodes.",
    "gate:dedupe": "Suppress duplicate activations for the same key until a TTL expires.",
    "gate:merge": "Merge compatible inbound event streams into one downstream path.",
    "gate:await-agent": "Pause the workflow until a selected agent session completes.",
    "gate:for": "Iterate over an array and run the body branch for each item.",
    "gate:while": "Repeat a body branch while a bounded predicate remains true.",
    "effect:vm:create": "Create a dedicated libvirt VM and session record.",
    "effect:vm:clone-repo": "Clone a repository into a VM-backed agent workspace.",
    "effect:vm:bootstrap-python": "Bootstrap a Python checkout and environment in a VM.",
    "effect:vm:bootstrap-frontend": "Bootstrap frontend dependencies in a VM.",
    "effect:vm:start": "Start an existing VM-backed agent target.",
    "effect:vm:stop": "Stop an existing VM-backed agent target.",
    "effect:agent:create": "Create an agent session using the selected execution backend.",
    "effect:agent:destroy": "Destroy an agent session and its backend resources.",
    "effect:agent:clone-repo": "Clone a repository into an agent session workspace.",
    "effect:agent:prepare-reference-repositories": (
        "Refresh and prepare exact read-only repository snapshots for an assistant."
    ),
    "effect:agent:grant-repo-access": "Grant exact-repository Git access to an agent.",
    "effect:agent:revoke-repo-access": "Revoke one agent repository grant.",
    "effect:agent:bootstrap-frontend": "Bootstrap frontend dependencies for an agent session.",
    "effect:agent:copy-files": "Copy rendered file content into an agent session workspace.",
    "effect:agent:prompt": "Dispatch a prompt to an agent session and wait for completion.",
    "effect:agent:enqueue-prompt": "Queue a prompt for asynchronous agent-session execution.",
    "effect:agent:exec": "Run a shell command inside an agent session workspace.",
    "effect:semantic:ensure-collection": (
        "Ensure a host-side semantic collection exists with the configured vector schema."
    ),
    "effect:semantic:upsert": "Embed one text string and upsert it as one Qdrant point.",
    "effect:semantic:delete": "Delete semantic points by explicit point IDs.",
    "effect:state:update": "Set, patch, or delete graph-local workflow state.",
    "effect:http:request": (
        "Perform a host-side raw HTTP GET, POST, or PUT request with URL allowlist control."
    ),
    "effect:runtime:diagnostics-snapshot": (
        "Capture a bounded, redacted host runtime diagnostics snapshot from allowlisted sources."
    ),
    "effect:vm:exec": "Run a shell command inside a VM-backed agent target.",
    "effect:vm:copy-files": "Copy rendered file content into a VM-backed agent target.",
    "effect:vm:delete-files": "Delete files from a VM-backed agent target.",
    "effect:issue:update-state": "Update the workflow state for a tracked issue.",
    "effect:issue:update-metadata": "Patch metadata on a tracked issue.",
    "effect:issue:upsert-ado-proxy": (
        "Create or refresh local proxy tickets from Azure DevOps work items."
    ),
    "effect:ado:pipeline-run": (
        "Queue an Azure DevOps pipeline run and wait for its terminal result."
    ),
    "effect:ado:update-work-item": (
        "Update an Azure DevOps work item with an optional expected-state guard."
    ),
    "effect:ado:add-work-item-tag": (
        "Add a tag to an Azure DevOps work item when it is not already present."
    ),
    "effect:ado:remove-work-item-tag": (
        "Remove a tag from an Azure DevOps work item when it is present."
    ),
    "effect:ado:post-work-item-comment": (
        "Post an HTML-formatted comment to an Azure DevOps work item."
    ),
    "effect:pull-request:update": (
        "Update pull request metadata or tags with an optional status guard."
    ),
    "effect:pull-request:add-tag": "Add a tag to a pull request when it is not already present.",
    "effect:pull-request:remove-tag": "Remove a tag from a pull request when it is present.",
    "effect:event:emit": "Emit an internal workflow event for other graphs.",
    "effect:communication:send-message": (
        "Send a message through a directory-scoped communication target."
    ),
    "effect:notification:send": (
        "Send a notification through a directory-scoped communication target."
    ),
    "effect:session:update-metadata": "Patch metadata on a host-side session record.",
    "effect:session:approve-mcp-tools-for-run": (
        "Approve MCP tools for one agent session during the current workflow run."
    ),
    "effect:session:revoke-mcp-tool-approval-lease": (
        "Revoke a workflow-run-scoped MCP tool approval lease for one agent session."
    ),
    "effect:session:renew-if-expired": ("Renew an expired or soon-expiring session."),
    "effect:artifact:stage": "Copy a file or directory from /home/agent into the session artifacts folder.",
    "effect:artifact:publish-artifacts": (
        "Compress and publish the session artifacts folder to the durable artifact store."
    ),
    "effect:artifact:import-published": (
        "Import a published artifact archive into a session artifacts folder."
    ),
    "effect:git:promote-agent-branch": (
        "Promote a pinned agent commit into a host-owned managed repository ref."
    ),
    "effect:git:push-branch": (
        "Publish a pinned managed repository ref to an upstream branch with a lease."
    ),
    "effect:pr:create": "Create a pull request from an approved managed repository ref.",
    "effect:event:schedule": "Schedule a one-shot internal workflow event for later delivery.",
    "query:vm:run-state": "Read the current run state for a VM-backed agent target.",
    "query:git:pr-note": (
        "Read pull-request note context for an exact commit in a managed repository."
    ),
    "query:pull-request:list": "List provider pull requests with optional status, branch, time, or tag filters.",
    "query:pull-request:feedback": "Fetch provider pull-request feedback without owning cursor state.",
    "query:communication:messages": (
        "Read messages from a read-capable Teams or Discord communication target."
    ),
    "query:pull-request:get": "Get a provider pull request by id without owning cursor state.",
    "query:pull-request:work-items": (
        "List Azure DevOps work items linked to a pull request, including normalized assignees."
    ),
    "query:session-context": "Read session context and metadata.",
    "query:state:get": "Read graph-local workflow state for the current persisted workflow graph.",
    "query:session:list": "List sessions by generic manifest metadata.",
    "query:model-usage:list": (
        "List persisted model usage events with optional query-time OpenAI cost estimates."
    ),
    "query:agent:artifact-text": "Read a UTF-8 text file from an agent session artifacts folder.",
    "query:issue:get": "Read a local issue record by id.",
    "query:issue:list": "List tracked local and proxy issues from the local issue store.",
    "query:ado:work-items": "Query Azure DevOps work items.",
    "query:remote-issues": "Query Azure DevOps work items or GitHub issues.",
    "query:semantic:store-status": "Read Qdrant health and semantic collection status.",
    "query:semantic:search": "Search a semantic collection with an embedded text query.",
    "query:semantic:get": "Retrieve semantic points by explicit point IDs.",
}

_VM_ARTIFACT_TEXT_READ_COMMAND = vm_agent_service._VM_ARTIFACT_TEXT_READ_COMMAND


_WORKFLOW_EDITOR_NODE_CATALOG: tuple[WorkflowEditorNodeCatalogEntry, ...] = (
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:cron",
        label="Cron",
        fields=[
            _field(
                "expression", "Cron expression", "string", required=True, placeholder="*/15 * * * *"
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:event",
        label="Internal event",
        fields=[
            _field(
                "event_type",
                "Event type",
                "string",
                required=True,
                description=(
                    "Durable internal workflow event name. Known stable producer events appear "
                    "as suggestions; external ingress events use external.<name>."
                ),
            ),
            _field("correlation_key", "Correlation key", "string"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:http:webhook",
        label="Webhook",
        fields=[
            _field(
                "slug",
                "Slug",
                "string",
                required=True,
                description="Public webhook path segment. Must match [A-Za-z0-9_.-]+.",
                placeholder="build-notify",
            ),
            _field(
                "secret_env",
                "Secret env",
                "string",
                required=True,
                description="Environment variable that stores the shared webhook HMAC secret.",
                placeholder="WEBHOOK_SECRET",
            ),
            _field(
                "event_type",
                "Event type",
                "string",
                description="Defaults to webhook.<slug> when omitted.",
                placeholder="webhook.build-notify",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:ticket:created",
        label="Ticket created",
        fields=[
            _field("directories", "Directories", "string_list", required=True, placeholder="root"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:ticket:changed",
        label="Ticket changed",
        fields=[
            _field("directories", "Directories", "string_list", required=True, placeholder="root"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:issue:comment-created",
        label="Issue comment created",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                options=[
                    _field_option("ado", "Azure DevOps"),
                ],
            ),
            _field("organization_url", "Organization URL", "string"),
            _field("project", "Project", "string"),
            _field("directories", "Directories", "string_list", placeholder="root"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:ado:work-item-matching",
        label="ADO work item matching",
        fields=[
            _field("directory_path", "Directory path", "string"),
            _field("project", "Project", "string"),
            _ado_query_mode_field(),
            _field(
                "wiql",
                "WIQL",
                "string",
                multiline=True,
                description="Manual WIQL mode only. Structured filter fields are not applied.",
            ),
            _field(
                "current_iteration",
                "Current iteration",
                "boolean",
                description="Structured filters mode only. Resolve the team's current iteration before querying.",
            ),
            _field("team", "Team", "string"),
            _field("iteration", "Iteration path", "string"),
            _field("area_path", "Area path", "string"),
            _field("states", "States", "string_list", description="Structured filters mode only."),
            _field("types", "Types", "string_list", description="Structured filters mode only."),
            _field(
                "tags_any", "Tags any", "string_list", description="Structured filters mode only."
            ),
            _field(
                "text_search", "Text search", "string", description="Structured filters mode only."
            ),
            _field("top", "Top", "number"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:ado:work-item-comment-created",
        label="ADO work item comment",
        fields=[
            _field("directory_path", "Directory path", "string"),
            _field("project", "Project", "string"),
            _ado_query_mode_field(),
            _field(
                "wiql",
                "WIQL",
                "string",
                multiline=True,
                description="Manual WIQL mode only. Structured filter fields are not applied.",
            ),
            _field(
                "current_iteration",
                "Current iteration",
                "boolean",
                default_value=False,
                description="Structured filters mode only. Resolve the team's current iteration before querying.",
            ),
            _field("team", "Team", "string"),
            _field("iteration", "Iteration path", "string"),
            _field("area_path", "Area path", "string"),
            _field(
                "states",
                "States",
                "string_list",
                default_value=[],
                description="Structured filters mode only.",
            ),
            _field(
                "types",
                "Types",
                "string_list",
                default_value=[],
                description="Structured filters mode only.",
            ),
            _field(
                "tags_any",
                "Tags any",
                "string_list",
                default_value=[],
                description="Structured filters mode only.",
            ),
            _field(
                "text_search", "Text search", "string", description="Structured filters mode only."
            ),
            _field("top", "Top", "number", default_value=50),
            _field(
                "comment_contains",
                "Comment contains",
                "string",
                description="Only emit comments whose body contains this text.",
            ),
            _field(
                "comment_contains_any",
                "Comment contains any",
                "string_list",
                default_value=[],
                description="Only emit comments whose body contains any listed text.",
            ),
            _field(
                "comment_author_exclude_names",
                "Comment author exclude names",
                "string_list",
                default_value=[],
                description="Comment author display names to suppress.",
            ),
            _field(
                "comment_author_exclude_ids",
                "Comment author exclude IDs",
                "string_list",
                default_value=[],
                description="Comment author identity IDs to suppress.",
            ),
            _field(
                "comment_case_sensitive",
                "Comment case sensitive",
                "boolean",
                default_value=False,
                description="Apply comment text filters with case-sensitive matching.",
            ),
            _field(
                "include_existing",
                "Include existing",
                "boolean",
                default_value=False,
                description="Emit existing comments on the first poll instead of baselining them.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:remote-issue-matching",
        label="Remote issue matching",
        fields=_remote_issue_base_fields(),
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:remote-issue-comment-created",
        label="Remote issue comment",
        fields=[*_remote_issue_base_fields(), *_remote_issue_comment_fields()],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:script-run-request",
        label="Script run request",
        fields=[],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:host-directory:file-added",
        label="Host directory file added",
        fields=[
            _field(
                "directory_id",
                "Host directory",
                "enum",
                required=True,
                description="Directory-scoped host directory resource to watch.",
            ),
            _field(
                "path",
                "Relative path",
                "string",
                default_value="",
                description="Optional directory path relative to the configured host root.",
            ),
            _field(
                "recursive",
                "Recursive",
                "boolean",
                default_value=False,
                description="Watch regular files in nested directories.",
            ),
            _field(
                "include_globs",
                "Include globs",
                "string_list",
                default_value=[],
                description="Optional relative-path patterns; leave empty to include all files.",
            ),
            _field(
                "exclude_globs",
                "Exclude globs",
                "string_list",
                default_value=[],
                description="Relative-path patterns to exclude after include filtering.",
            ),
            _field(
                "settle_seconds",
                "Settle seconds",
                "number",
                default_value=5,
                description=(
                    "Require unchanged identity, size, and modification time across polls "
                    "for at least this duration."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:discord:new-message",
        label="Discord message",
        fields=[
            _field("discord_bot_token_env", "Bot token env var", "string", required=True),
            _field("channel_ids", "Channel IDs", "string_list"),
            _field("user_ids", "User IDs", "string_list"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:teams:new-message",
        label="Teams message",
        fields=[
            _field(
                "targets",
                "Targets",
                "object_list",
                required=True,
                description="Teams targets with optional per-target message filters.",
                item_fields=[
                    _field(
                        "target",
                        "Target",
                        "string",
                        required=True,
                        placeholder="team-id/channel-id or chat:<chat-id>",
                    ),
                    _field(
                        "message_filter",
                        "Message filter",
                        "json",
                        description='Optional per-target wake filter, for example {"mentions":{"user_ids":["aad-user-id"]}} or {"content":{"contains":"literal text","case_sensitive":false}}.',
                    ),
                ],
            ),
            _field("user_ids", "User IDs", "string_list"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:communication:new-message",
        label="Communication message",
        fields=[
            _field(
                "communication_target_ids",
                "Communication targets",
                "string_list",
                required=True,
                description=(
                    "Directory-scoped Teams channel/chat or Discord channel targets with "
                    "read capability. The trigger listens only to explicitly selected targets."
                ),
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                placeholder="root",
                default_value="root",
            ),
            _field(
                "author_ids",
                "Author IDs",
                "string_list",
                description="Optional provider-native author/user IDs to allow.",
            ),
            _field(
                "content_contains",
                "Content contains",
                "string",
                description="Optional substring filter applied to message content.",
            ),
            _field(
                "content_starts_with",
                "Content starts with",
                "string",
                description="Optional prefix filter applied to message content.",
            ),
            _field(
                "case_sensitive",
                "Case sensitive",
                "boolean",
                default_value=False,
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:teams:direct-message",
        label="Teams direct message",
        fields=[
            _field(
                "targets",
                "Chat targets",
                "object_list",
                required=True,
                description=(
                    "One-on-one Teams chat targets allowed to wake this workflow, with optional "
                    "per-target message filters."
                ),
                item_fields=[
                    _field(
                        "target",
                        "Target",
                        "string",
                        required=True,
                        placeholder="chat:<chat-id>",
                    ),
                    _field(
                        "message_filter",
                        "Message filter",
                        "json",
                        description=(
                            'Optional per-target wake filter, for example '
                            '{"content":{"contains":"literal text","case_sensitive":false}}.'
                        ),
                    ),
                ],
            ),
            _field(
                "sender_user_ids",
                "Sender user IDs or emails",
                "string_list",
                required=True,
                description=(
                    "Microsoft Graph user IDs, UPNs, or mail addresses allowed to wake this "
                    "workflow from the configured one-on-one Teams chats."
                ),
            ),
            _field(
                "message_filter",
                "Message filter",
                "json",
                description=(
                    "Optional wake filter, for example "
                    '{"content":{"contains":"literal text","case_sensitive":false}}.'
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:email:new-message",
        label="Email message",
        fields=[
            _field("mailbox_ids", "Mailbox IDs", "string_list", required=True),
            _field("folder", "Folder", "string"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:pull-request:new-feedback",
        label="PR feedback",
        fields=_pull_request_trigger_fields(include_agent_identity_fields=True),
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:pull-request:completed",
        label="PR completed",
        fields=_pull_request_trigger_fields(),
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:ci:run:completed",
        label="CI run completed",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                options=[
                    _field_option("github_actions", "GitHub Actions"),
                    _field_option("ado_pipelines", "Azure DevOps Pipelines"),
                ],
                description="Select the CI provider that publishes completed runs.",
            ),
            _field(
                "repo",
                "Repo",
                "string",
                description=(
                    "Repository owner/name for GitHub Actions. Optional for Azure DevOps when "
                    "the directory provider config can resolve it."
                ),
            ),
            _field(
                "workflow",
                "Workflow",
                "string",
                description="Optional GitHub workflow name, file name, or workflow id.",
            ),
            _field(
                "ado_organization",
                "ADO organization",
                "string",
                description=(
                    "Optional Azure DevOps organization identifier. Directory provider config can "
                    "supply it when omitted."
                ),
            ),
            _field(
                "project",
                "Project",
                "string",
                description=(
                    "Optional Azure DevOps project. Directory provider config can supply it "
                    "when omitted."
                ),
            ),
            _field(
                "pipeline_id",
                "Pipeline ID",
                "number",
                description="Optional Azure DevOps pipeline definition id.",
            ),
            _field(
                "pipeline_name",
                "Pipeline name",
                "string",
                description="Optional Azure DevOps pipeline definition name.",
            ),
            _field(
                "branches",
                "Branches",
                "string_list",
                description="Branch names to include. Leave empty to match any branch.",
            ),
            _field(
                "conclusions",
                "Conclusions",
                "string_list",
                description=(
                    "Terminal outcomes to include. GitHub and Azure DevOps spellings are "
                    "normalized to success, failure, cancelled, timed_out, and skipped."
                ),
            ),
            _field(
                "top",
                "Top",
                "number",
                description="Maximum number of completed runs to fetch per poll.",
            ),
            _field(
                "include_existing",
                "Include existing",
                "boolean",
                description="Emit historical runs on the first poll instead of suppressing them.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:review:new-request",
        label="Review request",
        fields=[],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:approval:pending-created",
        label="Pending approval",
        fields=[
            _field(
                "directory_path",
                "Directory path",
                "string",
                placeholder="root",
                default_value="root",
            ),
            _field(
                "action_types",
                "Action types",
                "string_list",
                default_value=[],
                description=(
                    "Optional action type filter. Leave blank to include all pending approvals."
                ),
            ),
            _field(
                "tool_names",
                "Tool names",
                "string_list",
                default_value=[],
                description=(
                    "Optional tool name filter. Leave blank to include all pending approvals."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:git:push",
        label="Git push",
        fields=[
            _field("managed_repo_id", "Managed repository ID", "string", bindable=True),
            _field(
                "managed_repo_id_glob",
                "Managed repository ID glob",
                "string",
                bindable=True,
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:agent:run:completed",
        label="Agent run completed",
        fields=[
            _field("vm_user", "VM user", "string"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:filter",
        label="Filter",
        fields=[
            _field(
                "match",
                "Match",
                "json",
                bindable=True,
                description="Recursive subset match against the immediate incoming payload. Mutually exclusive with When.",
            ),
            _field(
                "when",
                "When",
                "json",
                bindable=True,
                description='Structured predicate using bindings, for example {"all":[{"left":"${{effect-1.success}}","equals":true}]} or {"left":"${{query-1.count}}","greater_than":8}. Mutually exclusive with Match.',
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the single upstream node and validated automatically.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:switch",
        label="Switch",
        fields=[
            _field(
                "cases",
                "Cases",
                "object_list",
                required=True,
                description=(
                    "Ordered routes. The first matching case wins; duplicate ports are allowed."
                ),
                item_fields=[
                    _field("port", "Port", "string", required=True),
                    _field("label", "Label", "string"),
                    _field(
                        "when",
                        "When",
                        "json",
                        bindable=True,
                        description='Structured predicate using bindings, for example {"all":[{"left":"${{effect-1.success}}","equals":true}]} or {"left":"${{query-1.count}}","greater_than":8}. Mutually exclusive with Match.',
                    ),
                    _field(
                        "match",
                        "Match",
                        "json",
                        bindable=True,
                        description="Recursive subset match against the immediate incoming payload. Mutually exclusive with When.",
                    ),
                ],
            ),
            _field(
                "default_port",
                "Default port",
                "string",
                description="Fallback route when no case matches.",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the single upstream node and passed through unchanged on the selected route.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:debounce",
        label="Debounce",
        fields=[
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Groups activations to debounce. Supports workflow bindings.",
                placeholder="issue-comment:${{trigger.issue_id}}",
            ),
            _field(
                "quiet_period",
                "Quiet period",
                "string",
                required=True,
                description="ISO-8601 duration to wait after the latest activation, for example PT2M.",
                placeholder="PT2M",
            ),
            _field(
                "max_delay",
                "Max delay",
                "string",
                required=True,
                description="ISO-8601 duration cap from the first activation in a burst, for example PT10M.",
                placeholder="PT10M",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the single upstream node and emitted from the latest activation in a burst.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:buffer",
        label="Buffer",
        fields=[
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Groups independent buffers. Supports workflow bindings.",
                placeholder="buffer:${{system.execution.trace_id}}",
            ),
            _field(
                "count",
                "Count",
                "number",
                required=True,
                bindable=True,
                description="Release when this many activations have accumulated.",
                default_value=10,
                placeholder="10",
            ),
            _field(
                "timeout",
                "Timeout",
                "string",
                bindable=True,
                description=(
                    "Optional ISO-8601 duration from the first buffered activation, "
                    "for example PT10M. Omit for count-only buffering."
                ),
                default_value="PT10M",
                placeholder="PT10M",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                default_value={
                    "items": {"type": "array"},
                    "count": {"type": "integer"},
                    "release_reason": {"type": "string"},
                },
                description="Emits buffered items, released count, and release reason.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:map",
        label="Map",
        fields=[
            _field("template", "Template", "json", required=True, bindable=True),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the top-level keys in the map template.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:aggregate",
        label="Aggregate",
        fields=[
            _field(
                "items",
                "Items",
                "json",
                required=True,
                bindable=True,
                description="Array to aggregate. Use an exact binding to pass through an upstream array.",
                json_value_type="array",
                default_value=[],
                placeholder="${{query-model-usage.items}}",
            ),
            _field(
                "metrics",
                "Metrics",
                "json",
                required=True,
                description=(
                    "Object keyed by output name. Each value defines operation "
                    "sum, min, max, or avg and a dotted item path."
                ),
                json_value_type="object",
                default_value={"total": {"operation": "sum", "path": "value"}},
                placeholder='{"total_tokens":{"operation":"sum","path":"total_tokens"}}',
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Emits count plus one flat output for each configured metric.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:deserialize",
        label="Deserialize",
        fields=[
            _field(
                "content",
                "Content",
                "string",
                required=True,
                bindable=True,
                multiline=True,
                description=(
                    "JSON or YAML string to parse. Bind to an upstream string when content "
                    "is produced by another node."
                ),
                placeholder="${{node.payload_text}}",
            ),
            _field(
                "format",
                "Format",
                "enum",
                options=[
                    _field_option("auto", "Auto"),
                    _field_option("json", "JSON"),
                    _field_option("yaml", "YAML"),
                ],
                description="Parser to use. Auto tries JSON first, then YAML.",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                description=(
                    "Optional output schema for binding validation when Content is dynamic. "
                    "Literal object content is inferred automatically."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:serialize",
        label="Serialize",
        fields=[
            _field(
                "value",
                "Value",
                "json",
                required=True,
                bindable=True,
                description="Value to serialize. Use an exact binding to serialize an upstream object unchanged.",
                placeholder='{"title":"${{gate-map-1.title}}"}',
            ),
            _field(
                "format",
                "Format",
                "enum",
                options=[
                    _field_option("json", "JSON"),
                    _field_option("yaml", "YAML"),
                ],
                description="Serializer to use. JSON emits compact text; YAML emits block-style text.",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Emits content and format for downstream bindings.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:dedupe",
        label="Dedupe",
        fields=[
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description=(
                    "Stable idempotency key. Duplicate activations with the same key "
                    "are dropped until TTL expires."
                ),
                placeholder="issue-comment:${{trigger.comment_id}}",
            ),
            _field(
                "ttl",
                "TTL",
                "string",
                required=True,
                bindable=True,
                description="ISO-8601 dedupe window, for example P7D or PT1H.",
                placeholder="P7D",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the single upstream node.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:merge",
        label="Merge",
        fields=[
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the compatible intersection of upstream outputs.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:await-agent",
        label="Await agent",
        fields=[
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description="Select the session to watch. Provide either Session ID or VM name.",
                placeholder="session-id",
            ),
            _field(
                "vm_name",
                "VM name",
                "string",
                bindable=True,
                description="Optional alternative selector when Session ID is unavailable.",
                placeholder="vm-name",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from agent completion events.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:for",
        label="For",
        fields=[
            _field(
                "items",
                "Items",
                "json",
                required=True,
                bindable=True,
                description="Array expression to iterate. The body branch receives current_element/current_index outputs.",
                json_value_type="array",
                default_value=[],
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from for-loop runtime outputs.",
            ),
        ],
        output_ports=[
            _port("body", "Body", description="Loop body path while items remain."),
            _port("done", "Done", description="Exit path after the final item."),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:while",
        label="While",
        fields=[
            _field(
                "when",
                "When",
                "json",
                required=True,
                bindable=True,
                description='Structured predicate using bindings, for example {"left":"${{parse-tests.failure_count}}","greater_than":0}.',
            ),
            _field(
                "max_iterations",
                "Max iterations",
                "number",
                required=True,
                bindable=True,
                description="Positive integer cap for body iterations before routing to Exhausted.",
                default_value=3,
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from while-loop runtime outputs.",
            ),
        ],
        output_ports=[
            _port("body", "Body", description="Loop body path while the predicate is true."),
            _port("done", "Done", description="Exit path when the predicate is false."),
            _port(
                "exhausted",
                "Exhausted",
                description="Exit path when Max iterations is reached while the predicate is still true.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:create",
        label="VM create",
        fields=[
            _field("vm_name", "VM name", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("labels", "Labels", "json", bindable=True),
            _field("model", "Model", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("ttl_hours", "TTL hours", "number", bindable=True),
            _field(
                "allowed_tools",
                "Allowed tools",
                "string_list",
                description="Additional MCP tools to allow for the created session.",
            ),
            _field(
                "connector_grants",
                "Connector grants",
                "json",
                bindable=True,
                description="Connector-id to operation-name lists granted to the created session.",
                json_value_type="object",
                default_value={},
            ),
            _field(
                "host_directory_grants",
                "Host directory grants",
                "json",
                bindable=True,
                description=(
                    "Host-directory-id to list, import, or delete capability lists "
                    "granted to the created session."
                ),
                json_value_type="object",
                default_value={},
            ),
            _field(
                "tool_argument_policies",
                "Tool argument policy",
                "json",
                bindable=True,
                description="Tool-keyed argument policy rules to attach to the created session.",
                json_value_type="object",
                default_value={},
                options=_tool_field_options(),
            ),
            _field(
                "allowed_script_names",
                "Allowed script names",
                "string_list",
                description="Additional script names the created session may invoke with run_script.",
            ),
            _field("discord_channels", "Discord channels", "string_list"),
            _field("email_mailboxes", "Email mailboxes", "string_list"),
            _field("email_folders", "Email folders", "string_list"),
            _field("dotnet", "Dotnet", "boolean", bindable=True),
            _field("nuget", "NuGet", "boolean", bindable=True),
            _field("mssql", "MSSQL", "boolean", bindable=True),
            _field("mssql_init_root", "MSSQL init root", "string", bindable=True),
            _field("playwright", "Playwright", "boolean", bindable=True),
            _field("codex_skills", "Codex skills", "string_list"),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("metadata", "Metadata", "json", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:clone-repo",
        label="VM clone repo",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "managed_repo_id",
                "Managed repository ID",
                "string",
                required=True,
                bindable=True,
            ),
            _field("clone_branch", "Clone branch", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field(
                "git_access_mode",
                "Git access mode",
                "enum",
                bindable=True,
                options=[
                    _field_option("read-only", "Read only"),
                    _field_option("write", "Write"),
                ],
            ),
            _field(
                "git_access_lifetime",
                "Git access lifetime",
                "enum",
                bindable=True,
                options=[
                    _field_option("session", "Session"),
                    _field_option("operation", "Operation"),
                ],
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:bootstrap-python",
        label="VM bootstrap Python",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("host_checkout", "Host checkout", "string", required=True, bindable=True),
            _field("target_dir", "Target dir", "string", required=True, bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:bootstrap-frontend",
        label="VM bootstrap frontend",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("host_checkout", "Host checkout", "string", required=True, bindable=True),
            _field("install_roots", "Install roots", "string_list"),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:start",
        label="VM start",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:stop",
        label="VM stop",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:create",
        label="Agent create",
        fields=[
            _field(
                "execution_backend",
                "Execution backend",
                "enum",
                required=True,
                description="Runtime backend that will host the created agent session.",
                options=[
                    _field_option("libvirt_vm", "Libvirt VM"),
                    _field_option("container_worker_vm", "Container worker VM"),
                ],
            ),
            _field(
                "agent_provider",
                "Agent provider",
                "enum",
                bindable=True,
                description=(
                    "Optional agent harness override. Leave blank to use the directory default."
                ),
                options=[
                    _field_option("codex", "Codex"),
                    _field_option("claude", "Claude"),
                    _field_option(
                        "copilot",
                        "Copilot",
                        metadata={"execution_backends": ["libvirt_vm"]},
                    ),
                ],
            ),
            _field(
                "provider",
                "VM provider",
                "string",
                bindable=True,
                description=(
                    "Optional libvirt VM infrastructure provider override. "
                    "Leave blank to use the directory VM default."
                ),
                metadata={"execution_backends": ["libvirt_vm"]},
            ),
            _field(
                "vm_name",
                "VM name",
                "string",
                bindable=True,
                description="Optional VM name for libvirt-backed agents. Container-backed agents usually leave this blank.",
                metadata={"execution_backends": ["libvirt_vm"]},
            ),
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description="Optional durable session id. Leave blank to let the runtime allocate one.",
            ),
            _field(
                "existing_agent_behavior",
                "Existing agent behavior",
                "enum",
                description="What effect:agent:create should do when the target session already exists.",
                options=[
                    _field_option("upsert", "Upsert existing agent"),
                    _field_option("noop", "No-op if existing"),
                ],
            ),
            _field(
                "labels",
                "Labels",
                "string_map",
                bindable=True,
                description="String key/value labels stored on the created agent session.",
            ),
            _field(
                "model",
                "Model",
                "string",
                bindable=True,
                description="Model name the agent should use. Leave blank to inherit the directory default.",
            ),
            _field(
                "model_provider_mode",
                "Model relay",
                "enum",
                bindable=True,
                description=(
                    "Optional OpenAI-compatible relay mode override. Leave blank to use "
                    "the directory provider default; choose Claude as the agent provider "
                    "for Anthropic routing."
                ),
                options=[
                    _field_option("openai_public", "Public OpenAI"),
                    _field_option("local_openai", "Local OpenAI"),
                    _field_option("azure_openai", "Azure OpenAI"),
                ],
            ),
            _field(
                "codex_auth_mode",
                "Codex usage",
                "enum",
                bindable=True,
                description=(
                    "Optional Codex authentication override for container workers. "
                    "Subscription mode uses the worker-owned ChatGPT account; metered mode "
                    "uses the configured OpenAI relay."
                ),
                options=[
                    _field_option("metered_api", "Metered API"),
                    _field_option("chatgpt_subscription", "ChatGPT subscription"),
                ],
                metadata={
                    "execution_backends": ["container_worker_vm"],
                    "agent_providers": ["codex"],
                },
            ),
            _field(
                "npm_registry_url",
                "NPM registry URL",
                "string",
                bindable=True,
                description="Optional npm registry override for container-backed agent sessions.",
                metadata={"execution_backends": ["container_worker_vm"]},
            ),
            _field(
                "vm_user",
                "VM user",
                "string",
                bindable=True,
                description="Guest user for libvirt-backed sessions. Defaults to agent.",
                metadata={"execution_backends": ["libvirt_vm"]},
            ),
            _field(
                "vm_image",
                "VM image",
                "string",
                bindable=True,
                description=(
                    "Optional approved libvirt VM template alias from the directory vm.json "
                    "approved VM template/base-image catalog. This is the libvirt-only "
                    "selector and applies only to execution_backend=libvirt_vm; use "
                    "container_image for container-worker agents."
                ),
            ),
            _field(
                "ttl_hours",
                "TTL hours",
                "number",
                bindable=True,
                description="Session lifetime in hours before the host considers it expired.",
            ),
            _field(
                "allowed_tools",
                "Allowed tools",
                "string_list",
                description="MCP tools the created session may invoke through the host gateway.",
            ),
            _field(
                "connector_grants",
                "Connector grants",
                "json",
                bindable=True,
                description="Connector-id to operation-name lists granted to the created session.",
                json_value_type="object",
                default_value={},
            ),
            _field(
                "host_directory_grants",
                "Host directory grants",
                "json",
                bindable=True,
                description=(
                    "Host-directory-id to list, import, or delete capability lists "
                    "granted to the created session."
                ),
                json_value_type="object",
                default_value={},
            ),
            _field(
                "tool_argument_policies",
                "Tool argument policy",
                "json",
                bindable=True,
                description="Tool-keyed argument policy rules to attach to the created session.",
                json_value_type="object",
                default_value={},
                options=_tool_field_options(),
            ),
            _field(
                "allowed_script_names",
                "Allowed script names",
                "string_list",
                description="Additional script names the created session may invoke with run_script.",
            ),
            _field(
                "communication_target_ids",
                "Communication targets",
                "string_list",
                description=(
                    "Known communication targets for Teams and Discord destinations or outbound "
                    "email recipients. This does not grant mailbox account access."
                ),
                metadata={"selection_only": True},
            ),
            _field(
                "email_mailbox_grants",
                "Email mailbox grants",
                "json",
                bindable=True,
                description=(
                    "Mailbox account grants with mailbox_id, read/draft/send access, and folders."
                ),
                json_value_type="array",
                default_value=[],
            ),
            _field(
                "discord_channels",
                "Legacy Discord channels",
                "string_list",
                description="Discord channel ids the created session may read or post to.",
            ),
            _field(
                "teams_channel_allowlist",
                "Teams targets",
                "string_list",
                description="Teams channels or chats the created session may use.",
                placeholder="team-id/channel-id or chat:<chat-id>",
            ),
            _field(
                "teams_chat_allowlist",
                "Teams chats",
                "string_list",
                placeholder="chat:<chat-id>",
            ),
            _field(
                "teams_user_allowlist",
                "Teams users",
                "string_list",
                description="Teams users the created session may interact with.",
                placeholder="user:<user-id-or-upn>",
            ),
            _field(
                "email_mailboxes",
                "Legacy email mailboxes",
                "string_list",
                description="Email mailboxes the created session may inspect.",
            ),
            _field(
                "email_folders",
                "Legacy email folders",
                "string_list",
                description="Email folders the created session may inspect.",
            ),
            _field(
                "communication_file_receive_enabled",
                "Receive inbound files",
                "boolean",
                bindable=True,
                description="Allow the session to download inbound communication attachments into host-managed artifacts.",
            ),
            _field(
                "communication_file_send_enabled",
                "Send files",
                "boolean",
                bindable=True,
                description="Allow the session to send staged communication artifacts through approved targets.",
            ),
            _field(
                "communication_file_require_approval_for_send",
                "Require approval for file sends",
                "boolean",
                bindable=True,
                description="Require an approval before the session can send staged communication files.",
            ),
            _field(
                "communication_file_max_bytes",
                "Communication file max bytes",
                "number",
                bindable=True,
                description="Maximum size in bytes for a single communication file. Leave blank to inherit policy defaults.",
            ),
            _field(
                "web_search_enabled",
                "Web search",
                "boolean",
                bindable=True,
                description="Allow the created session to use OpenAI-hosted web_search tools through the OpenAI relay.",
            ),
            _field(
                "dotnet",
                "Dotnet",
                "boolean",
                bindable=True,
                description="Enable .NET toolchain setup for the created agent when supported by the selected backend.",
            ),
            _field(
                "nuget",
                "NuGet",
                "boolean",
                bindable=True,
                description="Enable NuGet relay and package-cache setup for the created agent.",
            ),
            _field(
                "mssql",
                "MSSQL",
                "boolean",
                bindable=True,
                description="Bootstrap SQL Server tooling for libvirt-backed agents.",
                metadata={"execution_backends": ["libvirt_vm"]},
            ),
            _field(
                "mssql_init_root",
                "MSSQL init root",
                "string",
                bindable=True,
                description="Optional host init-script root for MSSQL provisioning.",
                metadata={"execution_backends": ["libvirt_vm"]},
            ),
            _field(
                "playwright",
                "Playwright",
                "boolean",
                bindable=True,
                description="Enable Playwright browser automation setup for the created agent.",
            ),
            _field(
                "codex_skills",
                "Codex skills",
                "string_list",
                description="Managed Codex skills to install into the created session.",
            ),
            _field(
                "container_image",
                "Container image",
                "string",
                bindable=True,
                description="Optional approved container image alias or the configured default image.",
                metadata={"execution_backends": ["container_worker_vm"]},
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description="Directory scope that owns the created session and resolves inherited configuration.",
            ),
            _field(
                "metadata",
                "Metadata",
                "json",
                bindable=True,
                description="Session metadata patch to merge into the created agent session.",
                json_value_type="object",
                default_value={},
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:destroy",
        label="Agent destroy",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "execution_backend",
                "Execution backend",
                "enum",
                options=[
                    _field_option("libvirt_vm", "Libvirt VM"),
                    _field_option("container_worker_vm", "Container worker VM"),
                ],
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:clone-repo",
        label="Agent clone repo",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "managed_repo_id",
                "Managed repository ID",
                "string",
                required=True,
                bindable=True,
            ),
            _field("clone_branch", "Clone branch", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field(
                "git_access_mode",
                "Git access mode",
                "string",
                bindable=True,
                description=(
                    "Use write for git push-agent or read-only for clone, fetch, and pull."
                ),
            ),
            _field(
                "git_access_lifetime",
                "Git access lifetime",
                "enum",
                bindable=True,
                options=[
                    _field_option("session", "Session"),
                    _field_option("operation", "Operation"),
                ],
                description="Defaults to persistent session access.",
            ),
            _field(
                "pinned_ref",
                "Pinned ref",
                "string",
                bindable=True,
                description="Optional ref to fetch and verify before checkout.",
            ),
            _field(
                "pinned_commit_sha",
                "Pinned commit SHA",
                "string",
                bindable=True,
                description="Exact commit required for pinned_ref.",
            ),
            _field(
                "git_author_name",
                "Git author name",
                "string",
                bindable=True,
                description=(
                    "Optional author.name for cloned repositories. "
                    "Set together with git_author_email."
                ),
            ),
            _field(
                "git_author_email",
                "Git author email",
                "string",
                bindable=True,
                description=(
                    "Optional author.email for cloned repositories. "
                    "Set together with git_author_name."
                ),
            ),
            _field(
                "git_committer_name",
                "Git committer name",
                "string",
                bindable=True,
                description=(
                    "Optional committer.name for cloned repositories. "
                    "Set together with git_committer_email."
                ),
            ),
            _field(
                "git_committer_email",
                "Git committer email",
                "string",
                bindable=True,
                description=(
                    "Optional committer.email for cloned repositories. "
                    "Set together with git_committer_name."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:prepare-reference-repositories",
        label="Prepare reference repositories",
        description=(
            "Refresh managed mirrors concurrently and prepare clean, detached, read-only "
            "repository snapshots at the exact refreshed commits."
        ),
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "directory_path",
                "Directory path",
                "string",
                required=True,
                bindable=True,
            ),
            _field(
                "repositories",
                "Repositories",
                "json",
                required=True,
                bindable=True,
                json_value_type="array",
                description=(
                    "Array of objects with repo_source and an optional branch override."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:grant-repo-access",
        label="Agent grant repo access",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "managed_repo_id",
                "Managed repository ID",
                "string",
                required=True,
                bindable=True,
            ),
            _field(
                "git_access_mode",
                "Git access mode",
                "enum",
                bindable=True,
                options=[
                    _field_option("read-only", "Read only"),
                    _field_option("write", "Write"),
                ],
            ),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:revoke-repo-access",
        label="Agent revoke repo access",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "managed_repo_id",
                "Managed repository ID",
                "string",
                required=True,
                bindable=True,
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:git:ensure-managed-repo",
        label="Ensure managed repository",
        description=(
            "Idempotently register or refresh a canonical repository inside the current "
            "directory trust boundary and return its managed repository ID."
        ),
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("repo_source", "Repository source", "string", required=True, bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:bootstrap-frontend",
        label="Agent bootstrap frontend",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("host_checkout", "Host checkout", "string", required=True, bindable=True),
            _field("install_roots", "Install roots", "string_list"),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:copy-files",
        label="Agent copy files",
        fields=[
            _field(
                "target",
                "Target",
                "string",
                bindable=True,
                description="Agent session selector. Usually bind this to an upstream session_id.",
            ),
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description="Explicit session id to copy into when Target is not used.",
            ),
            _field(
                "vm_name",
                "VM name",
                "string",
                bindable=True,
                description="Legacy VM selector for libvirt-backed agent sessions.",
            ),
            _field(
                "vm_user",
                "VM user",
                "string",
                bindable=True,
                description="Guest user for libvirt-backed copies. Defaults to agent.",
            ),
            _field(
                "allowed_source_roots",
                "Allowed source roots",
                "string_list",
                bindable=True,
                description=(
                    "Optional absolute host roots allowed for Source path beyond the "
                    "built-in repo template/script and artifact roots."
                ),
            ),
            _field(
                "files",
                "Files",
                "object_list",
                required=True,
                description=(
                    "Files to write into the agent session. Each item needs either "
                    "Source path or Content."
                ),
                item_fields=[
                    _field(
                        "source_path",
                        "Source path",
                        "string",
                        bindable=True,
                        description="Host-side file path to copy. Mutually exclusive with Content.",
                    ),
                    _field(
                        "source_format",
                        "Source format",
                        "enum",
                        description=(
                            "How to read Source path. File copies the source; Template renders "
                            "workflow bindings before writing."
                        ),
                        options=[
                            _field_option("file", "File"),
                            _field_option("template", "Template"),
                        ],
                    ),
                    _field(
                        "content",
                        "Content",
                        "string",
                        bindable=True,
                        multiline=True,
                        description="Inline file content. Mutually exclusive with Source path.",
                    ),
                    _field(
                        "content_format",
                        "Content format",
                        "enum",
                        description="How to serialize inline Content before writing it.",
                        options=[
                            _field_option("text", "Text"),
                            _field_option("json", "JSON"),
                            _field_option("yaml", "YAML"),
                        ],
                    ),
                    _field(
                        "destination_path",
                        "Destination path",
                        "string",
                        required=True,
                        bindable=True,
                        description=(
                            "Absolute destination path inside the agent session. "
                            "Container-backed sessions require /home/agent/..."
                        ),
                    ),
                    _field(
                        "mode",
                        "Mode",
                        "string",
                        bindable=True,
                        description=(
                            "Optional Unix file permissions to apply after copying, "
                            'for example "0644" or "0755".'
                        ),
                    ),
                    _field(
                        "overwrite",
                        "Overwrite",
                        "boolean",
                        bindable=True,
                        description=(
                            "When enabled, replace an existing destination file. "
                            "When disabled, fail if the destination already exists."
                        ),
                    ),
                    _field(
                        "merge_strategy",
                        "Merge strategy",
                        "enum",
                        bindable=True,
                        description=(
                            "Optional write strategy. managed_section replaces only the marked "
                            "section identified by Section ID and preserves the rest of the file."
                        ),
                        options=[_field_option("managed_section", "Managed section")],
                    ),
                    _field(
                        "section_id",
                        "Section ID",
                        "string",
                        bindable=True,
                        description=(
                            "Stable identifier for managed_section writes. Required when Merge "
                            "strategy is managed_section."
                        ),
                    ),
                    _field(
                        "remove_section_ids",
                        "Remove section IDs",
                        "string_list",
                        bindable=True,
                        description=(
                            "Optional managed section IDs to remove from the destination before "
                            "writing Section ID."
                        ),
                    ),
                ],
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:artifact:stage",
        label="Stage artifact",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "path",
                "Path",
                "string",
                required=True,
                bindable=True,
                description="File or directory path that resolves under /home/agent.",
                placeholder="repos/project/output/report.html",
            ),
            _field(
                "destination_path",
                "Destination path",
                "string",
                bindable=True,
                description="Optional relative destination under /artifacts. Defaults to the source basename.",
                placeholder="reports/report.html",
            ),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:artifact:publish-artifacts",
        label="Publish artifacts",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Immutable key for the published tar.gz archive.",
                placeholder="runs/${{trigger.event_id}}",
            ),
            _field(
                "source_paths",
                "Source paths",
                "string_list",
                bindable=True,
                description=(
                    "Optional relative paths under /artifacts to publish. "
                    "Leave empty to publish the full artifacts folder."
                ),
            ),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:artifact:import-published",
        label="Import published artifact",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Immutable key of the published tar.gz archive to import.",
                placeholder="manuals/true-app/reviewed/latest",
            ),
            _field(
                "destination_path",
                "Destination path",
                "string",
                bindable=True,
                description=(
                    "Optional relative prefix under /artifacts. Defaults to the archive paths."
                ),
                placeholder="input",
            ),
            _field(
                "overwrite",
                "Overwrite",
                "boolean",
                bindable=True,
                description="When enabled, replace existing artifact files during import.",
            ),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:prompt",
        label="Agent prompt",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("prompt", "Prompt", "string", required=True, bindable=True, multiline=True),
            _field("model", "Model", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("resume_thread_id", "Resume thread ID", "string", bindable=True),
            _field(
                "resume_thread_metadata_key",
                "Resume thread metadata key",
                "string",
                bindable=True,
            ),
            _field(
                "resume_thread_metadata_collection",
                "Resume thread metadata collection",
                "string",
                bindable=True,
                placeholder="agent_thread_ids",
            ),
            _field(
                "mcp_tool_approval_lease_id",
                "MCP tool approval lease ID",
                "string",
                bindable=True,
                description=(
                    "Lease ID from Approve MCP tools for run. Direct upstream approval "
                    "outputs are detected automatically."
                ),
            ),
        ],
        output_ports=[
            _port(
                AGENT_PROMPT_STARTED_PORT,
                "Started",
                description="Emits after the prompt has been dispatched.",
            ),
            _port(
                AGENT_PROMPT_COMPLETED_PORT,
                "Completed",
                description="Emits after the agent run has completed.",
            ),
            _port(
                AGENT_PROMPT_ERROR_PORT,
                "Error",
                description=(
                    "Emits when prompt dispatch fails and the node is handled by an error route."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:enqueue-prompt",
        label="Agent enqueue prompt",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("prompt", "Prompt", "string", required=True, bindable=True, multiline=True),
            _field("model", "Model", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("dispatch_id", "Dispatch ID", "string", bindable=True),
            _field("resume_thread_id", "Resume thread ID", "string", bindable=True),
            _field(
                "resume_mode",
                "Resume mode",
                "string",
                bindable=True,
                placeholder="same_context",
            ),
            _field(
                "resume_thread_metadata_key",
                "Resume thread metadata key",
                "string",
                bindable=True,
            ),
            _field(
                "resume_thread_metadata_collection",
                "Resume thread metadata collection",
                "string",
                bindable=True,
                placeholder="agent_thread_ids",
            ),
            _field(
                "mcp_tool_approval_lease_id",
                "MCP tool approval lease ID",
                "string",
                bindable=True,
                description=(
                    "Lease ID from Approve MCP tools for run. Direct upstream approval "
                    "outputs are detected automatically."
                ),
            ),
            _field("queue_key", "Queue key", "string", bindable=True),
        ],
        output_ports=[
            _port(
                AGENT_PROMPT_STARTED_PORT,
                "Started",
                description="Emits after the queued prompt has been dispatched.",
            ),
            _port(
                AGENT_PROMPT_COMPLETED_PORT,
                "Completed",
                description="Emits after the dispatched agent run has completed.",
            ),
            _port(
                AGENT_PROMPT_ERROR_PORT,
                "Error",
                description=(
                    "Emits when prompt dispatch fails and the node is handled by an error route."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:exec",
        label="Agent exec",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("command", "Command", "string", required=True, bindable=True, multiline=True),
            _field("working_directory", "Working directory", "string", bindable=True),
            _field("environment", "Environment", "json", bindable=True),
            _field("stdin", "Stdin", "string", bindable=True, multiline=True),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
            _field("check", "Check exit code", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:semantic:ensure-collection",
        label="Semantic ensure collection",
        fields=[
            _field(
                "collection_name",
                "Collection name",
                "string",
                required=True,
                bindable=True,
                description="Qdrant collection name to create if missing.",
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description="Directory-scoped provider config to use for semantic settings.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:semantic:upsert",
        label="Semantic upsert",
        fields=[
            _field(
                "collection_name",
                "Collection name",
                "string",
                required=True,
                bindable=True,
                description="Qdrant collection name to create if missing and upsert into.",
            ),
            _field(
                "point_id",
                "Point ID",
                "string",
                required=True,
                bindable=True,
                description=(
                    "Caller-supplied Qdrant point ID used for idempotent updates. "
                    "Use an unsigned 64-bit integer or UUID string."
                ),
            ),
            _field("text", "Text", "string", required=True, bindable=True, multiline=True),
            _field(
                "payload",
                "Payload",
                "json",
                bindable=True,
                description="Optional Qdrant payload. The _semantic_text key is reserved.",
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("wait", "Wait", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:semantic:delete",
        label="Semantic delete",
        fields=[
            _field("collection_name", "Collection name", "string", required=True, bindable=True),
            _field(
                "point_ids",
                "Point IDs",
                "string_list",
                required=True,
                bindable=True,
                description=(
                    "Explicit Qdrant point IDs to delete. IDs must be unsigned "
                    "64-bit integers or UUID strings. Filter deletes are not supported."
                ),
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("wait", "Wait", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:state:update",
        label="State update",
        fields=[
            _field(
                "namespace",
                "Namespace",
                "string",
                required=True,
                bindable=True,
                description="Workflow state namespace. Use one namespace per logical state family.",
                placeholder="workflow",
            ),
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Workflow state key within the selected namespace.",
                placeholder="cursor",
            ),
            _field(
                "operation",
                "Operation",
                "enum",
                required=True,
                bindable=True,
                description="Choose whether to set, patch, or delete the state record.",
                options=[
                    _field_option("set", "Set"),
                    _field_option("patch", "Patch"),
                    _field_option("delete", "Delete"),
                ],
            ),
            _field(
                "value",
                "Value",
                "json",
                bindable=True,
                description="JSON value to store when operation is set.",
                json_value_type="object",
                default_value={},
            ),
            _field(
                "patch",
                "Patch",
                "json",
                bindable=True,
                description="JSON Merge Patch object to apply when operation is patch.",
                json_value_type="object",
                default_value={},
            ),
            _field(
                "expected_version",
                "Expected version",
                "number",
                bindable=True,
                description="Optional optimistic concurrency guard. Missing records compare as version 0.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:http:request",
        label="HTTP request",
        fields=[
            _field(
                "method",
                "Method",
                "enum",
                bindable=True,
                default_value="GET",
                options=[
                    _field_option("GET", "GET"),
                    _field_option("POST", "POST"),
                    _field_option("PUT", "PUT"),
                ],
            ),
            _field(
                "url",
                "URL",
                "string",
                required=True,
                bindable=True,
                placeholder="https://api.example.test/items",
            ),
            _field(
                "allowed_url_patterns",
                "Allowed URL patterns",
                "string_list",
                bindable=True,
                default_value=["*"],
                placeholder="https://api.example.test/*",
                description=(
                    "Shell-style URL or host patterns allowed for this request. Bound URLs "
                    "require explicit host-restricted patterns."
                ),
            ),
            _field(
                "allow_private_networks",
                "Allow private networks",
                "boolean",
                bindable=True,
                default_value=False,
                description=(
                    "When enabled, allow private, loopback, and link-local destinations that "
                    "also match Allowed URL patterns. Metadata destinations remain blocked."
                ),
            ),
            _field(
                "payload",
                "Payload",
                "json",
                bindable=True,
                json_value_type="object",
                description="Optional JSON request body sent for POST or PUT requests.",
            ),
            _field(
                "headers",
                "Headers",
                "json",
                bindable=True,
                json_value_type="object",
                description="Optional request headers as a JSON object.",
            ),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
            _field(
                "check",
                "Check status",
                "boolean",
                bindable=True,
                default_value=False,
                description=(
                    "When enabled, non-2xx responses raise after response details are captured."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:runtime:diagnostics-snapshot",
        label="Runtime diagnostics snapshot",
        fields=[
            _field(
                "since",
                "Since",
                "string",
                bindable=True,
                default_value="24 hours ago",
                placeholder="24 hours ago",
                description="journalctl-compatible time window for service warnings.",
            ),
            _field(
                "units",
                "Units",
                "string_list",
                bindable=True,
                description="Optional subset of the built-in evander service allowlist.",
            ),
            _field(
                "include_failed_units",
                "Include failed units",
                "boolean",
                bindable=True,
                default_value=True,
            ),
            _field(
                "include_journal",
                "Include journal warnings",
                "boolean",
                bindable=True,
                default_value=True,
            ),
            _field(
                "include_runtime_files",
                "Include runtime failure files",
                "boolean",
                bindable=True,
                default_value=True,
            ),
            _field(
                "max_journal_lines_per_unit",
                "Max journal lines per unit",
                "number",
                bindable=True,
                default_value=200,
            ),
            _field(
                "max_runtime_file_bytes",
                "Max runtime file bytes",
                "number",
                bindable=True,
                default_value=120000,
            ),
            _field(
                "command_timeout_seconds",
                "Command timeout seconds",
                "number",
                bindable=True,
                default_value=10,
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:exec",
        label="VM exec",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("command", "Command", "string", required=True, bindable=True),
            _field("working_directory", "Working directory", "string", bindable=True),
            _field("environment", "Environment", "json", bindable=True),
            _field("stdin", "Stdin", "string", bindable=True, multiline=True),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
            _field("check", "Check exit code", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:copy-files",
        label="VM copy files",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field(
                "allowed_source_roots",
                "Allowed source roots",
                "string_list",
                bindable=True,
                description=(
                    "Optional absolute host roots allowed for Source path beyond the "
                    "built-in repo template/script and artifact roots."
                ),
            ),
            _field(
                "files",
                "Files",
                "object_list",
                required=True,
                item_fields=[
                    _field("source_path", "Source path", "string", bindable=True),
                    _field(
                        "source_format",
                        "Source format",
                        "enum",
                        options=[
                            _field_option("file", "File"),
                            _field_option("template", "Template"),
                        ],
                    ),
                    _field("content", "Content", "string", bindable=True),
                    _field(
                        "content_format",
                        "Content format",
                        "enum",
                        options=[
                            _field_option("text", "Text"),
                            _field_option("json", "JSON"),
                            _field_option("yaml", "YAML"),
                        ],
                    ),
                    _field(
                        "destination_path",
                        "Destination path",
                        "string",
                        required=True,
                        bindable=True,
                    ),
                    _field("mode", "Mode", "string", bindable=True),
                    _field("overwrite", "Overwrite", "boolean", bindable=True),
                    _field(
                        "merge_strategy",
                        "Merge strategy",
                        "enum",
                        bindable=True,
                        options=[_field_option("managed_section", "Managed section")],
                    ),
                    _field("section_id", "Section ID", "string", bindable=True),
                    _field(
                        "remove_section_ids",
                        "Remove section IDs",
                        "string_list",
                        bindable=True,
                    ),
                ],
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:delete-files",
        label="VM delete files",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field(
                "paths",
                "Paths",
                "string_list",
                required=True,
                description="Absolute guest paths to delete.",
            ),
            _field("recursive", "Recursive", "boolean", bindable=True),
            _field("missing_ok", "Ignore missing", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:issue:update-state",
        label="Issue update state",
        fields=[
            _field("issue_id", "Issue ID", "string", required=True, bindable=True),
            _field("state", "State", "string", required=True, bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:issue:update-metadata",
        label="Issue update metadata",
        fields=[
            _field("issue_id", "Issue ID", "string", required=True, bindable=True),
            _field("patch", "Patch", "json", bindable=True),
            _field("merge_patch", "Merge patch", "json", bindable=True),
            _field("expected_updated_at", "Expected updated at", "string", bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:issue:upsert-ado-proxy",
        label="Issue upsert ADO proxy",
        fields=[
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("organization_url", "Organization URL", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("managed_repo_id", "Managed repository ID", "string", bindable=True),
            _field(
                "repo_source",
                "Repository source",
                "string",
                bindable=True,
                description="Transient source registered before the issue record is stored.",
            ),
            _field("yaml_output_dir", "YAML output dir", "string", bindable=True),
            _field("item", "Item", "json", bindable=True),
            _field("items", "Items", "json", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:update-work-item",
        label="ADO update work item",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("project", "Project", "string", required=True, bindable=True),
            _field("work_item_id", "Work item ID", "string", required=True, bindable=True),
            _field("state", "State", "string", bindable=True),
            _field("fields", "Fields", "json", bindable=True),
            _field("expected_states", "Expected states", "string_list", bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:add-work-item-tag",
        label="ADO add work item tag",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("project", "Project", "string", required=True, bindable=True),
            _field("work_item_id", "Work item ID", "string", required=True, bindable=True),
            _field("tag", "Tag", "string", required=True, bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:remove-work-item-tag",
        label="ADO remove work item tag",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("project", "Project", "string", required=True, bindable=True),
            _field("work_item_id", "Work item ID", "string", required=True, bindable=True),
            _field("tag", "Tag", "string", required=True, bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:post-work-item-comment",
        label="ADO post work item comment",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("project", "Project", "string", required=True, bindable=True),
            _field("work_item_id", "Work item ID", "string", required=True, bindable=True),
            _field("comment", "Comment", "string", required=True, bindable=True, multiline=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:pipeline-run",
        label="ADO pipeline run",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("pipeline_id", "Pipeline ID", "number", required=True, bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("branch", "Branch", "string", bindable=True),
            _field(
                "variables",
                "Variables",
                "json",
                bindable=True,
                json_value_type="object",
                default_value={},
            ),
            _field(
                "template_parameters",
                "Template parameters",
                "json",
                bindable=True,
                json_value_type="object",
                default_value={},
            ),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
            _field("poll_interval_seconds", "Poll interval seconds", "number", bindable=True),
            _field("check", "Check result", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:event:emit",
        label="Emit internal event",
        fields=[
            _field("event_type", "Event type", "string", required=True, bindable=True),
            _field("correlation_key", "Correlation key", "string", bindable=True),
            _field("payload", "Payload", "json", required=True, bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:event:schedule",
        label="Schedule internal event",
        fields=[
            _field("event_type", "Event type", "string", required=True, bindable=True),
            _field("correlation_key", "Correlation key", "string", bindable=True),
            _field(
                "payload",
                "Payload",
                "json",
                required=True,
                bindable=True,
                json_value_type="object",
            ),
            _field("run_at", "Run at", "string", bindable=True),
            _field("delay_seconds", "Delay seconds", "number", bindable=True),
            _field("idempotency_key", "Idempotency key", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:communication:send-message",
        label="Send message",
        fields=[
            _field(
                "communication_target_id",
                "Communication target",
                "enum",
                required=True,
                description=(
                    "Directory-scoped target used to send a message. Teams channel, chat, "
                    "or user targets need write or reply capability; Discord targets need "
                    "write capability."
                ),
            ),
            _field("message", "Message", "string", required=True, bindable=True, multiline=True),
            _field(
                "thread_id",
                "Thread ID",
                "string",
                bindable=True,
                description=(
                    "Optional Teams channel reply ID. Leave blank for a top-level send. "
                    "Teams chat and user targets, and Discord targets, do not support "
                    "thread replies."
                ),
            ),
            _field(
                "sender_session_id",
                "Sender session ID",
                "string",
                bindable=True,
                description=(
                    "Optional session whose resolved identity should be used when a Discord "
                    "target opts into resolved sender identity. If omitted or unresolved, the "
                    "message uses the platform identity."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:notification:send",
        label="Send notification",
        fields=[
            _field(
                "communication_target_id",
                "Communication target ID",
                "string",
                required=True,
                description=(
                    "Directory-scoped target used to send a notification. Teams, Discord, "
                    "and email targets need notify capability."
                ),
            ),
            _field("message", "Message", "string", required=True, bindable=True, multiline=True),
            _field(
                "severity",
                "Severity",
                "enum",
                options=[
                    _field_option("info", "Info"),
                    _field_option("success", "Success"),
                    _field_option("warning", "Warning"),
                    _field_option("error", "Error"),
                ],
            ),
            _field("subject", "Subject", "string", bindable=True),
            _field(
                "thread_id",
                "Thread ID",
                "string",
                bindable=True,
                description=(
                    "Optional Teams channel reply hint. Leave blank for a top-level "
                    "notification. Teams chat and user targets reject thread replies at "
                    "runtime, and Discord or email targets are not thread destinations."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:session:update-metadata",
        label="Session update metadata",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("metadata", "Metadata", "json", required=True, bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:session:approve-mcp-tools-for-run",
        label="Approve MCP tools for run",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "tools",
                "Tools",
                "string_list",
                required=True,
                bindable=True,
                description=("MCP tools to approve only for this workflow run and session."),
                options=_tool_field_options(),
            ),
            _field(
                "approval_notification_target_ids",
                "Approval notification target IDs",
                "string_list",
                bindable=True,
                description=(
                    "Optional host-side communication target IDs that should receive "
                    "approval notifications for this run-scoped lease."
                ),
            ),
            _field(
                "expires_in_seconds",
                "Expires in seconds",
                "number",
                bindable=True,
                description=(
                    "Optional lease lifetime. Defaults to 24 hours and never extends "
                    "past the session expiry."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:session:revoke-mcp-tool-approval-lease",
        label="Revoke MCP tool approval lease",
        fields=[
            _field(
                "mcp_tool_approval_lease_id",
                "MCP tool approval lease ID",
                "string",
                required=True,
                bindable=True,
                description=(
                    "Preferred lease ID from Approve MCP tools for run. Revokes that lease."
                ),
            ),
            _field(
                "lease_id",
                "Lease ID",
                "string",
                bindable=True,
                description="Alias for MCP tool approval lease ID.",
            ),
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description="Optional guard that must match the stored lease.",
            ),
            _field(
                "workflow_run_id",
                "Workflow run ID",
                "string",
                bindable=True,
                description="Optional guard that must match the stored lease.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:session:renew-if-expired",
        label="Session renew if expired",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "extend_by",
                "Extend by",
                "string",
                bindable=True,
                description="Duration from now for the renewed session, such as 8h, 2d, or PT8H.",
            ),
            _field(
                "renew_if_expires_within",
                "Renew if expires within",
                "string",
                bindable=True,
                description=(
                    "Optional pre-expiry window. Renew active sessions that expire within "
                    "this duration, such as 30m, 4h, or PT30M."
                ),
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:git:promote-agent-branch",
        label="Promote agent branch",
        fields=[
            _field(
                "managed_repo_id", "Managed repository ID", "string", required=True, bindable=True
            ),
            _field("agent_branch", "Agent branch", "string", required=True, bindable=True),
            _field("target_branch", "Target branch", "string", required=True, bindable=True),
            _field(
                "expected_commit_sha", "Expected commit SHA", "string", required=True, bindable=True
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:git:push-branch",
        label="Push git branch",
        fields=[
            _field(
                "managed_repo_id", "Managed repository ID", "string", required=True, bindable=True
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("source_ref", "Source ref", "string", required=True, bindable=True),
            _field(
                "expected_commit_sha", "Expected commit SHA", "string", required=True, bindable=True
            ),
            _field(
                "expected_destination_commit_sha",
                "Expected destination commit SHA",
                "string",
                bindable=True,
            ),
            _field(
                "destination_branch", "Destination branch", "string", required=True, bindable=True
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:pr:create",
        label="PR create",
        fields=[
            _field(
                "managed_repo_id", "Managed repository ID", "string", required=True, bindable=True
            ),
            _field("source_ref", "Source ref", "string", required=True, bindable=True),
            _field(
                "expected_commit_sha", "Expected commit SHA", "string", required=True, bindable=True
            ),
            _field(
                "destination_branch", "Destination branch", "string", required=True, bindable=True
            ),
            _field(
                "fallback_destination_branch",
                "Fallback destination branch",
                "string",
                bindable=True,
            ),
            _field("base_branch", "Base branch", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("work_item_id", "Work item ID", "string", bindable=True),
            _field("title", "Title", "string", bindable=True),
            _field("fallback_title", "Fallback title", "string", bindable=True),
            _field("body", "Body", "string", bindable=True, multiline=True),
            _field("draft", "Draft", "boolean", bindable=True),
            _field("fill", "Fill", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:pull-request:update",
        label="Pull request update",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("pr_id", "PR ID", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("ado_organization", "ADO organization", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("title", "Title", "string", bindable=True),
            _field("description", "Description", "string", bindable=True, multiline=True),
            _field("status", "Status", "string", bindable=True),
            _field("add_tags", "Add tags", "string_list", bindable=True),
            _field("remove_tags", "Remove tags", "string_list", bindable=True),
            _field("expected_statuses", "Expected statuses", "string_list", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:pull-request:add-tag",
        label="Pull request add tag",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("pr_id", "PR ID", "string", required=True, bindable=True),
            _field("tag", "Tag", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("ado_organization", "ADO organization", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:pull-request:remove-tag",
        label="Pull request remove tag",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("pr_id", "PR ID", "string", required=True, bindable=True),
            _field("tag", "Tag", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("ado_organization", "ADO organization", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:vm:run-state",
        label="VM run state",
        fields=[
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:git:pr-note",
        label="Git PR note",
        fields=[
            _field(
                "managed_repo_id", "Managed repository ID", "string", required=True, bindable=True
            ),
            _field("commit_sha", "Commit SHA", "string", required=True, bindable=True),
            _field("notes_ref", "Notes ref", "string", required=True, bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:session-context",
        label="Session context",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:state:get",
        label="State get",
        fields=[
            _field(
                "namespace",
                "Namespace",
                "string",
                required=True,
                bindable=True,
                description="Workflow state namespace to read from.",
                placeholder="workflow",
            ),
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Workflow state key to read within the namespace.",
                placeholder="cursor",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:session:list",
        label="Session list",
        fields=[
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description=(
                    "Optional directory path such as root or root/team. When set, matches "
                    "sessions in that directory and its descendants."
                ),
            ),
            _field(
                "metadata_equals",
                "Metadata equals",
                "json",
                bindable=True,
                description=(
                    "Value: JSON object whose keys are metadata names or dot-path selectors "
                    "and whose values must match exactly."
                ),
            ),
            _field(
                "metadata_exists",
                "Metadata exists",
                "json",
                bindable=True,
                description=(
                    "Value: metadata selector string, selector array, or JSON object. Object "
                    "values of true require presence; false requires absence."
                ),
            ),
            _field(
                "metadata_prefix",
                "Metadata prefix",
                "json",
                bindable=True,
                description=(
                    "Value: JSON object whose keys are metadata names or dot-path selectors "
                    "and whose values are required string prefixes. Arrays match when any "
                    "item starts with the prefix."
                ),
            ),
            _field(
                "state",
                "State",
                "enum",
                bindable=True,
                options=[
                    _field_option("active", "Active"),
                    _field_option("expired", "Expired"),
                    _field_option("inactive", "Inactive"),
                    _field_option("all", "All"),
                    _field_option("any", "Any"),
                ],
                description=(
                    "Value: active, expired, inactive, all, or any. Empty behaves like all."
                ),
            ),
            _field(
                "active_only",
                "Active only",
                "boolean",
                bindable=True,
                description=(
                    "Value: boolean. Set true to return only sessions whose expires_at is "
                    "still in the future."
                ),
            ),
            _field(
                "limit",
                "Limit",
                "number",
                bindable=True,
                description="Value: positive integer maximum number of matched sessions to return.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:model-usage:list",
        label="Model usage list",
        fields=[
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description=(
                    "Optional session filter. Leave blank to include usage from every session "
                    "that matches the other filters."
                ),
            ),
            _field(
                "workflow_run_id",
                "Workflow run ID",
                "string",
                bindable=True,
                description=(
                    "Optional workflow run filter. To scope the query to the current run, bind "
                    "this to ${{system.execution.trace_id}}. Leave blank to include every run "
                    "that matches the other filters."
                ),
            ),
            _field(
                "effect_id",
                "Effect ID",
                "string",
                bindable=True,
                description=(
                    "Optional effect node ID filter, for example effect-agent-prompt-1. Leave "
                    "blank to include usage from all effects in the selected run, session, or "
                    "directory scope."
                ),
            ),
            _field(
                "dispatch_id",
                "Dispatch ID",
                "string",
                bindable=True,
                description=(
                    "Optional prompt dispatch ID filter. Use this only when narrowing to one "
                    "agent prompt dispatch; leave blank for all dispatches in scope."
                ),
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description=(
                    "Optional directory filter such as root or root/team. Leave blank to include "
                    "all directories. When set, matches only that directory unless matching "
                    "descendants is enabled."
                ),
            ),
            _field(
                "include_directory_descendants",
                "Include directory descendants",
                "boolean",
                bindable=True,
                default_value=False,
                description=(
                    "Only applies when Directory path is set. When enabled, a filter such as "
                    "root/team also includes usage from root/team/subteam."
                ),
            ),
            _field(
                "provider",
                "Provider",
                "string",
                bindable=True,
                description=(
                    "Optional provider filter. The built-in relay currently records OpenAI "
                    "usage as openai; leave blank for all providers."
                ),
                options=[_field_option("openai", "openai")],
            ),
            _field(
                "model",
                "Model",
                "string",
                bindable=True,
                description=(
                    "Optional exact model filter using the provider response or correlated "
                    "session model value. Leave blank for all models."
                ),
            ),
            _field(
                "agent_harness",
                "Agent harness",
                "string",
                bindable=True,
                description=(
                    "Optional harness filter. Prompt effects currently record Codex requests as "
                    "codex; leave blank for every harness."
                ),
                options=[_field_option("codex", "codex")],
            ),
            _field(
                "execution_backend",
                "Execution backend",
                "string",
                bindable=True,
                description=(
                    "Optional execution backend filter. Use libvirt_vm for VM-backed agents or "
                    "container_worker_vm for container worker sessions; leave blank for both."
                ),
                options=[
                    _field_option("libvirt_vm", "libvirt_vm"),
                    _field_option("container_worker_vm", "container_worker_vm"),
                ],
            ),
            _field(
                "normalized_usage_status",
                "Normalized usage status",
                "enum",
                bindable=True,
                description=(
                    "Optional usage parsing filter. available means token usage was parsed from "
                    "the provider response; usage_unavailable means the request was recorded "
                    "without token usage."
                ),
                options=[
                    _field_option("available", "available"),
                    _field_option("usage_unavailable", "usage_unavailable"),
                ],
            ),
            _field(
                "request_status",
                "Request status",
                "enum",
                bindable=True,
                description=(
                    "Optional request outcome filter recorded by the relay. Leave blank to "
                    "include every request status."
                ),
                options=[
                    _field_option("success", "success"),
                    _field_option("failed", "failed"),
                    _field_option("error", "error"),
                    _field_option("cancelled", "cancelled"),
                ],
            ),
            _field(
                "occurred_after",
                "Occurred after",
                "string",
                bindable=True,
                description=(
                    "Optional lower bound for occurred_at, expressed as an ISO-8601 timestamp. "
                    "Leave blank for no start time filter."
                ),
                placeholder="2026-05-24T12:00:00Z",
            ),
            _field(
                "occurred_before",
                "Occurred before",
                "string",
                bindable=True,
                description=(
                    "Optional upper bound for occurred_at, expressed as an ISO-8601 timestamp. "
                    "Leave blank for no end time filter."
                ),
                placeholder="2026-05-24T13:00:00Z",
            ),
            _field(
                "limit",
                "Limit",
                "number",
                bindable=True,
                description=(
                    "Optional maximum number of events to return. Must be a positive integer; "
                    "results are ordered from oldest to newest."
                ),
            ),
            _field(
                "include_costs",
                "Include costs",
                "boolean",
                bindable=True,
                default_value=True,
                description=(
                    "Value: boolean. When enabled, adds query-time OpenAI cost estimates using "
                    "the configured local pricing catalog."
                ),
            ),
            _field(
                "cost_currency",
                "Cost currency",
                "enum",
                bindable=True,
                default_value="usd",
                description=(
                    "Currency for cost estimates. The built-in pricing catalog supports usd."
                ),
                options=[_field_option("usd", "usd")],
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:agent:artifact-text",
        label="Agent artifact text",
        fields=[
            _field("session_id", "Session ID", "string", required=True, bindable=True),
            _field(
                "path",
                "Artifact path",
                "string",
                required=True,
                bindable=True,
                description="Relative path inside the resolved session artifacts folder.",
                placeholder="report/summary.txt",
            ),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:issue:get",
        label="Issue get",
        fields=[
            _field("issue_id", "Issue ID", "string", required=True, bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:issue:list",
        label="Issue list",
        fields=[
            _field("directory_path", "Directory path", "string", bindable=True),
            _field(
                "direct_only",
                "Direct only",
                "boolean",
                bindable=True,
            ),
            _field("states", "States", "string_list", bindable=True),
            _field("exclude_states", "Exclude states", "string_list", bindable=True),
            _field("source", "Source", "string", bindable=True),
            _field("source_type", "Source type", "string", bindable=True),
            _field("tags_any", "Tags any", "string_list", bindable=True),
            _field("tags_all", "Tags all", "string_list", bindable=True),
            _field("metadata_equals", "Metadata equals", "json", bindable=True),
            _field("metadata_exists", "Metadata exists", "json", bindable=True),
            _field("metadata_prefix", "Metadata prefix", "json", bindable=True),
            _field("limit", "Limit", "number", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:pull-request:list",
        label="Pull request list",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("ado_organization", "ADO organization", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("status", "Status", "string", bindable=True),
            _field("creator_id", "Creator ID", "string", bindable=True),
            _field("reviewer_id", "Reviewer ID", "string", bindable=True),
            _field("source_ref_name", "Source ref", "string", bindable=True),
            _field("target_ref_name", "Target ref", "string", bindable=True),
            _field("source_repository_id", "Source repository ID", "string", bindable=True),
            _field("repository_id", "Repository ID", "string", bindable=True),
            _field("min_time", "Min time", "string", bindable=True),
            _field("max_time", "Max time", "string", bindable=True),
            _field("query_time_range_type", "Time range type", "string", bindable=True),
            _field("include_links", "Include links", "boolean", bindable=True),
            _field("max_comment_length", "Max comment length", "number", bindable=True),
            _field(
                "tags",
                "Tags",
                "string",
                bindable=True,
                description="Tag expression with and, or, not, and parentheses.",
                placeholder="(TagA or TagB) and not TagC",
            ),
            _field("top", "Top", "number", bindable=True),
            _field("skip", "Skip", "number", bindable=True),
            _field("cursor", "Cursor", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:pull-request:get",
        label="Pull request get",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                    _field_option("github", "GitHub"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("pr_id", "PR ID", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("ado_organization", "ADO organization", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("include_reviewers", "Include reviewers", "boolean", bindable=True),
            _field("include_commits", "Include commits", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:pull-request:work-items",
        label="Pull request work items",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("pr_id", "PR ID", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("ado_organization", "ADO organization", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:pull-request:feedback",
        label="Pull request feedback",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                    _field_option("github", "GitHub"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("pr_id", "PR ID", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("ado_organization", "ADO organization", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("cursor", "Cursor", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:communication:messages",
        label="Communication messages",
        fields=[
            _field(
                "communication_target_id",
                "Communication target",
                "enum",
                required=True,
                description=("Select a read-capable Teams or Discord communication target."),
            ),
            _field(
                "limit",
                "Limit",
                "number",
                bindable=True,
                description="Default 50. Maximum 200.",
                default_value=50,
            ),
            _field("cursor", "Cursor", "string", bindable=True),
            _field("before_cursor", "Before cursor", "string", bindable=True),
            _field(
                "thread_id",
                "Thread ID",
                "string",
                bindable=True,
                description=(
                    "Filters Teams channel-thread replies; Discord targets reject thread_id."
                ),
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description="Directory path used to resolve the communication target.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:ado:work-items",
        label="ADO work items",
        fields=[
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _ado_query_mode_field(),
            _field(
                "wiql",
                "WIQL",
                "string",
                bindable=True,
                multiline=True,
                description="Manual WIQL mode only. Structured filter fields are not applied.",
            ),
            _field(
                "current_iteration",
                "Current iteration",
                "boolean",
                bindable=True,
                description="Structured filters mode only. Resolve the team's current iteration before querying.",
            ),
            _field("team", "Team", "string", bindable=True),
            _field("iteration", "Iteration path", "string", bindable=True),
            _field("area_path", "Area path", "string", bindable=True),
            _field("states", "States", "string_list", description="Structured filters mode only."),
            _field("types", "Types", "string_list", description="Structured filters mode only."),
            _field(
                "tags_any", "Tags any", "string_list", description="Structured filters mode only."
            ),
            _field(
                "text_search",
                "Text search",
                "string",
                bindable=True,
                description="Structured filters mode only.",
            ),
            _field("top", "Top", "number", bindable=True),
            _field("skip", "Skip", "number", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:remote-issues",
        label="Remote issues",
        fields=[
            *_remote_issue_base_fields(bindable=True),
            _field("skip", "Skip", "number", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:semantic:store-status",
        label="Semantic store status",
        fields=[
            _field("collection_name", "Collection name", "string", bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:semantic:search",
        label="Semantic search",
        fields=[
            _field("collection_name", "Collection name", "string", required=True, bindable=True),
            _field("query_text", "Query text", "string", required=True, bindable=True),
            _field("filter", "Filter", "json", bindable=True),
            _field("limit", "Limit", "number", bindable=True),
            _field("score_threshold", "Score threshold", "number", bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:semantic:get",
        label="Semantic get",
        fields=[
            _field("collection_name", "Collection name", "string", required=True, bindable=True),
            _field(
                "point_ids",
                "Point IDs",
                "string_list",
                required=True,
                bindable=True,
                description="Explicit Qdrant point IDs as unsigned 64-bit integers or UUID strings.",
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
)
