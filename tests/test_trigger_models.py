from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter

from agent_sandbox.triggers.models import (
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
