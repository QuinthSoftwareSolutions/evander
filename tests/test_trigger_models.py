from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter

from agent_sandbox.events.models import (
    ConfigValue,
    EffectDefinitionDiscriminated,
    GateDefinitionDiscriminated,
    TriggerDefinitionDiscriminated,
    TriggerEvent,
    materialize_node_definition,
)
from agent_sandbox.events.provider_models import (
    AgentEnqueuePromptEffectDefinition,
    AgentPromptNewTriggerDefinition,
    AgentRunCompletedTriggerDefinition,
    ArtifactPublishArtifactsEffectDefinition,
    ArtifactStageEffectDefinition,
    CommunicationSendMessageEffectDefinition,
    GitPromoteAgentBranchEffectDefinition,
    GitPushTriggerDefinition,
    IssueUpdateStateEffectDefinition,
    NotificationSendEffectDefinition,
    PrCreateEffectDefinition,
    PullRequestCompletedTriggerDefinition,
    PullRequestNewFeedbackTriggerDefinition,
    ReviewNewRequestTriggerDefinition,
    ScriptRunRequestTriggerDefinition,
    SessionContextQueryDefinition,
    SessionApproveMcpToolsForRunEffectDefinition,
    SessionRenewIfExpiredEffectDefinition,
    SessionUpdateMetadataEffectDefinition,
)


def test_ticket_created_trigger_normalizes_directories() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    trigger = materialize_node_definition(
        adapter.validate_python(
            {
                "id": "t-1",
                "display_name": "Ticket created",
                "type": "trigger:ticket:created",
                "directories": [" root/team-a ", "", "root/team-a/subdir"],
            }
        )
    )

    assert trigger.directories == ["root/team-a", "root/team-a/subdir"]


def test_issue_comment_trigger_normalizes_ado_config() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    trigger = materialize_node_definition(
        adapter.validate_python(
            {
                "id": "t-comment",
                "display_name": "Issue comment",
                "type": "trigger:issue:comment-created",
                "config": {
                    "provider": "azure_devops",
                    "organization_url": " https://dev.azure.com/example/ ",
                    "project": " APP ",
                    "directories": [" root/team-a "],
                },
            }
        )
    )

    assert trigger.provider == "ado"
    assert trigger.organization_url == "https://dev.azure.com/example"
    assert trigger.project == "APP"
    assert trigger.directories == ["root/team-a"]


def test_email_trigger_validates_provider_enum() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    with pytest.raises(ValueError, match="Unsupported email provider"):
        adapter.validate_python(
            {
                "id": "t-2",
                "display_name": "Email",
                "type": "trigger:email:new-message",
                "provider": "exchange",
                "account_identifier": "ops@example.com",
                "mailbox_ids": ["primary"],
                "auth_secret_refs": {"access_token": "EMAIL_ACCESS_TOKEN"},
            }
        )


def test_teams_trigger_normalizes_filters() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    trigger = materialize_node_definition(
        adapter.validate_python(
            {
                "id": "t-teams",
                "display_name": "Teams",
                "type": "trigger:teams:new-message",
                "targets": [{"target": " team-a/channel-1 "}, {"target": "team-b/channel-9/"}],
                "user_ids": " user-a ",
            }
        )
    )

    assert [item.target for item in trigger.targets] == ["team-a/channel-1", "team-b/channel-9"]
    assert trigger.user_ids == ["user-a"]


def test_teams_trigger_accepts_per_target_message_filter() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    trigger = materialize_node_definition(
        adapter.validate_python(
            {
                "id": "t-teams",
                "display_name": "Teams",
                "type": "trigger:teams:new-message",
                "targets": [
                    {
                        "target": "chat:19:abc@thread.v2",
                        "message_filter": {
                            "mentions": {"user_ids": [" 143e5b02-868f-4f0c-a262-cf509b6c09e5 "]}
                        },
                    }
                ],
            }
        )
    )

    assert trigger.targets[0].message_filter is not None
    assert trigger.targets[0].message_filter.mentions is not None
    assert trigger.targets[0].message_filter.mentions.user_ids == [
        "143e5b02-868f-4f0c-a262-cf509b6c09e5"
    ]


def test_teams_trigger_message_filter_requires_content_or_mentions() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    with pytest.raises(ValueError, match="content or mentions"):
        adapter.validate_python(
            {
                "id": "t-teams",
                "display_name": "Teams",
                "type": "trigger:teams:new-message",
                "targets": [
                    {
                        "target": "chat:19:abc@thread.v2",
                        "message_filter": {},
                    }
                ],
            }
        )


def test_teams_trigger_requires_targets() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    with pytest.raises(ValueError, match="targets"):
        adapter.validate_python(
            {
                "id": "t-teams-missing",
                "display_name": "Teams",
                "type": "trigger:teams:new-message",
            }
        )


def test_pr_feedback_trigger_requires_org_url_for_ado() -> None:
    with pytest.raises(ValueError, match="organization_url is required"):
        PullRequestNewFeedbackTriggerDefinition.model_validate(
            {
                "id": "t-3",
                "display_name": "PR feedback",
                "type": "trigger:pull-request:new-feedback",
                "provider": "ado",
            }
        )


def test_pr_completed_trigger_requires_org_url_for_ado() -> None:
    with pytest.raises(ValueError, match="organization_url is required"):
        PullRequestCompletedTriggerDefinition.model_validate(
            {
                "id": "t-pr-done",
                "display_name": "PR completed",
                "type": "trigger:pull-request:completed",
                "provider": "ado",
            }
        )


def test_git_push_trigger_requires_repo_path_or_glob() -> None:
    with pytest.raises(ValueError, match="path_to_repo or path_glob"):
        GitPushTriggerDefinition.model_validate(
            {
                "id": "t-3b",
                "display_name": "Git push",
                "type": "trigger:git:push",
            }
        )


def test_git_push_trigger_rejects_both_repo_path_and_glob() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        GitPushTriggerDefinition.model_validate(
            {
                "id": "t-3c",
                "display_name": "Git push",
                "type": "trigger:git:push",
                "path_to_repo": "/tmp/repo.git",
                "path_glob": "/tmp/*.git",
            }
        )


def test_email_trigger_requires_account_and_mailboxes() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    with pytest.raises(ValueError, match="account_identifier"):
        adapter.validate_python(
            {
                "id": "t-2b",
                "display_name": "Email",
                "type": "trigger:email:new-message",
                "provider": "gmail",
                "mailbox_ids": ["inbox"],
                "auth_secret_refs": {"access_token": "EMAIL_ACCESS_TOKEN"},
            }
        )

    with pytest.raises(ValueError, match="mailbox_ids"):
        adapter.validate_python(
            {
                "id": "t-2c",
                "display_name": "Email",
                "type": "trigger:email:new-message",
                "provider": "gmail",
                "account_identifier": "ops@example.com",
                "auth_secret_refs": {"access_token": "EMAIL_ACCESS_TOKEN"},
            }
        )


def test_pr_feedback_trigger_accepts_local_provider() -> None:
    trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "t-4",
            "display_name": "Local PR feedback",
            "type": "trigger:pull-request:new-feedback",
            "provider": "local",
        }
    )

    assert trigger.provider == "local"


def test_review_request_trigger_validates() -> None:
    trigger = ReviewNewRequestTriggerDefinition.model_validate(
        {
            "id": "review-1",
            "display_name": "Review request",
            "type": "trigger:review:new-request",
        }
    )

    assert trigger.type == "trigger:review:new-request"


def test_script_run_request_trigger_validates() -> None:
    trigger = ScriptRunRequestTriggerDefinition.model_validate(
        {
            "id": "script-run-1",
            "display_name": "Script run request",
            "type": "trigger:script-run-request",
        }
    )

    assert trigger.type == "trigger:script-run-request"


def test_agent_prompt_new_trigger_validates() -> None:
    trigger = AgentPromptNewTriggerDefinition.model_validate(
        {
            "id": "agent-prompt-1",
            "display_name": "Agent prompt",
            "type": "trigger:agent:prompt:new",
        }
    )

    assert trigger.type == "trigger:agent:prompt:new"


def test_await_agent_gate_requires_session_id_or_vm_name() -> None:
    adapter = TypeAdapter(GateDefinitionDiscriminated)

    with pytest.raises(ValueError, match="gate:await-agent requires session_id or vm_name"):
        adapter.validate_python(
            {
                "id": "g-1",
                "display_name": "Await agent",
                "type": "gate:await-agent",
                "outputs": {},
            }
        )


def test_await_agent_gate_accepts_single_selector() -> None:
    adapter = TypeAdapter(GateDefinitionDiscriminated)

    gate = materialize_node_definition(
        adapter.validate_python(
            {
                "id": "g-2",
                "display_name": "Await agent",
                "type": "gate:await-agent",
                "session_id": "${{effect-1.session_id}}",
                "outputs": {},
            }
        )
    )

    assert gate.session_id == "${{effect-1.session_id}}"
    assert gate.vm_name is None


def test_trigger_event_envelope_accepts_normalized_payload() -> None:
    event = TriggerEvent.model_validate(
        {
            "event_id": "event-1",
            "trigger_id": "t-1",
            "trigger_type": "trigger:git:push",
            "occurred_at": datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc),
            "payload": {"branch": "main"},
        }
    )

    assert event.payload == {"branch": "main"}


def test_declared_outputs_reject_reserved_output_name() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    with pytest.raises(ValueError, match="reserved output name"):
        adapter.validate_python(
            {
                "id": "t-reserved",
                "display_name": "Reserved output",
                "type": "trigger:cron",
                "expression": "* * * * *",
                "outputs": {"__node_outputs": {"type": "object"}},
            }
        )


def test_effect_discriminated_union_accepts_output_reference_interpolation() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-2",
            "display_name": "Prompt agent",
            "type": "effect:agent:prompt",
            "prompt": "Build summary for ${{trigger.ticket_id}}",
            "model": "${{trigger.model}}",
        }
    )

    assert effect.type == "effect:agent:prompt"
    assert effect.model == "${{trigger.model}}"


def test_agent_run_completed_trigger_accepts_default_vm_user() -> None:
    trigger = AgentRunCompletedTriggerDefinition.model_validate(
        {
            "id": "t-agent-done",
            "display_name": "Agent completed",
            "type": "trigger:agent:run:completed",
        }
    )

    assert trigger.vm_user == "agent"


def test_vm_exec_effect_accepts_command_list() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-exec",
            "display_name": "Exec",
            "type": "effect:vm:exec",
            "vm_name": "vm-1",
            "command": ["python3", "--version"],
        }
    )

    assert effect.type == "effect:vm:exec"


def test_vm_create_effect_accepts_labels_mapping() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-vm",
            "display_name": "Create VM",
            "type": "effect:vm:create",
            "vm_name": "vm-1",
            "labels": {
                "workflow": "issue-worker",
                "ticket_id": "${{trigger.ticket_id}}",
            },
        }
    )

    assert effect.type == "effect:vm:create"
    assert effect.labels == {"workflow": "issue-worker", "ticket_id": "${{trigger.ticket_id}}"}


def test_vm_create_effect_accepts_allowed_tools_list() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-vm-tools",
            "display_name": "Create VM",
            "type": "effect:vm:create",
            "vm_name": "vm-1",
            "allowed_tools": ["list_unread_feedback", "post_pr_feedback_reply"],
        }
    )

    assert effect.type == "effect:vm:create"
    assert effect.allowed_tools == ["list_unread_feedback", "post_pr_feedback_reply"]


def test_vm_create_effect_accepts_allowed_script_names_list() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-vm-tools",
            "display_name": "Create VM",
            "type": "effect:vm:create",
            "vm_name": "vm-1",
            "allowed_script_names": ["deploy-review", "collect-logs"],
        }
    )

    assert effect.type == "effect:vm:create"
    assert effect.allowed_script_names == ["deploy-review", "collect-logs"]


def test_vm_create_effect_accepts_codex_skills_list() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-vm-skills",
            "display_name": "Create VM",
            "type": "effect:vm:create",
            "vm_name": "vm-1",
            "codex_skills": ["playwright", "playwright-interactive"],
        }
    )

    assert effect.type == "effect:vm:create"
    assert effect.codex_skills == ["playwright", "playwright-interactive"]


def test_vm_start_effect_accepts_session_target() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-start",
            "display_name": "Start VM",
            "type": "effect:vm:start",
            "session_id": "session-1",
        }
    )

    assert effect.type == "effect:vm:start"


def test_vm_stop_effect_accepts_timeout_override() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-stop",
            "display_name": "Stop VM",
            "type": "effect:vm:stop",
            "vm_name": "vm-1",
            "timeout_seconds": 120,
        }
    )

    assert effect.type == "effect:vm:stop"


def test_vm_copy_files_effect_requires_exactly_one_source_kind() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    with pytest.raises(ValueError, match="either source_path or content is required"):
        adapter.validate_python(
            {
                "id": "effect-copy",
                "display_name": "Copy",
                "type": "effect:vm:copy-files",
                "vm_name": "vm-1",
                "files": [{"destination_path": "/tmp/example.txt"}],
            }
        )


def test_pr_create_effect_requires_host_repo_path() -> None:
    effect = PrCreateEffectDefinition.model_validate(
        {
            "id": "effect-pr",
            "display_name": "Create PR",
            "type": "effect:pr:create",
            "host_repo_path": "/repo",
        }
    )

    assert effect.host_repo_path == "/repo"

    with pytest.raises(ValueError, match="Field required"):
        PrCreateEffectDefinition.model_validate(
            {
                "id": "effect-pr-missing-path",
                "display_name": "Create PR",
                "type": "effect:pr:create",
            }
        )


def test_notification_send_effect_requires_target_and_message() -> None:
    communication = CommunicationSendMessageEffectDefinition.model_validate(
        {
            "id": "effect-send-message",
            "display_name": "Send message",
            "type": "effect:communication:send-message",
            "communication_target_id": "Teams-Primary",
            "message": "Done",
        }
    )
    assert communication.communication_target_id == "teams-primary"

    with pytest.raises(ValueError, match="Field required"):
        CommunicationSendMessageEffectDefinition.model_validate(
            {
                "id": "effect-send-message-missing-message",
                "display_name": "Send message",
                "type": "effect:communication:send-message",
                "communication_target_id": "teams-primary",
            }
        )

    effect = NotificationSendEffectDefinition.model_validate(
        {
            "id": "effect-notify",
            "display_name": "Notify",
            "type": "effect:notification:send",
            "communication_target_id": "Teams-Primary",
            "message": "Done",
            "severity": "success",
        }
    )

    assert effect.communication_target_id == "teams-primary"
    assert effect.severity == "success"

    with pytest.raises(ValueError, match="Field required"):
        NotificationSendEffectDefinition.model_validate(
            {
                "id": "effect-notify-missing-message",
                "display_name": "Notify",
                "type": "effect:notification:send",
                "communication_target_id": "teams-primary",
            }
        )


def test_git_promote_agent_branch_effect_requires_repo_and_branch_fields() -> None:
    effect = GitPromoteAgentBranchEffectDefinition.model_validate(
        {
            "id": "effect-promote",
            "display_name": "Promote branch",
            "type": "effect:git:promote-agent-branch",
            "host_repo_path": "/repo",
            "bare_repo_path": "/git-remotes/repo.git",
            "agent_branch": "agents/session-1/topic",
        }
    )

    assert effect.host_repo_path == "/repo"
    assert effect.agent_branch == "agents/session-1/topic"

    with pytest.raises(ValueError, match="bare_repo_path or bare_repo_name"):
        GitPromoteAgentBranchEffectDefinition.model_validate(
            {
                "id": "effect-promote-missing-bare",
                "display_name": "Promote branch",
                "type": "effect:git:promote-agent-branch",
                "host_repo_path": "/repo",
                "agent_branch": "agents/session-1/topic",
            }
        )


def test_session_update_metadata_effect_and_session_context_query_validate() -> None:
    state_effect = IssueUpdateStateEffectDefinition.model_validate(
        {
            "id": "effect-issue-state",
            "display_name": "Mark issue in progress",
            "type": "effect:issue:update-state",
            "issue_id": "14084",
            "state": "In Progress",
        }
    )
    effect = SessionUpdateMetadataEffectDefinition.model_validate(
        {
            "id": "effect-session-meta",
            "display_name": "Store issue metadata",
            "type": "effect:session:update-metadata",
            "session_id": "session-1",
            "metadata": {"issue_number": "14084"},
        }
    )
    query = SessionContextQueryDefinition.model_validate(
        {
            "id": "query-session-context",
            "display_name": "Session context",
            "type": "query:session-context",
            "session_id": "session-1",
        }
    )

    assert state_effect.issue_id == "14084"
    assert state_effect.state == "In Progress"
    assert effect.metadata == {"issue_number": "14084"}
    assert query.session_id == "session-1"


def test_session_approve_mcp_tools_for_run_effect_validates_target_selector() -> None:
    effect = SessionApproveMcpToolsForRunEffectDefinition.model_validate(
        {
            "id": "effect-session-approve-tools",
            "display_name": "Approve tools",
            "type": "effect:session:approve-mcp-tools-for-run",
            "session_id": "session-1",
            "tools": ["run_script", "get_agent_status"],
            "expires_in_seconds": 600,
        }
    )

    assert effect.session_id == "session-1"
    assert effect.tools == ["run_script", "get_agent_status"]
    assert effect.expires_in_seconds == 600

    with pytest.raises(ValueError, match="target, session_id, or vm_name"):
        SessionApproveMcpToolsForRunEffectDefinition.model_validate(
            {
                "id": "effect-session-approve-tools",
                "display_name": "Approve tools",
                "type": "effect:session:approve-mcp-tools-for-run",
                "tools": ["run_script"],
            }
        )


def test_session_renew_if_expired_effect_validates_target_selector() -> None:
    effect = SessionRenewIfExpiredEffectDefinition.model_validate(
        {
            "id": "effect-session-renew",
            "display_name": "Renew expired session",
            "type": "effect:session:renew-if-expired",
            "session_id": "session-1",
            "extend_by": "8h",
        }
    )

    assert effect.session_id == "session-1"
    assert effect.extend_by == "8h"

    with pytest.raises(ValueError, match="target, session_id, or vm_name"):
        SessionRenewIfExpiredEffectDefinition.model_validate(
            {
                "id": "effect-session-renew",
                "display_name": "Renew expired session",
                "type": "effect:session:renew-if-expired",
            }
        )


def test_artifact_effects_validate_target_selectors() -> None:
    stage = ArtifactStageEffectDefinition.model_validate(
        {
            "id": "effect-artifact-stage",
            "display_name": "Stage artifact",
            "type": "effect:artifact:stage",
            "session_id": "session-1",
            "path": "repos/project/report.html",
        }
    )
    publish = ArtifactPublishArtifactsEffectDefinition.model_validate(
        {
            "id": "effect-artifact-publish",
            "display_name": "Publish artifacts",
            "type": "effect:artifact:publish-artifacts",
            "session_id": "session-1",
            "key": "runs/session-1/report",
        }
    )

    assert stage.path == "repos/project/report.html"
    assert publish.key == "runs/session-1/report"
    with pytest.raises(ValueError, match="target, session_id, or vm_name"):
        ArtifactStageEffectDefinition.model_validate(
            {
                "id": "effect-artifact-stage",
                "display_name": "Stage artifact",
                "type": "effect:artifact:stage",
                "path": "report.html",
            }
        )
    with pytest.raises(ValueError, match="target, session_id, or vm_name"):
        ArtifactPublishArtifactsEffectDefinition.model_validate(
            {
                "id": "effect-artifact-publish",
                "display_name": "Publish artifacts",
                "type": "effect:artifact:publish-artifacts",
                "key": "report",
            }
        )


def test_session_update_metadata_effect_allows_templated_metadata_reference() -> None:
    effect = SessionUpdateMetadataEffectDefinition.model_validate(
        {
            "id": "effect-session-meta",
            "display_name": "Store issue metadata",
            "type": "effect:session:update-metadata",
            "session_id": "session-1",
            "metadata": "${{effect-bash-script-1.session_metadata_patch}}",
        }
    )

    assert effect.metadata == "${{effect-bash-script-1.session_metadata_patch}}"


def test_agent_enqueue_prompt_accepts_resume_thread_metadata_key() -> None:
    effect = AgentEnqueuePromptEffectDefinition.model_validate(
        {
            "id": "effect-agent-enqueue",
            "display_name": "Enqueue agent",
            "type": "effect:agent:enqueue-prompt",
            "session_id": "session-1",
            "prompt": "continue",
            "resume_thread_metadata_key": "teams:${{trigger-teams.channel_target}}:agent_thread_id",
            "resume_thread_metadata_collection": "agent_thread_ids",
        }
    )

    assert effect.resume_thread_metadata_key == (
        "teams:${{trigger-teams.channel_target}}:agent_thread_id"
    )
    assert effect.resume_thread_metadata_collection == "agent_thread_ids"


def test_vm_copy_files_effect_rejects_mixed_source_kinds() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    with pytest.raises(ValueError, match="mutually exclusive"):
        adapter.validate_python(
            {
                "id": "effect-copy",
                "display_name": "Copy",
                "type": "effect:vm:copy-files",
                "vm_name": "vm-1",
                "files": [
                    {
                        "destination_path": "/tmp/example.txt",
                        "source_path": "/tmp/src.txt",
                        "content": "hello",
                    }
                ],
            }
        )


def test_config_value_adapter_accepts_nested_binding_structures() -> None:
    value = TypeAdapter(ConfigValue).validate_python(
        {
            "session_id": "${{node.session_id}}",
            "prompt": "Continue ${{node.session_id}}",
            "metadata": [{"ticket": "${{trigger.ticket_id}}"}, True, None],
        }
    )

    assert value == {
        "session_id": "${{node.session_id}}",
        "prompt": "Continue ${{node.session_id}}",
        "metadata": [{"ticket": "${{trigger.ticket_id}}"}, True, None],
    }
