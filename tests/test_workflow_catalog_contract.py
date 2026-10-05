"""Independent workflow catalog contracts, formerly one large HTTP assertion chain.

Every literal expectation is kept here. The small Admin Web integration test
checks routing and serialization; these cases diagnose individual node contracts.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from config_helpers import canonicalize_workflow_files, write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.models import ToolName


@pytest.fixture(scope="module")
def _catalog_snapshot(tmp_path_factory):
    project = tmp_path_factory.mktemp("workflow-catalog-contracts")
    service = build_admin_runtime(write_runtime_config(project))
    canonicalize_workflow_files(service.project_dir)
    return service.workflow_config_admin.read_workflow_editor_catalog(
        directory_path="root"
    ).model_dump(mode="json")


@pytest.fixture
def catalog_payload(_catalog_snapshot):
    # A case can never mutate the snapshot observed by another case.
    return deepcopy(_catalog_snapshot)


@pytest.fixture
def pr_trigger_field_keys():
    return [
        "provider",
        "repo",
        "directory_path",
        "ado_organization",
        "project",
        "status",
        "creator_id",
        "reviewer_id",
        "source_ref_name",
        "target_ref_name",
        "source_repository_id",
        "repository_id",
        "min_time",
        "max_time",
        "query_time_range_type",
        "include_links",
        "max_comment_length",
        "tags",
        "top",
        "skip",
        "cursor",
    ]


def test_workflow_catalog_descriptions(catalog_payload) -> None:
    assert any(node["type"] == "effect:vm:exec" for node in catalog_payload["nodes"])
    event_validator = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:event:validate")
    assert {port["key"] for port in event_validator["output_ports"]} == {"valid", "invalid", "error"}
    assert all(field["required"] and field["bindable"] for field in event_validator["fields"] if field["key"] in {"event_type", "payload"})
    generic_kind_tooltips = {
        "Triggers start workflow execution from incoming events.",
        "Gates transform, filter, fan out, or combine workflow data between nodes.",
        "Effects perform side effects such as VM actions, session updates, Git operations, or agent prompts.",
        "Queries read workflow or environment state without causing a side effect.",
    }
    descriptions_by_type = {
        node["type"]: node.get("description") for node in catalog_payload["nodes"]
    }
    assert all(descriptions_by_type[node["type"]] for node in catalog_payload["nodes"]), (
        descriptions_by_type
    )
    assert not any(
        descriptions_by_type[node["type"]] in generic_kind_tooltips
        for node in catalog_payload["nodes"]
    )
    assert (
        descriptions_by_type["effect:ado:pipeline-run"]
        == "Queue an Azure DevOps pipeline run and wait for its terminal result."
    )
    assert (
        descriptions_by_type["effect:http:request"]
        == "Perform a host-side raw HTTP GET, POST, or PUT request with URL allowlist control."
    )
    assert (
        descriptions_by_type["effect:runtime:diagnostics-snapshot"]
        == "Capture a bounded, redacted host runtime diagnostics snapshot from allowlisted sources."
    )
    assert (
        descriptions_by_type["effect:agent:exec"]
        == "Run a shell command inside an agent session workspace."
    )
    assert (
        descriptions_by_type["trigger:script-run-request"]
        == "Start from an approved run_script request submitted by a session."
    )


def test_workflow_catalog_effect_agent_exec(catalog_payload) -> None:
    agent_exec = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:exec"
    )
    assert [field["key"] for field in agent_exec["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "vm_user",
        "command",
        "working_directory",
        "environment",
        "stdin",
        "timeout_seconds",
        "check",
    ]
    agent_exec_command = next(field for field in agent_exec["fields"] if field["key"] == "command")
    assert agent_exec_command["multiline"] is True


def test_workflow_catalog_effect_semantic_ensure_collection(catalog_payload) -> None:
    semantic_ensure = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:semantic:ensure-collection"
    )
    assert [field["key"] for field in semantic_ensure["fields"]] == [
        "collection_name",
        "directory_path",
    ]


def test_workflow_catalog_effect_semantic_upsert(catalog_payload) -> None:
    semantic_upsert = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:semantic:upsert"
    )
    assert [field["key"] for field in semantic_upsert["fields"]] == [
        "collection_name",
        "point_id",
        "text",
        "payload",
        "directory_path",
        "wait",
    ]


def test_workflow_catalog_effect_semantic_delete(catalog_payload) -> None:
    semantic_delete = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:semantic:delete"
    )
    assert [field["key"] for field in semantic_delete["fields"]] == [
        "collection_name",
        "point_ids",
        "directory_path",
        "wait",
    ]


def test_workflow_catalog_effect_state_update(catalog_payload) -> None:
    state_update = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:state:update"
    )
    assert state_update["description"] == "Set, patch, or delete graph-local workflow state."
    assert [field["key"] for field in state_update["fields"]] == [
        "namespace",
        "key",
        "operation",
        "value",
        "patch",
        "expected_version",
    ]
    state_update_fields = {field["key"]: field for field in state_update["fields"]}
    assert state_update_fields["namespace"]["required"]
    assert state_update_fields["namespace"]["bindable"]
    assert state_update_fields["operation"]["kind"] == "enum"
    assert [option["value"] for option in state_update_fields["operation"]["options"]] == [
        "set",
        "patch",
        "delete",
    ]
    assert state_update_fields["value"]["kind"] == "json"
    assert state_update_fields["value"]["json_value_type"] == "object"
    assert state_update_fields["patch"]["kind"] == "json"
    assert state_update_fields["patch"]["json_value_type"] == "object"
    assert state_update_fields["expected_version"]["kind"] == "number"


def test_workflow_catalog_query_state_get(catalog_payload) -> None:
    state_get = next(node for node in catalog_payload["nodes"] if node["type"] == "query:state:get")
    assert state_get["description"] == (
        "Read graph-local workflow state for the current persisted workflow graph."
    )
    assert [field["key"] for field in state_get["fields"]] == [
        "namespace",
        "key",
    ]
    state_get_fields = {field["key"]: field for field in state_get["fields"]}
    assert state_get_fields["namespace"]["required"]
    assert state_get_fields["namespace"]["bindable"]
    assert state_get_fields["key"]["required"]
    assert state_get_fields["key"]["bindable"]


def test_workflow_catalog_effect_agent_copy_files(catalog_payload) -> None:
    agent_copy_files = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:copy-files"
    )
    agent_copy_fields = {field["key"]: field for field in agent_copy_files["fields"]}
    assert all(
        agent_copy_fields[key]["description"]
        for key in [
            "target",
            "session_id",
            "vm_name",
            "vm_user",
            "allowed_source_roots",
            "files",
        ]
    )
    agent_copy_file_fields = {
        field["key"]: field for field in agent_copy_fields["files"]["item_fields"]
    }
    assert all(
        agent_copy_file_fields[key]["description"]
        for key in [
            "source_path",
            "source_format",
            "content",
            "content_format",
            "destination_path",
            "mode",
            "overwrite",
            "merge_strategy",
            "section_id",
            "remove_section_ids",
        ]
    )
    assert agent_copy_file_fields["content"]["multiline"]
    assert "/home/agent" in agent_copy_file_fields["destination_path"]["description"]
    assert "0644" in agent_copy_file_fields["mode"]["description"]
    agent_copy_files = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:copy-files"
    )
    assert [field["key"] for field in agent_copy_files["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "vm_user",
        "allowed_source_roots",
        "files",
    ]


def test_workflow_catalog_effect_vm_delete_files(catalog_payload) -> None:
    vm_delete = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:delete-files"
    )
    assert [field["key"] for field in vm_delete["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "vm_user",
        "paths",
        "recursive",
        "missing_ok",
    ]


def test_workflow_catalog_effect_issue_update_state(catalog_payload) -> None:
    issue_state = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:issue:update-state"
    )
    assert [field["key"] for field in issue_state["fields"]] == [
        "issue_id",
        "state",
        "sync_proxy_cache",
    ]


def test_workflow_catalog_effect_issue_update_metadata(catalog_payload) -> None:
    issue_metadata = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:issue:update-metadata"
    )
    assert [field["key"] for field in issue_metadata["fields"]] == [
        "issue_id",
        "patch",
        "merge_patch",
        "expected_updated_at",
        "sync_proxy_cache",
    ]


def test_workflow_catalog_effect_ado_update_work_item(catalog_payload) -> None:
    ado_update = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:ado:update-work-item"
    )
    assert [field["key"] for field in ado_update["fields"]] == [
        "directory_path",
        "project",
        "work_item_id",
        "state",
        "fields",
        "expected_states",
        "sync_proxy_cache",
    ]


def test_workflow_catalog_effect_ado_add_work_item_tag(catalog_payload) -> None:
    ado_add_tag = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:ado:add-work-item-tag"
    )
    assert [field["key"] for field in ado_add_tag["fields"]] == [
        "directory_path",
        "project",
        "work_item_id",
        "tag",
        "sync_proxy_cache",
    ]


def test_workflow_catalog_effect_ado_remove_work_item_tag(catalog_payload) -> None:
    ado_remove_tag = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:ado:remove-work-item-tag"
    )
    assert [field["key"] for field in ado_remove_tag["fields"]] == [
        "directory_path",
        "project",
        "work_item_id",
        "tag",
        "sync_proxy_cache",
    ]


def test_workflow_catalog_effect_ado_post_work_item_comment(catalog_payload) -> None:
    ado_comment = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:ado:post-work-item-comment"
    )
    assert [field["key"] for field in ado_comment["fields"]] == [
        "directory_path",
        "project",
        "work_item_id",
        "comment",
        "mentions",
        "idempotency_key",
    ]
    ado_comment_fields = {field["key"]: field for field in ado_comment["fields"]}
    assert ado_comment_fields["comment"]["multiline"] is True
    assert ado_comment_fields["mentions"]["default_value"] == []
    assert ado_comment_fields["idempotency_key"]["required"] is False


def test_workflow_catalog_effect_pull_request_update(catalog_payload) -> None:
    pr_update = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:pull-request:update"
    )
    assert [field["key"] for field in pr_update["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "directory_path",
        "ado_organization",
        "project",
        "title",
        "description",
        "status",
        "add_tags",
        "remove_tags",
        "expected_statuses",
    ]


def test_workflow_catalog_effect_pull_request_reply(catalog_payload) -> None:
    pr_reply = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:pull-request:reply"
    )
    assert [field["key"] for field in pr_reply["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "feedback_kind",
        "thread_id",
        "comment_id",
        "body_text",
        "idempotency_key",
        "directory_path",
        "ado_organization",
        "project",
    ]
    assert pr_reply["fields"][6]["multiline"] is True
    assert pr_reply["fields"][7]["required"] is True


def test_workflow_catalog_effect_pull_request_add_tag(catalog_payload) -> None:
    pr_add_tag = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:pull-request:add-tag"
    )
    assert [field["key"] for field in pr_add_tag["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "tag",
        "directory_path",
        "ado_organization",
        "project",
    ]


def test_workflow_catalog_effect_pull_request_remove_tag(catalog_payload) -> None:
    pr_remove_tag = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:pull-request:remove-tag"
    )
    assert [field["key"] for field in pr_remove_tag["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "tag",
        "directory_path",
        "ado_organization",
        "project",
    ]


def test_workflow_catalog_effect_event_emit(catalog_payload) -> None:
    event_emit = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:event:emit"
    )
    assert [field["key"] for field in event_emit["fields"]] == [
        "event_type",
        "correlation_key",
        "payload",
    ]


def test_workflow_catalog_effect_event_schedule(catalog_payload) -> None:
    event_schedule = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:event:schedule"
    )
    assert [field["key"] for field in event_schedule["fields"]] == [
        "event_type",
        "correlation_key",
        "payload",
        "run_at",
        "delay_seconds",
        "idempotency_key",
    ]
    assert event_schedule["fields"][0]["bindable"] is True
    assert event_schedule["fields"][2]["required"] is True
    assert event_schedule["fields"][2]["json_value_type"] == "object"
    assert event_schedule["fields"][3]["bindable"] is True
    assert event_schedule["fields"][4]["bindable"] is True
    assert event_schedule["fields"][5]["bindable"] is True


def test_workflow_catalog_effect_communication_send_message(catalog_payload) -> None:
    communication = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:communication:send-message"
    )
    assert [field["key"] for field in communication["fields"]] == [
        "communication_target_id",
        "message",
        "thread_id",
        "sender_session_id",
    ]
    assert (
        next(
            field for field in communication["fields"] if field["key"] == "communication_target_id"
        )["kind"]
        == "enum"
    )
    assert any(
        field["key"] == "message" and field["bindable"] and field["multiline"]
        for field in communication["fields"]
    )


def test_workflow_catalog_effect_notification_send(catalog_payload) -> None:
    notification = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:notification:send"
    )
    assert [field["key"] for field in notification["fields"]] == [
        "communication_target_id",
        "message",
        "severity",
        "subject",
        "thread_id",
    ]
    assert any(
        field["key"] == "message" and field["bindable"] and field["multiline"]
        for field in notification["fields"]
    )


def test_workflow_catalog_effect_agent_prompt(catalog_payload) -> None:
    agent_prompt = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:prompt"
    )
    assert any(field["key"] == "model" and field["bindable"] for field in agent_prompt["fields"])
    assert any(
        field["key"] == "mcp_tool_approval_lease_id" and field["bindable"]
        for field in agent_prompt["fields"]
    )
    assert any(
        field["key"] == "resume_thread_metadata_collection" and field["bindable"]
        for field in agent_prompt["fields"]
    )
    speed = next(field for field in agent_prompt["fields"] if field["key"] == "fast_mode")
    assert speed["kind"] == "boolean"
    assert speed["bindable"] is True
    assert speed["required"] is False
    assert speed["metadata"]["nullable"] is True
    assert all(field["key"] != "output_mode" for field in agent_prompt["fields"])
    assert agent_prompt["output_ports"] == [
        {
            "key": "started",
            "label": "Started",
            "description": "Emits after the prompt has been dispatched.",
        },
        {
            "key": "completed",
            "label": "Completed",
            "description": "Emits after the agent run has completed.",
        },
        {
            "key": "error",
            "label": "Error",
            "description": (
                "Emits on dispatch or terminal execution failure, including container timeout, "
                "when an error route is connected."
            ),
        },
    ]


def test_workflow_catalog_effect_agent_enqueue_prompt(catalog_payload) -> None:
    agent_enqueue_prompt = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:enqueue-prompt"
    )
    assert [field["key"] for field in agent_enqueue_prompt["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "prompt",
        "model",
        "cost_category",
        "reasoning_effort",
        "fast_mode",
        "vm_user",
        "dispatch_id",
        "resume_thread_id",
        "resume_mode",
        "resume_thread_metadata_key",
        "resume_thread_metadata_collection",
        "mcp_tool_approval_lease_id",
        "queue_key",
    ]
    assert agent_enqueue_prompt["output_ports"] == [
        {
            "key": "started",
            "label": "Started",
            "description": "Emits after the queued prompt has been dispatched.",
        },
        {
            "key": "completed",
            "label": "Completed",
            "description": "Emits after the dispatched agent run has completed.",
        },
        {
            "key": "error",
            "label": "Error",
            "description": (
                "Emits on dispatch or terminal execution failure, including container timeout, "
                "when an error route is connected."
            ),
        },
    ]


def test_workflow_catalog_effect_session_approve_mcp_tools_for_run(catalog_payload) -> None:
    tool_approval = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:session:approve-mcp-tools-for-run"
    )
    assert [field["key"] for field in tool_approval["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "tools",
        "approval_notification_target_ids",
        "expires_in_seconds",
    ]
    assert any(
        field["key"] == "tools" and field["required"] and field["bindable"]
        for field in tool_approval["fields"]
    )
    approval_notification_target_ids = next(
        field
        for field in tool_approval["fields"]
        if field["key"] == "approval_notification_target_ids"
    )
    assert approval_notification_target_ids["kind"] == "string_list"
    assert approval_notification_target_ids["options"] == []


def test_workflow_catalog_effect_ado_pipeline_run(catalog_payload) -> None:
    ado_pipeline = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:ado:pipeline-run"
    )
    assert [field["key"] for field in ado_pipeline["fields"]] == [
        "directory_path",
        "pipeline_id",
        "project",
        "branch",
        "variables",
        "template_parameters",
        "timeout_seconds",
        "poll_interval_seconds",
        "check",
        "reject_running_parameters",
    ]


def test_workflow_catalog_effect_http_request(catalog_payload) -> None:
    http_request = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:http:request"
    )
    assert [field["key"] for field in http_request["fields"]] == [
        "method",
        "url",
        "allowed_url_patterns",
        "allow_private_networks",
        "payload",
        "headers",
        "timeout_seconds",
        "check",
    ]


def test_workflow_catalog_effect_runtime_diagnostics_snapshot(catalog_payload) -> None:
    http_request = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:http:request"
    )
    diagnostics = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:runtime:diagnostics-snapshot"
    )
    assert [field["key"] for field in diagnostics["fields"]] == [
        "since",
        "units",
        "include_failed_units",
        "include_journal",
        "include_runtime_files",
        "max_journal_lines_per_unit",
        "max_runtime_file_bytes",
        "command_timeout_seconds",
    ]
    http_request_fields = {field["key"]: field for field in http_request["fields"]}
    assert [option["value"] for option in http_request_fields["method"]["options"]] == [
        "GET",
        "POST",
        "PUT",
    ]
    assert http_request_fields["method"]["default_value"] == "GET"
    assert http_request_fields["url"]["required"] and http_request_fields["url"]["bindable"]
    assert http_request_fields["allowed_url_patterns"]["kind"] == "string_list"
    assert http_request_fields["allowed_url_patterns"]["default_value"] == ["*"]
    assert http_request_fields["allow_private_networks"]["kind"] == "boolean"
    assert http_request_fields["allow_private_networks"]["default_value"] is False
    assert http_request_fields["payload"]["json_value_type"] == "object"
    assert http_request_fields["headers"]["json_value_type"] == "object"
    assert http_request_fields["check"]["default_value"] is False


def test_workflow_catalog_trigger_approval_pending_created(catalog_payload) -> None:
    approval_request = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:approval:pending-created"
    )
    assert approval_request["label"] == "Pending approval"
    assert approval_request["description"] == "Start when a pending approval request is created."
    assert [field["key"] for field in approval_request["fields"]] == [
        "directory_path",
        "action_types",
        "tool_names",
    ]
    approval_request_fields = {field["key"]: field for field in approval_request["fields"]}
    assert approval_request_fields["directory_path"]["default_value"] == "root"
    assert approval_request_fields["action_types"]["default_value"] == []
    assert approval_request_fields["tool_names"]["default_value"] == []


def test_workflow_catalog_trigger_event(catalog_payload) -> None:
    internal_event = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:event"
    )
    assert [field["key"] for field in internal_event["fields"]] == [
        "event_type",
        "correlation_key",
    ]


def test_workflow_catalog_trigger_http_webhook(catalog_payload) -> None:
    webhook_trigger = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:http:webhook"
    )
    assert webhook_trigger["description"] == (
        "Start from a signed HTTP webhook delivered to a public endpoint."
    )
    assert [field["key"] for field in webhook_trigger["fields"]] == [
        "slug",
        "secret_env",
        "event_type",
    ]
    assert [field["required"] for field in webhook_trigger["fields"]] == [
        True,
        True,
        False,
    ]


def test_workflow_catalog_trigger_issue_comment_created(catalog_payload) -> None:
    issue_comment = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:issue:comment-created"
    )
    assert [field["key"] for field in issue_comment["fields"]] == [
        "provider",
        "organization_url",
        "project",
        "directories",
    ]


def test_workflow_catalog_trigger_ado_work_item_comment_created(catalog_payload) -> None:
    ado_comment = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:ado:work-item-comment-created"
    )
    assert ado_comment["label"] == "ADO work item comment"
    assert ado_comment["description"] == (
        "Start when a matching Azure DevOps work item receives a new matching comment."
    )
    assert [field["key"] for field in ado_comment["fields"]] == [
        "directory_path",
        "project",
        "query_mode",
        "wiql",
        "current_iteration",
        "team",
        "iteration",
        "area_path",
        "states",
        "types",
        "tags_any",
        "text_search",
        "top",
        "comment_contains",
        "comment_contains_any",
        "comment_author_exclude_names",
        "comment_author_exclude_ids",
        "comment_case_sensitive",
        "include_existing",
    ]


def test_workflow_catalog_trigger_remote_issue_matching(catalog_payload) -> None:
    remote_issue = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:remote-issue-matching"
    )
    assert remote_issue["label"] == "Remote issue matching"
    assert [field["key"] for field in remote_issue["fields"]] == [
        "provider",
        "directory_path",
        "project",
        "repo",
        "query_mode",
        "wiql",
        "current_iteration",
        "team",
        "iteration",
        "area_path",
        "states",
        "types",
        "tags_any",
        "state",
        "labels_any",
        "labels_all",
        "assignee",
        "author",
        "mentioned",
        "milestone",
        "since",
        "text_search",
        "top",
    ]


def test_workflow_catalog_trigger_remote_issue_comment_created(catalog_payload) -> None:
    ado_comment = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:ado:work-item-comment-created"
    )
    remote_issue_comment = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:remote-issue-comment-created"
    )
    assert [field["key"] for field in remote_issue_comment["fields"]][-6:] == [
        "comment_contains",
        "comment_contains_any",
        "comment_author_exclude_names",
        "comment_author_exclude_ids",
        "comment_case_sensitive",
        "include_existing",
    ]
    ado_comment_fields = {field["key"]: field for field in ado_comment["fields"]}
    assert ado_comment_fields["query_mode"]["default_value"] == "structured_filters"
    assert ado_comment_fields["wiql"]["multiline"] is True
    assert ado_comment_fields["top"]["default_value"] == 50
    assert ado_comment_fields["comment_contains_any"]["default_value"] == []
    assert ado_comment_fields["comment_case_sensitive"]["default_value"] is False
    assert ado_comment_fields["include_existing"]["default_value"] is False


def test_workflow_catalog_trigger_pull_request_new_feedback(
    catalog_payload, pr_trigger_field_keys
) -> None:
    pr_feedback_trigger = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:pull-request:new-feedback"
    )
    assert [field["key"] for field in pr_feedback_trigger["fields"]] == [
        *pr_trigger_field_keys,
        "agent_author_names",
        "agent_mention_names",
        "agent_author_ids",
        "agent_mention_ids",
        "suppress_agent_authored_feedback",
    ]


def test_workflow_catalog_trigger_pull_request_completed(
    catalog_payload, pr_trigger_field_keys
) -> None:
    pr_completed_trigger = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:pull-request:completed"
    )
    assert [field["key"] for field in pr_completed_trigger["fields"]] == pr_trigger_field_keys


def test_workflow_catalog_trigger_teams_new_message(catalog_payload) -> None:
    teams_message = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:teams:new-message"
    )
    assert [field["key"] for field in teams_message["fields"]] == ["targets", "user_ids"]


def test_workflow_catalog_trigger_ci_run_completed(catalog_payload) -> None:
    ci_run_completed = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:ci:run:completed"
    )
    assert [field["key"] for field in ci_run_completed["fields"]] == [
        "provider",
        "repo",
        "workflow",
        "ado_organization",
        "project",
        "pipeline_id",
        "pipeline_name",
        "branches",
        "conclusions",
        "top",
        "include_existing",
    ]
    assert next(field for field in ci_run_completed["fields"] if field["key"] == "provider")[
        "options"
    ] == [
        {"value": "github_actions", "label": "GitHub Actions"},
        {"value": "ado_pipelines", "label": "Azure DevOps Pipelines"},
    ]
    assert (
        next(field for field in ci_run_completed["fields"] if field["key"] == "include_existing")[
            "kind"
        ]
        == "boolean"
    )


def test_workflow_catalog_trigger_communication_new_message(catalog_payload) -> None:
    communication_message = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:communication:new-message"
    )
    assert [field["key"] for field in communication_message["fields"]] == [
        "communication_target_ids",
        "directory_path",
        "author_ids",
        "content_contains",
        "content_starts_with",
        "case_sensitive",
    ]
    assert (
        next(
            field
            for field in communication_message["fields"]
            if field["key"] == "communication_target_ids"
        )["kind"]
        == "string_list"
    )


def test_workflow_catalog_trigger_teams_direct_message(catalog_payload) -> None:
    teams_direct_message = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:teams:direct-message"
    )
    assert [field["key"] for field in teams_direct_message["fields"]] == [
        "chat_target_mode",
        "direct_chat_user_upns",
        "targets",
        "sender_user_ids",
        "message_filter",
    ]
    mode_field = next(
        field for field in teams_direct_message["fields"] if field["key"] == "chat_target_mode"
    )
    assert mode_field["default_value"] == "explicit"
    assert [option["value"] for option in mode_field["options"]] == ["direct", "explicit"]
    direct_chat_user_upns_field = next(
        field for field in teams_direct_message["fields"] if field["key"] == "direct_chat_user_upns"
    )
    assert direct_chat_user_upns_field["kind"] == "string_list"
    targets_field = next(
        field for field in teams_direct_message["fields"] if field["key"] == "targets"
    )
    assert targets_field["kind"] == "object_list"
    assert targets_field["required"] is True
    assert targets_field["item_fields"][0]["placeholder"] == "chat:<chat-id>"
    sender_user_ids_field = next(
        field for field in teams_direct_message["fields"] if field["key"] == "sender_user_ids"
    )
    assert sender_user_ids_field["label"] == "Allowed sender references"
    assert "UPNs, or mail addresses" in sender_user_ids_field["description"]


def test_workflow_catalog_gate_deserialize(catalog_payload) -> None:
    deserialize_gate = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:deserialize"
    )
    assert [field["key"] for field in deserialize_gate["fields"]] == [
        "content",
        "format",
        "outputs",
    ]
    assert deserialize_gate["fields"][0]["bindable"]
    assert [option["value"] for option in deserialize_gate["fields"][1]["options"]] == [
        "auto",
        "json",
        "yaml",
    ]
    assert deserialize_gate["output_ports"] == [
        {
            "key": "error",
            "label": "Error",
            "description": "Gate validation or runtime failure handled by an error route.",
        },
    ]


def test_workflow_catalog_gate_serialize(catalog_payload) -> None:
    serialize_gate = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:serialize"
    )
    assert [field["key"] for field in serialize_gate["fields"]] == [
        "value",
        "format",
        "outputs",
    ]
    assert serialize_gate["fields"][0]["bindable"]
    assert [option["value"] for option in serialize_gate["fields"][1]["options"]] == [
        "json",
        "yaml",
    ]


def test_workflow_catalog_gate_aggregate(catalog_payload) -> None:
    aggregate_gate = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:aggregate"
    )
    aggregate_fields = {field["key"]: field for field in aggregate_gate["fields"]}
    assert [field["key"] for field in aggregate_gate["fields"]] == [
        "items",
        "metrics",
        "outputs",
    ]
    assert aggregate_fields["items"]["kind"] == "json"
    assert aggregate_fields["items"]["required"]
    assert aggregate_fields["items"]["bindable"]
    assert aggregate_fields["items"]["json_value_type"] == "array"
    assert aggregate_fields["metrics"]["kind"] == "json"
    assert aggregate_fields["metrics"]["required"]
    assert aggregate_fields["metrics"]["json_value_type"] == "object"
    assert aggregate_fields["metrics"]["default_value"] == {
        "total": {"operation": "sum", "path": "value"}
    }
    assert aggregate_fields["outputs"]["read_only"]


def test_workflow_catalog_gate_buffer(catalog_payload) -> None:
    buffer_gate = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:buffer")
    buffer_fields = {field["key"]: field for field in buffer_gate["fields"]}
    assert buffer_gate["label"] == "Buffer"
    assert [field["key"] for field in buffer_gate["fields"]] == [
        "key",
        "count",
        "timeout",
        "outputs",
    ]
    assert buffer_fields["key"]["required"]
    assert buffer_fields["key"]["bindable"]
    assert buffer_fields["count"]["required"]
    assert buffer_fields["count"]["bindable"]
    assert buffer_fields["count"]["default_value"] == 10
    assert not buffer_fields["timeout"]["required"]
    assert buffer_fields["timeout"]["bindable"]
    assert buffer_fields["timeout"]["default_value"] == "PT10M"
    assert buffer_fields["outputs"]["read_only"]
    assert buffer_fields["outputs"]["default_value"] == {
        "items": {"type": "array"},
        "count": {"type": "integer"},
        "release_reason": {"type": "string"},
    }


def test_workflow_catalog_gate_await_agent(catalog_payload) -> None:
    await_agent = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:await-agent"
    )
    assert [field["key"] for field in await_agent["fields"]] == ["session_id", "vm_name", "outputs"]
    assert all(field["bindable"] for field in await_agent["fields"][:2])


def test_workflow_catalog_gate_for(catalog_payload) -> None:
    for_gate = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:for")
    for_gate_fields = {field["key"]: field for field in for_gate["fields"]}
    assert for_gate_fields["items"]["kind"] == "json"
    assert for_gate_fields["items"]["required"]
    assert for_gate_fields["items"]["bindable"]
    assert for_gate_fields["items"]["json_value_type"] == "array"
    assert for_gate_fields["items"]["default_value"] == []
    assert for_gate["output_ports"] == [
        {"key": "body", "label": "Body", "description": "Loop body path while items remain."},
        {"key": "done", "label": "Done", "description": "Exit path after the final item."},
        {
            "key": "error",
            "label": "Error",
            "description": "Gate validation or runtime failure handled by an error route.",
        },
    ]


def test_workflow_catalog_gate_while(catalog_payload) -> None:
    while_gate = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:while")
    while_gate_fields = {field["key"]: field for field in while_gate["fields"]}
    assert while_gate_fields["when"]["kind"] == "json"
    assert while_gate_fields["when"]["required"]
    assert while_gate_fields["when"]["bindable"]
    assert while_gate_fields["max_iterations"]["kind"] == "number"
    assert while_gate_fields["max_iterations"]["required"]
    assert while_gate_fields["max_iterations"]["bindable"]
    assert while_gate_fields["max_iterations"]["default_value"] == 3
    assert while_gate["output_ports"] == [
        {
            "key": "body",
            "label": "Body",
            "description": "Loop body path while the predicate is true.",
        },
        {"key": "done", "label": "Done", "description": "Exit path when the predicate is false."},
        {
            "key": "exhausted",
            "label": "Exhausted",
            "description": "Exit path when Max iterations is reached while the predicate is still true.",
        },
        {
            "key": "error",
            "label": "Error",
            "description": "Gate validation or runtime failure handled by an error route.",
        },
    ]


def test_workflow_catalog_gate_switch(catalog_payload) -> None:
    switch_gate = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:switch")
    assert switch_gate["output_ports"] == [
        {
            "key": "error",
            "label": "Error",
            "description": "Gate validation or runtime failure handled by an error route.",
        }
    ]
    assert [field["key"] for field in switch_gate["fields"]] == [
        "cases",
        "default_port",
        "outputs",
    ]
    switch_case_fields = {field["key"]: field for field in switch_gate["fields"][0]["item_fields"]}
    assert switch_gate["fields"][0]["kind"] == "object_list"
    assert switch_gate["fields"][0]["required"]
    assert [field["key"] for field in switch_gate["fields"][0]["item_fields"]] == [
        "port",
        "label",
        "when",
        "match",
    ]
    assert switch_case_fields["port"]["required"]
    assert switch_case_fields["when"]["bindable"]
    assert switch_case_fields["match"]["bindable"]
    assert not switch_gate["fields"][1]["required"]
    assert switch_gate["fields"][2]["read_only"]


def test_workflow_catalog_gate_validate(catalog_payload) -> None:
    validate_gate = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:validate"
    )
    assert validate_gate["output_ports"] == [
        {"key": "valid", "label": "Valid", "description": "All configured checks passed."},
        {
            "key": "invalid",
            "label": "Invalid",
            "description": "One or more checks failed; structured failures are available.",
        },
        {
            "key": "error",
            "label": "Error",
            "description": "Gate validation or runtime failure handled by an error route.",
        },
    ]
    assert [field["key"] for field in validate_gate["fields"]] == ["checks", "outputs"]
    validate_check_fields = validate_gate["fields"][0]
    assert validate_check_fields["kind"] == "object_list"
    assert validate_check_fields["required"]
    assert [field["key"] for field in validate_check_fields["item_fields"]] == [
        "code",
        "message",
        "when",
    ]
    assert validate_check_fields["item_fields"][2]["bindable"]
    assert validate_gate["fields"][1]["read_only"]
    assert validate_gate["fields"][1]["default_value"]["valid"] == {"type": "boolean"}
    assert validate_gate["fields"][1]["default_value"]["failures"]["type"] == "array"


def test_workflow_catalog_query_semantic_store_status(catalog_payload) -> None:
    semantic_status = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:semantic:store-status"
    )
    assert [field["key"] for field in semantic_status["fields"]] == [
        "collection_name",
        "directory_path",
    ]


def test_workflow_catalog_query_semantic_search(catalog_payload) -> None:
    semantic_search = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:semantic:search"
    )
    assert [field["key"] for field in semantic_search["fields"]] == [
        "collection_name",
        "query_text",
        "filter",
        "limit",
        "score_threshold",
        "directory_path",
    ]


def test_workflow_catalog_query_semantic_get(catalog_payload) -> None:
    semantic_get = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:semantic:get"
    )
    assert [field["key"] for field in semantic_get["fields"]] == [
        "collection_name",
        "point_ids",
        "directory_path",
    ]


def test_workflow_catalog_query_agent_artifact_text(catalog_payload) -> None:
    artifact_text = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:agent:artifact-text"
    )
    assert [field["key"] for field in artifact_text["fields"]] == [
        "session_id",
        "path",
        "vm_user",
    ]


def test_workflow_catalog_query_ado_work_items(catalog_payload) -> None:
    ado_work_items = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:ado:work-items"
    )
    assert [field["key"] for field in ado_work_items["fields"]] == [
        "directory_path",
        "project",
        "query_mode",
        "wiql",
        "current_iteration",
        "team",
        "iteration",
        "area_path",
        "states",
        "types",
        "tags_any",
        "text_search",
        "top",
        "skip",
    ]


def test_workflow_catalog_query_remote_issues(catalog_payload) -> None:
    remote_issues = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:remote-issues"
    )
    assert [field["key"] for field in remote_issues["fields"]] == [
        "provider",
        "directory_path",
        "project",
        "repo",
        "query_mode",
        "wiql",
        "current_iteration",
        "team",
        "iteration",
        "area_path",
        "states",
        "types",
        "tags_any",
        "state",
        "labels_any",
        "labels_all",
        "assignee",
        "author",
        "mentioned",
        "milestone",
        "since",
        "text_search",
        "top",
        "skip",
    ]


def test_workflow_catalog_query_pull_request_list(catalog_payload) -> None:
    pr_list = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:pull-request:list"
    )
    assert [field["key"] for field in pr_list["fields"]] == [
        "provider",
        "repo",
        "directory_path",
        "ado_organization",
        "project",
        "status",
        "creator_id",
        "reviewer_id",
        "source_ref_name",
        "target_ref_name",
        "source_repository_id",
        "repository_id",
        "min_time",
        "max_time",
        "query_time_range_type",
        "include_links",
        "max_comment_length",
        "tags",
        "top",
        "skip",
        "cursor",
    ]


def test_workflow_catalog_query_pull_request_get(catalog_payload) -> None:
    pr_get = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:pull-request:get"
    )
    assert [field["key"] for field in pr_get["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "directory_path",
        "ado_organization",
        "project",
        "include_reviewers",
        "include_commits",
    ]
    assert [option["value"] for option in pr_get["fields"][0]["options"]] == [
        "azure_devops",
        "ado",
        "github",
    ]


def test_workflow_catalog_query_pull_request_work_items(catalog_payload) -> None:
    pr_work_items = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:pull-request:work-items"
    )
    assert [field["key"] for field in pr_work_items["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "directory_path",
        "ado_organization",
        "project",
    ]
    assert [option["value"] for option in pr_work_items["fields"][0]["options"]] == [
        "azure_devops",
        "ado",
    ]


def test_workflow_catalog_query_session_list(catalog_payload) -> None:
    session_list = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:session:list"
    )
    assert [field["key"] for field in session_list["fields"]] == [
        "directory_path",
        "metadata_equals",
        "metadata_exists",
        "metadata_prefix",
        "state",
        "active_only",
        "limit",
    ]
    session_list_fields = {field["key"]: field for field in session_list["fields"]}
    assert all(field["description"] for field in session_list_fields.values())
    assert "descendants" in session_list_fields["directory_path"]["description"]
    assert "dot-path selectors" in session_list_fields["metadata_equals"]["description"]
    assert "presence" in session_list_fields["metadata_exists"]["description"]
    assert "string prefixes" in session_list_fields["metadata_prefix"]["description"]
    assert session_list_fields["state"]["kind"] == "enum"
    assert session_list_fields["state"]["bindable"] is True
    assert [option["value"] for option in session_list_fields["state"]["options"]] == [
        "active",
        "expired",
        "inactive",
        "all",
        "any",
    ]
    assert "active, expired, inactive, all, or any" in session_list_fields["state"]["description"]
    assert "expires_at" in session_list_fields["active_only"]["description"]
    assert "positive integer" in session_list_fields["limit"]["description"]


def test_workflow_catalog_query_model_usage_list(catalog_payload) -> None:
    model_usage_list = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:model-usage:list"
    )
    assert [field["key"] for field in model_usage_list["fields"]] == [
        "session_id",
        "workflow_run_id",
        "effect_id",
        "dispatch_id",
        "directory_path",
        "include_directory_descendants",
        "provider",
        "model",
        "agent_harness",
        "execution_backend",
        "normalized_usage_status",
        "request_status",
        "occurred_after",
        "occurred_before",
        "limit",
        "include_costs",
        "cost_currency",
    ]
    model_usage_list_fields = {field["key"]: field for field in model_usage_list["fields"]}
    assert all(field["description"] for field in model_usage_list_fields.values())
    assert "system.execution.trace_id" in model_usage_list_fields["workflow_run_id"]["description"]
    assert "every run" in model_usage_list_fields["workflow_run_id"]["description"]
    assert "all effects" in model_usage_list_fields["effect_id"]["description"]
    assert "all dispatches" in model_usage_list_fields["dispatch_id"]["description"]
    assert model_usage_list_fields["directory_path"]["description"]
    assert (
        "Leave blank to include all directories"
        in model_usage_list_fields["directory_path"]["description"]
    )
    assert "descendants" in model_usage_list_fields["directory_path"]["description"]
    assert model_usage_list_fields["include_directory_descendants"]["kind"] == "boolean"
    assert model_usage_list_fields["include_directory_descendants"]["default_value"] is False
    assert [option["value"] for option in model_usage_list_fields["provider"]["options"]] == [
        "openai"
    ]
    assert [option["value"] for option in model_usage_list_fields["agent_harness"]["options"]] == [
        "codex"
    ]
    assert [
        option["value"] for option in model_usage_list_fields["execution_backend"]["options"]
    ] == [
        "libvirt_vm",
        "container_worker_vm",
    ]
    assert model_usage_list_fields["normalized_usage_status"]["kind"] == "enum"
    assert [
        option["value"] for option in model_usage_list_fields["normalized_usage_status"]["options"]
    ] == [
        "available",
        "usage_unavailable",
    ]
    assert model_usage_list_fields["request_status"]["kind"] == "enum"
    assert [option["value"] for option in model_usage_list_fields["request_status"]["options"]] == [
        "success",
        "failed",
        "error",
        "cancelled",
    ]
    assert "ISO-8601" in model_usage_list_fields["occurred_after"]["description"]
    assert "positive integer" in model_usage_list_fields["limit"]["description"]
    assert model_usage_list_fields["include_costs"]["kind"] == "boolean"
    assert model_usage_list_fields["include_costs"]["default_value"] is True
    assert model_usage_list_fields["cost_currency"]["kind"] == "enum"
    assert model_usage_list_fields["cost_currency"]["default_value"] == "usd"
    assert [option["value"] for option in model_usage_list_fields["cost_currency"]["options"]] == [
        "usd"
    ]


def test_workflow_catalog_query_pull_request_feedback(catalog_payload) -> None:
    pr_feedback = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:pull-request:feedback"
    )
    assert [field["key"] for field in pr_feedback["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "directory_path",
        "ado_organization",
        "project",
        "cursor",
    ]


def test_workflow_catalog_query_communication_messages(catalog_payload) -> None:
    communication_messages = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:communication:messages"
    )
    assert [field["key"] for field in communication_messages["fields"]] == [
        "communication_target_id",
        "limit",
        "cursor",
        "before_cursor",
        "thread_id",
        "directory_path",
    ]
    communication_messages_fields = {
        field["key"]: field for field in communication_messages["fields"]
    }
    assert communication_messages_fields["communication_target_id"]["kind"] == "enum"
    assert (
        "read-capable Teams or Discord"
        in communication_messages_fields["communication_target_id"]["description"]
    )
    assert communication_messages_fields["limit"]["default_value"] == 50
    assert "Maximum 200" in communication_messages_fields["limit"]["description"]
    assert (
        "Discord targets reject thread_id"
        in communication_messages_fields["thread_id"]["description"]
    )


def test_workflow_catalog_effect_vm_create(catalog_payload) -> None:
    vm_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:create"
    )
    assert any(field["key"] == "allowed_script_names" for field in vm_create["fields"])
    assert any(field["key"] == "metadata" for field in vm_create["fields"])


def test_workflow_catalog_effect_agent_create(catalog_payload) -> None:
    vm_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:create"
    )
    agent_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:create"
    )
    assert {field["key"] for field in agent_create["fields"]} == {
        "execution_backend",
        "agent_provider",
        "provider",
        "vm_name",
        "session_id",
        "existing_agent_behavior",
        "labels",
        "model",
        "cost_category",
        "reasoning_effort",
        "fast_mode",
        "model_provider_mode",
        "codex_auth_mode",
        "subscription_profile_id",
        "vm_user",
        "vm_image",
        "ttl_hours",
        "allowed_tools",
        "connector_grants",
        "host_directory_grants",
        "tool_argument_policies",
        "allowed_script_names",
        "communication_target_ids",
        "email_mailbox_grants",
        "discord_channels",
        "teams_channel_allowlist",
        "teams_chat_allowlist",
        "teams_user_allowlist",
        "email_mailboxes",
        "email_folders",
        "communication_file_receive_enabled",
        "communication_file_send_enabled",
        "communication_file_require_approval_for_send",
        "communication_file_max_bytes",
        "web_search_enabled",
        "image_generation_enabled",
        "dotnet",
        "nuget",
        "mssql",
        "mssql_init_root",
        "playwright",
        "codex_skills",
        "container_image",
        "worker_id",
        "npm_registry_url",
        "directory_path",
        "metadata",
    }
    assert [field["key"] for field in agent_create["fields"][:3]] == [
        "execution_backend",
        "agent_provider",
        "provider",
    ]
    agent_create_fields = {field["key"]: field for field in agent_create["fields"]}
    assert agent_create_fields["execution_backend"]["required"] is True
    assert agent_create_fields["agent_provider"]["kind"] == "enum"
    assert agent_create_fields["agent_provider"]["options"] == [
        {"value": "codex", "label": "Codex"},
        {"value": "claude", "label": "Claude"},
        {
            "value": "copilot",
            "label": "Copilot",
            "metadata": {"execution_backends": ["libvirt_vm"]},
        },
    ]
    assert agent_create_fields["model_provider_mode"]["options"] == [
        {"value": "openai_public", "label": "Public OpenAI"},
        {"value": "local_openai", "label": "Local OpenAI"},
        {"value": "azure_openai", "label": "Azure OpenAI"},
        {"value": "openrouter", "label": "OpenRouter"},
    ]
    assert agent_create_fields["provider"]["label"] == "VM provider"
    assert agent_create_fields["provider"]["metadata"] == {"execution_backends": ["libvirt_vm"]}
    assert agent_create_fields["vm_name"]["metadata"] == {"execution_backends": ["libvirt_vm"]}
    assert agent_create_fields["vm_user"]["metadata"] == {"execution_backends": ["libvirt_vm"]}
    assert agent_create_fields["mssql"]["metadata"] == {"execution_backends": ["libvirt_vm"]}
    assert agent_create_fields["mssql_init_root"]["metadata"] == {
        "execution_backends": ["libvirt_vm"]
    }
    assert agent_create_fields["container_image"]["metadata"] == {
        "execution_backends": ["container_worker_vm"]
    }
    assert agent_create_fields["npm_registry_url"]["metadata"] == {
        "execution_backends": ["container_worker_vm"]
    }
    assert agent_create_fields["labels"]["kind"] == "string_map"
    assert agent_create_fields["labels"]["description"]
    assert agent_create_fields["communication_target_ids"]["kind"] == "string_list"
    assert "communication target" in agent_create_fields["communication_target_ids"]["description"]
    assert agent_create_fields["communication_target_ids"]["metadata"] == {"selection_only": True}
    assert agent_create_fields["metadata"]["kind"] == "json"
    assert agent_create_fields["metadata"]["json_value_type"] == "object"
    assert agent_create_fields["metadata"]["description"]
    assert agent_create_fields["directory_path"]["description"]
    assert agent_create_fields["container_image"]["bindable"]
    assert "approved container image" in agent_create_fields["container_image"]["description"]
    assert agent_create_fields["vm_image"]["bindable"]
    assert "libvirt-only selector" in agent_create_fields["vm_image"]["description"]
    assert "approved libvirt VM template" in agent_create_fields["vm_image"]["description"]
    assert "base-image" in agent_create_fields["vm_image"]["description"]
    assert agent_create_fields["npm_registry_url"]["bindable"]
    assert agent_create_fields["web_search_enabled"]["kind"] == "boolean"
    assert agent_create_fields["web_search_enabled"]["bindable"] is True
    assert "web_search" in agent_create_fields["web_search_enabled"]["description"]
    assert agent_create_fields["image_generation_enabled"]["kind"] == "boolean"
    assert agent_create_fields["image_generation_enabled"]["bindable"] is True
    assert (
        "generate and edit images"
        in (agent_create_fields["image_generation_enabled"]["description"])
    )
    assert agent_create_fields["allowed_tools"]["options"]
    assert {
        "value": ToolName.RUN_SCRIPT.value,
        "label": ToolName.RUN_SCRIPT.value.replace("_", " "),
    } in agent_create_fields["allowed_tools"]["options"]
    assert vm_create["fields"]
    vm_create_fields = {field["key"]: field for field in vm_create["fields"]}
    assert vm_create_fields["tool_argument_policies"]["options"]
    assert {
        "value": ToolName.RUN_SCRIPT.value,
        "label": ToolName.RUN_SCRIPT.value.replace("_", " "),
    } in vm_create_fields["tool_argument_policies"]["options"]
    assert agent_create_fields["tool_argument_policies"]["options"]
    assert {
        "value": ToolName.RUN_SCRIPT.value,
        "label": ToolName.RUN_SCRIPT.value.replace("_", " "),
    } in agent_create_fields["tool_argument_policies"]["options"]
    execution_backend = next(
        field for field in agent_create["fields"] if field["key"] == "execution_backend"
    )
    assert execution_backend["options"] == [
        {"value": "libvirt_vm", "label": "Libvirt VM"},
        {"value": "container_worker_vm", "label": "Container worker VM"},
    ]
    existing_agent_behavior = next(
        field for field in agent_create["fields"] if field["key"] == "existing_agent_behavior"
    )
    assert existing_agent_behavior["options"] == [
        {"value": "upsert", "label": "Upsert existing agent"},
        {"value": "noop", "label": "No-op if existing"},
    ]


def test_workflow_catalog_effect_agent_destroy(catalog_payload) -> None:
    agent_destroy = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:destroy"
    )
    assert [field["key"] for field in agent_destroy["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "execution_backend",
        "directory_path",
    ]


def test_workflow_catalog_effect_session_renew_if_expired(catalog_payload) -> None:
    session_renew = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:session:renew-if-expired"
    )
    assert [field["key"] for field in session_renew["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "extend_by",
        "renew_if_expires_within",
        "directory_path",
    ]
    session_renew_fields = {field["key"]: field for field in session_renew["fields"]}
    assert "from now" in session_renew_fields["extend_by"]["description"]
    assert "8h" in session_renew_fields["extend_by"]["description"]
    assert "PT8H" in session_renew_fields["extend_by"]["description"]
    assert "pre-expiry window" in session_renew_fields["renew_if_expires_within"]["description"]
    assert "30m" in session_renew_fields["renew_if_expires_within"]["description"]
    assert "4h" in session_renew_fields["renew_if_expires_within"]["description"]


def test_workflow_catalog_effect_artifact_stage(catalog_payload) -> None:
    artifact_stage = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:artifact:stage"
    )
    assert [field["key"] for field in artifact_stage["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "path",
        "destination_path",
        "vm_user",
    ]


def test_workflow_catalog_effect_artifact_publish_artifacts(catalog_payload) -> None:
    artifact_publish = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:artifact:publish-artifacts"
    )
    assert [field["key"] for field in artifact_publish["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "key",
        "source_paths",
        "vm_user",
    ]


def test_workflow_catalog_effect_artifact_import_published(catalog_payload) -> None:
    artifact_import = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:artifact:import-published"
    )
    assert [field["key"] for field in artifact_import["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "key",
        "destination_path",
        "overwrite",
        "vm_user",
    ]


def test_workflow_catalog_effect_agent_clone_repo(catalog_payload) -> None:
    agent_clone_repo = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:clone-repo"
    )
    assert [field["key"] for field in agent_clone_repo["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "managed_repo_id",
        "clone_branch",
        "vm_user",
        "git_access_mode",
        "git_access_lifetime",
        "pinned_ref",
        "pinned_commit_sha",
        "git_author_name",
        "git_author_email",
        "git_committer_name",
        "git_committer_email",
    ]


def test_workflow_catalog_effect_agent_grant_repo_access(catalog_payload) -> None:
    grant_repo_access = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:agent:grant-repo-access"
    )
    assert [field["key"] for field in grant_repo_access["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "managed_repo_id",
        "git_access_mode",
        "vm_user",
    ]


def test_workflow_catalog_effect_agent_bootstrap_frontend(catalog_payload) -> None:
    agent_bootstrap_frontend = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:agent:bootstrap-frontend"
    )
    assert [field["key"] for field in agent_bootstrap_frontend["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "host_checkout",
        "install_roots",
        "vm_user",
    ]


def test_workflow_catalog_effect_vm_clone_repo(catalog_payload) -> None:
    vm_clone_repo = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:clone-repo"
    )
    assert [field["key"] for field in vm_clone_repo["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "managed_repo_id",
        "clone_branch",
        "vm_user",
        "git_access_mode",
        "git_access_lifetime",
    ]


def test_workflow_catalog_effect_vm_bootstrap_python(catalog_payload) -> None:
    vm_bootstrap_python = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:bootstrap-python"
    )
    assert [field["key"] for field in vm_bootstrap_python["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "host_checkout",
        "target_dir",
        "vm_user",
    ]


def test_workflow_catalog_effect_vm_bootstrap_frontend(catalog_payload) -> None:
    vm_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:create"
    )
    vm_bootstrap_frontend = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:bootstrap-frontend"
    )
    assert [field["key"] for field in vm_bootstrap_frontend["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "host_checkout",
        "install_roots",
        "vm_user",
    ]
    assert {field["key"] for field in vm_create["fields"]} == {
        "vm_name",
        "session_id",
        "labels",
        "model",
        "vm_user",
        "ttl_hours",
        "allowed_tools",
        "connector_grants",
        "host_directory_grants",
        "tool_argument_policies",
        "allowed_script_names",
        "discord_channels",
        "email_mailboxes",
        "email_folders",
        "dotnet",
        "nuget",
        "mssql",
        "mssql_init_root",
        "playwright",
        "codex_skills",
        "directory_path",
        "metadata",
    }
