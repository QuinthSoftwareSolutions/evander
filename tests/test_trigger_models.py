from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter

from agent_sandbox.events.models import (
    AgentPromptEffectDefinition,
    EffectDefinitionDiscriminated,
    GitPushTriggerDefinition,
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


def test_effect_requires_non_self_upstream() -> None:
    with pytest.raises(ValueError, match="effect cannot reference itself as upstream"):
        AgentPromptEffectDefinition.model_validate(
            {
                "id": "effect-1",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "upstream_id": "effect-1",
                "prompt": "hello",
            }
        )


def test_effect_discriminated_union_accepts_output_reference_interpolation() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-2",
            "display_name": "Prompt agent",
            "type": "effect:agent:prompt",
            "upstream_id": "trigger-1",
            "prompt": "Build summary for $(ticket_id)",
        }
    )

    assert effect.type == "effect:agent:prompt"
