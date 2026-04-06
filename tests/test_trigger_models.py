from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter

from agent_sandbox.events.models import (
    AgentPromptEffectDefinition,
    AgentRunCompletedTriggerDefinition,
    ConfigValue,
    EffectDefinitionDiscriminated,
    GitPushTriggerDefinition,
    PrCreateEffectDefinition,
    PullRequestNewFeedbackTriggerDefinition,
    TriggerDefinitionDiscriminated,
    TriggerEvent,
)



def test_ticket_created_trigger_normalizes_directories() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    trigger = adapter.validate_python(
        {
            "id": "t-1",
            "display_name": "Ticket created",
            "type": "trigger:ticket:created",
            "directories": [" root/team-a ", "", "root/team-a/subdir"],
        }
    )

    assert trigger.directories == ["root/team-a", "root/team-a/subdir"]


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


def test_git_push_trigger_requires_repo_path() -> None:
    with pytest.raises(ValueError, match="path_to_repo"):
        GitPushTriggerDefinition.model_validate(
            {
                "id": "t-3b",
                "display_name": "Git push",
                "type": "trigger:git:push",
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
            "prompt": "Build summary for $(ticket_id)",
        }
    )

    assert effect.type == "effect:agent:prompt"


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
                "ticket_id": "$(ticket_id)",
            },
        }
    )

    assert effect.type == "effect:vm:create"
    assert effect.labels == {"workflow": "issue-worker", "ticket_id": "$(ticket_id)"}


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
            "session_id": "$(session_id)",
            "prompt": "Continue $(session_id)",
            "metadata": [{"ticket": "$(ticket_id)"}, True, None],
        }
    )

    assert value == {
        "session_id": "$(session_id)",
        "prompt": "Continue $(session_id)",
        "metadata": [{"ticket": "$(ticket_id)"}, True, None],
    }
