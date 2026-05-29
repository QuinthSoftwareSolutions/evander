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
    ArtifactImportPublishedEffectDefinition,
    ArtifactPublishArtifactsEffectDefinition,
    ArtifactStageEffectDefinition,
    CiRunCompletedTriggerDefinition,
    CommunicationNewMessageTriggerDefinition,
    CommunicationSendMessageEffectDefinition,
    GitPromoteAgentBranchEffectDefinition,
    GitPushTriggerDefinition,
    HttpRequestEffectDefinition,
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
    SessionRevokeMcpToolApprovalLeaseEffectDefinition,
    SessionUpdateMetadataEffectDefinition,
    WebhookTriggerDefinition,
)
from agent_sandbox.workflow.contracts import effective_node_output_schema


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


def test_webhook_trigger_defaults_event_type_from_slug() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    trigger = materialize_node_definition(
        adapter.validate_python(
            {
                "id": "webhook-1",
                "display_name": "Webhook",
                "type": "trigger:http:webhook",
                "slug": "build-notify",
                "secret_env": "WEBHOOK_SECRET",
            }
        )
    )

    assert isinstance(trigger, WebhookTriggerDefinition)
    assert trigger.slug == "build-notify"
    assert trigger.secret_env == "WEBHOOK_SECRET"
    assert trigger.event_type == "webhook.build-notify"
    assert trigger.config["slug"] == "build-notify"
    assert trigger.config["secret_env"] == "WEBHOOK_SECRET"
    assert trigger.config["event_type"] == "webhook.build-notify"


@pytest.mark.parametrize(
    "slug, expected_error",
    [
        ("", "slug must be a non-empty string"),
        ("not a slug", r"slug must match \[A-Za-z0-9_.-\]\+"),
    ],
)
def test_webhook_trigger_rejects_invalid_slug(slug: str, expected_error: str) -> None:
    with pytest.raises(ValueError, match=expected_error):
        WebhookTriggerDefinition.model_validate(
            {
                "id": "webhook-2",
                "display_name": "Webhook",
                "type": "trigger:http:webhook",
                "slug": slug,
                "secret_env": "WEBHOOK_SECRET",
            }
        )


def test_webhook_trigger_rejects_blank_secret_env() -> None:
    with pytest.raises(ValueError, match="secret_env must be a non-empty string"):
        WebhookTriggerDefinition.model_validate(
            {
                "id": "webhook-3",
                "display_name": "Webhook",
                "type": "trigger:http:webhook",
                "slug": "build-notify",
                "secret_env": "   ",
            }
        )


def test_webhook_trigger_rejects_reserved_external_event_type() -> None:
    with pytest.raises(ValueError, match="reserved 'external\\.' prefix"):
        WebhookTriggerDefinition.model_validate(
            {
                "id": "webhook-4",
                "display_name": "Webhook",
                "type": "trigger:http:webhook",
                "slug": "build-notify",
                "secret_env": "WEBHOOK_SECRET",
                "event_type": "external.webhook.build-notify",
            }
        )


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


def test_communication_trigger_normalizes_targets_and_authors() -> None:
    trigger = CommunicationNewMessageTriggerDefinition.model_validate(
        {
            "id": "comm-1",
            "display_name": "Communication",
            "type": "trigger:communication:new-message",
            "communication_target_ids": [" TEAM-A/CHANNEL-1 ", "discord:CHAN-2", ""],
            "directory_path": " root/team-a ",
            "author_ids": [" AUTHOR-A ", "Author-B", ""],
            "content_contains": "  Hello there  ",
        }
    )

    assert trigger.communication_target_ids == ["team-a/channel-1", "discord:chan-2"]
    assert trigger.directory_path == "root/team-a"
    assert trigger.author_ids == ["author-a", "author-b"]
    assert trigger.content_contains == "Hello there"
    assert trigger.case_sensitive is False


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": "comm-missing",
            "display_name": "Communication",
            "type": "trigger:communication:new-message",
        },
        {
            "id": "comm-empty",
            "display_name": "Communication",
            "type": "trigger:communication:new-message",
            "communication_target_ids": ["  ", ""],
        },
    ],
)
def test_communication_trigger_requires_target_ids(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="communication_target_ids"):
        CommunicationNewMessageTriggerDefinition.model_validate(payload)


def test_teams_direct_message_trigger_requires_sender_user_ids() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    trigger = materialize_node_definition(
        adapter.validate_python(
            {
                "id": "t-teams-dm",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
                "sender_user_ids": [" 143e5b02-868f-4f0c-a262-cf509b6c09e5 "],
                "message_filter": {"content": {"contains": "hello"}},
            }
        )
    )

    assert trigger.sender_user_ids == ["143e5b02-868f-4f0c-a262-cf509b6c09e5"]
    assert trigger.message_filter is not None
    assert trigger.message_filter.content is not None
    assert trigger.message_filter.content.contains == "hello"

    with pytest.raises(ValueError, match="sender_user_ids"):
        adapter.validate_python(
            {
                "id": "t-teams-dm-missing",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
            }
        )


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


@pytest.mark.parametrize(
    ("trigger_cls", "trigger_type"),
    [
        (
            PullRequestNewFeedbackTriggerDefinition,
            "trigger:pull-request:new-feedback",
        ),
        (
            PullRequestCompletedTriggerDefinition,
            "trigger:pull-request:completed",
        ),
    ],
)
def test_pr_triggers_accept_full_criteria_and_preserve_materialized_fields(
    trigger_cls: type[PullRequestNewFeedbackTriggerDefinition]
    | type[PullRequestCompletedTriggerDefinition],
    trigger_type: str,
) -> None:
    trigger = materialize_node_definition(
        TypeAdapter(TriggerDefinitionDiscriminated).validate_python(
            {
                "id": "t-pr",
                "display_name": "PR",
                "type": trigger_type,
                "provider": "ado",
                "repo": "repo-1",
                "directory_path": " root/team-a ",
                "organization_url": " https://dev.azure.com/example/ ",
                "project": " Sandbox ",
                "status": "active",
                "creator_id": "creator-1",
                "reviewer_id": "reviewer-1",
                "source_ref_name": "refs/heads/feature",
                "target_ref_name": "refs/heads/main",
                "source_repository_id": "source-repo-1",
                "repository_id": "repository-1",
                "min_time": "2026-05-01T00:00:00Z",
                "max_time": "2026-05-02T00:00:00Z",
                "query_time_range_type": "created",
                "include_links": False,
                "max_comment_length": 0,
                "tags": "needs-review",
                "top": 25,
                "skip": 5,
                "cursor": "cursor-1",
            }
        )
    )

    assert isinstance(trigger, trigger_cls)
    assert trigger.provider == "ado"
    assert trigger.repo == "repo-1"
    assert trigger.directory_path == " root/team-a "
    assert trigger.organization_url == "https://dev.azure.com/example"
    assert trigger.project == "Sandbox"
    assert trigger.status == "active"
    assert trigger.creator_id == "creator-1"
    assert trigger.reviewer_id == "reviewer-1"
    assert trigger.source_ref_name == "refs/heads/feature"
    assert trigger.target_ref_name == "refs/heads/main"
    assert trigger.source_repository_id == "source-repo-1"
    assert trigger.repository_id == "repository-1"
    assert trigger.min_time == "2026-05-01T00:00:00Z"
    assert trigger.max_time == "2026-05-02T00:00:00Z"
    assert trigger.query_time_range_type == "created"
    assert trigger.include_links is False
    assert trigger.max_comment_length == 0
    assert trigger.tags == "needs-review"
    assert trigger.top == 25
    assert trigger.skip == 5
    assert trigger.cursor == "cursor-1"


@pytest.mark.parametrize(
    ("trigger_cls", "trigger_type"),
    [
        (
            PullRequestNewFeedbackTriggerDefinition,
            "trigger:pull-request:new-feedback",
        ),
        (
            PullRequestCompletedTriggerDefinition,
            "trigger:pull-request:completed",
        ),
    ],
)
def test_pr_triggers_accept_ado_without_repo(
    trigger_cls: type[PullRequestNewFeedbackTriggerDefinition]
    | type[PullRequestCompletedTriggerDefinition],
    trigger_type: str,
) -> None:
    trigger = materialize_node_definition(
        TypeAdapter(TriggerDefinitionDiscriminated).validate_python(
            {
                "id": "t-pr-legacy",
                "display_name": "PR legacy",
                "type": trigger_type,
                "provider": "ado",
            }
        )
    )

    assert isinstance(trigger, trigger_cls)
    assert trigger.provider == "ado"
    assert trigger.repo is None
    assert trigger.organization_url is None
    assert trigger.project is None


def test_pr_feedback_trigger_accepts_agent_directed_identity_names() -> None:
    trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "t-pr-feedback",
            "display_name": "PR feedback",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "agent_author_names": [" evander Bot ", "Truman Bot"],
            "agent_mention_names": ["@evander", "Truman"],
            "agent_author_ids": [" bot-id "],
            "agent_mention_ids": [" C74221CB-EE08-6A83-9055-AF01C3333112 "],
        }
    )

    assert trigger.agent_author_names == ["evander Bot", "Truman Bot"]
    assert trigger.agent_mention_names == ["@evander", "Truman"]
    assert trigger.agent_author_ids == ["bot-id"]
    assert trigger.agent_mention_ids == ["C74221CB-EE08-6A83-9055-AF01C3333112"]


@pytest.mark.parametrize(
    ("trigger_cls", "trigger_type"),
    [
        (
            PullRequestNewFeedbackTriggerDefinition,
            "trigger:pull-request:new-feedback",
        ),
        (
            PullRequestCompletedTriggerDefinition,
            "trigger:pull-request:completed",
        ),
    ],
)
def test_pr_triggers_reject_non_string_tags(
    trigger_cls: type[PullRequestNewFeedbackTriggerDefinition]
    | type[PullRequestCompletedTriggerDefinition],
    trigger_type: str,
) -> None:
    with pytest.raises(ValueError, match="tags must be a string"):
        trigger_cls.model_validate(
            {
                "id": "t-pr-tags",
                "display_name": "PR tags",
                "type": trigger_type,
                "provider": "ado",
                "tags": ["needs-review"],
            }
        )


def test_ci_run_completed_trigger_normalizes_aliases_and_defaults() -> None:
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)

    trigger = materialize_node_definition(
        adapter.validate_python(
            {
                "id": "t-ci",
                "display_name": "CI run completed",
                "type": "trigger:ci:run:completed",
                "provider": "github-actions",
                "repo": " owner/repo ",
                "workflow": " build.yml ",
                "branches": [" main ", "", "release/v1"],
                "conclusions": [" succeeded ", "partiallySucceeded", "canceled", "timedOut"],
            }
        )
    )

    assert isinstance(trigger, CiRunCompletedTriggerDefinition)
    assert trigger.provider == "github_actions"
    assert trigger.repo == "owner/repo"
    assert trigger.workflow == "build.yml"
    assert trigger.branches == ["main", "release/v1"]
    assert trigger.conclusions == ["success", "failure", "cancelled", "timed_out"]
    assert trigger.top == 50
    assert trigger.include_existing is False


def test_ci_run_completed_trigger_requires_repo_for_github() -> None:
    with pytest.raises(ValueError, match="repo is required when provider is 'github_actions'"):
        CiRunCompletedTriggerDefinition.model_validate(
            {
                "id": "t-ci-github",
                "display_name": "CI run completed",
                "type": "trigger:ci:run:completed",
                "provider": "github",
            }
        )


def test_ci_run_completed_trigger_normalizes_ado_config() -> None:
    trigger = CiRunCompletedTriggerDefinition.model_validate(
        {
            "id": "t-ci-ado",
            "display_name": "CI run completed",
            "type": "trigger:ci:run:completed",
            "provider": "azure-pipelines",
            "organization_url": " https://dev.azure.com/example/ ",
            "project": " APP ",
            "repo": " ",
            "pipeline_id": "42",
            "pipeline_name": " nightly ",
            "branches": " release/* ",
            "conclusions": ["partiallySucceeded", "skipped", "canceled", "timedOut"],
        }
    )

    assert trigger.provider == "ado_pipelines"
    assert trigger.organization_url == "https://dev.azure.com/example"
    assert trigger.project == "APP"
    assert trigger.repo is None
    assert trigger.pipeline_id == 42
    assert trigger.pipeline_name == "nightly"
    assert trigger.branches == ["release/*"]
    assert trigger.conclusions == ["failure", "skipped", "cancelled", "timed_out"]


def test_ci_run_completed_trigger_treats_empty_conclusions_as_default() -> None:
    trigger = CiRunCompletedTriggerDefinition.model_validate(
        {
            "id": "t-ci-empty-conclusions",
            "display_name": "CI run completed",
            "type": "trigger:ci:run:completed",
            "provider": "github_actions",
            "repo": "owner/repo",
            "conclusions": [],
        }
    )

    assert trigger.conclusions == ["success", "failure", "cancelled", "timed_out", "skipped"]


def test_ci_run_completed_trigger_rejects_invalid_provider_conclusion_top_and_missing_provider() -> (
    None
):
    with pytest.raises(ValueError, match="Unsupported CI provider"):
        CiRunCompletedTriggerDefinition.model_validate(
            {
                "id": "t-ci-provider",
                "display_name": "CI run completed",
                "type": "trigger:ci:run:completed",
                "provider": "circleci",
            }
        )

    with pytest.raises(ValueError, match="Unsupported CI conclusion"):
        CiRunCompletedTriggerDefinition.model_validate(
            {
                "id": "t-ci-conclusion",
                "display_name": "CI run completed",
                "type": "trigger:ci:run:completed",
                "provider": "github_actions",
                "repo": "owner/repo",
                "conclusions": ["unknown"],
            }
        )

    with pytest.raises(ValueError, match="greater than or equal to 1"):
        CiRunCompletedTriggerDefinition.model_validate(
            {
                "id": "t-ci-top",
                "display_name": "CI run completed",
                "type": "trigger:ci:run:completed",
                "provider": "github_actions",
                "repo": "owner/repo",
                "top": 0,
            }
        )

    with pytest.raises(ValueError, match="provider"):
        CiRunCompletedTriggerDefinition.model_validate(
            {
                "id": "t-ci-missing-provider",
                "display_name": "CI run completed",
                "type": "trigger:ci:run:completed",
                "repo": "owner/repo",
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


def test_communication_trigger_output_schema_exposes_normalized_payload_fields() -> None:
    schema = effective_node_output_schema({"type": "trigger:communication:new-message"})

    assert schema["provider"] == {"type": "string"}
    assert schema["communication_target_id"] == {"type": "string"}
    assert schema["message_id"] == {"type": "string"}
    assert schema["thread_id"] == {"type": ["string", "null"]}
    assert schema["author_id"] == {"type": "string"}
    assert schema["author_name"] == {"type": "string"}
    assert schema["content"] == {"type": "string"}
    assert schema["created_at"] == {"type": "string"}
    assert schema["received_at"] == {"type": "string"}
    assert schema["target"] == {"type": "string"}
    assert schema["message"]["properties"]["provider"] == {"type": "string"}
    assert schema["message"]["properties"]["communication_target_id"] == {"type": "string"}
    assert schema["message"]["properties"]["message_id"] == {"type": "string"}
    assert schema["message"]["properties"]["target"] == {"type": "string"}
    assert schema["message"]["properties"]["metadata"] == {"type": "object"}


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


def test_http_request_effect_accepts_bindings_and_static_headers() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)
    effect = adapter.validate_python(
        {
            "id": "effect-http",
            "display_name": "HTTP request",
            "type": "effect:http:request",
            "method": "GET",
            "url": "https://example.test/api",
            "allowed_url_patterns": ["https://example.test/*"],
            "headers": {"Accept": "application/json"},
            "payload": {"ticket_id": "${{trigger.ticket_id}}"},
            "check": "${{trigger.check}}",
        }
    )

    assert effect.type == "effect:http:request"
    assert effect.method == "GET"
    assert effect.url == "https://example.test/api"
    assert effect.allowed_url_patterns == ["https://example.test/*"]
    assert effect.headers == {"Accept": "application/json"}
    assert effect.payload == {"ticket_id": "${{trigger.ticket_id}}"}
    assert effect.check == "${{trigger.check}}"


def test_http_request_effect_defaults_to_allow_any_url_pattern() -> None:
    effect = HttpRequestEffectDefinition.model_validate(
        {
            "id": "effect-http",
            "display_name": "HTTP request",
            "type": "effect:http:request",
            "url": "https://example.test/api",
        }
    )

    assert effect.allowed_url_patterns == ["*"]


def test_http_request_effect_rejects_invalid_static_method() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)

    with pytest.raises(ValueError, match="effect:http:request method must be GET, POST, or PUT"):
        adapter.validate_python(
            {
                "id": "effect-http",
                "display_name": "HTTP request",
                "type": "effect:http:request",
                "method": "delete",
                "url": "https://example.test/api",
            }
        )


def test_http_request_effect_rejects_invalid_static_url() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)

    with pytest.raises(
        ValueError, match="effect:http:request url must be a non-empty http or https URL"
    ):
        adapter.validate_python(
            {
                "id": "effect-http",
                "display_name": "HTTP request",
                "type": "effect:http:request",
                "url": "ftp://example.test/api",
            }
        )


def test_http_request_effect_rejects_invalid_allowed_url_patterns() -> None:
    adapter = TypeAdapter(EffectDefinitionDiscriminated)

    with pytest.raises(
        ValueError,
        match="effect:http:request allowed_url_patterns must be a string, string list, or binding",
    ):
        adapter.validate_python(
            {
                "id": "effect-http",
                "display_name": "HTTP request",
                "type": "effect:http:request",
                "url": "https://example.test/api",
                "allowed_url_patterns": [42],
            }
        )


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


def test_session_revoke_mcp_tool_approval_lease_effect_validates_lease_aliases() -> None:
    effect = SessionRevokeMcpToolApprovalLeaseEffectDefinition.model_validate(
        {
            "id": "effect-session-revoke-lease",
            "display_name": "Revoke tool lease",
            "type": "effect:session:revoke-mcp-tool-approval-lease",
            "mcp_tool_approval_lease_id": "lease-1",
            "session_id": "session-1",
            "workflow_run_id": "workflow-run-1",
        }
    )
    alias_effect = SessionRevokeMcpToolApprovalLeaseEffectDefinition.model_validate(
        {
            "id": "effect-session-revoke-lease",
            "display_name": "Revoke tool lease",
            "type": "effect:session:revoke-mcp-tool-approval-lease",
            "lease_id": "lease-2",
            "session_id": "session-1",
        }
    )
    both_aliases = SessionRevokeMcpToolApprovalLeaseEffectDefinition.model_validate(
        {
            "id": "effect-session-revoke-lease",
            "display_name": "Revoke tool lease",
            "type": "effect:session:revoke-mcp-tool-approval-lease",
            "mcp_tool_approval_lease_id": "lease-3",
            "lease_id": "lease-4",
            "session_id": "session-1",
        }
    )

    assert effect.mcp_tool_approval_lease_id == "lease-1"
    assert effect.lease_id is None
    assert effect.session_id == "session-1"
    assert effect.workflow_run_id == "workflow-run-1"
    assert alias_effect.lease_id == "lease-2"
    assert both_aliases.mcp_tool_approval_lease_id == "lease-3"
    assert both_aliases.lease_id == "lease-4"

    with pytest.raises(ValueError, match="mcp_tool_approval_lease_id or lease_id"):
        SessionRevokeMcpToolApprovalLeaseEffectDefinition.model_validate(
            {
                "id": "effect-session-revoke-lease",
                "display_name": "Revoke tool lease",
                "type": "effect:session:revoke-mcp-tool-approval-lease",
                "session_id": "session-1",
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
            "renew_if_expires_within": "30m",
        }
    )

    assert effect.session_id == "session-1"
    assert effect.extend_by == "8h"
    assert effect.renew_if_expires_within == "30m"

    bound_threshold = SessionRenewIfExpiredEffectDefinition.model_validate(
        {
            "id": "effect-session-renew",
            "display_name": "Renew expired session",
            "type": "effect:session:renew-if-expired",
            "session_id": "session-1",
            "renew_if_expires_within": "${{query-session-context.renewal_window}}",
        }
    )

    assert bound_threshold.renew_if_expires_within == "${{query-session-context.renewal_window}}"

    effect_with_blank_selectors = SessionRenewIfExpiredEffectDefinition.model_validate(
        {
            "id": "effect-session-renew",
            "display_name": "Renew expired session",
            "type": "effect:session:renew-if-expired",
            "target": "",
            "session_id": "session-2",
            "vm_name": "",
        }
    )

    assert effect_with_blank_selectors.session_id == "session-2"
    assert effect_with_blank_selectors.target == ""

    with pytest.raises(ValueError, match="target, session_id, or vm_name"):
        SessionRenewIfExpiredEffectDefinition.model_validate(
            {
                "id": "effect-session-renew",
                "display_name": "Renew expired session",
                "type": "effect:session:renew-if-expired",
            }
        )

    with pytest.raises(
        ValueError,
        match="requires exactly one of target, session_id, or vm_name",
    ):
        SessionRenewIfExpiredEffectDefinition.model_validate(
            {
                "id": "effect-session-renew",
                "display_name": "Renew expired session",
                "type": "effect:session:renew-if-expired",
                "target": "session-1",
                "session_id": "session-2",
            }
        )

    with pytest.raises(ValueError, match="renew_if_expires_within"):
        SessionRenewIfExpiredEffectDefinition.model_validate(
            {
                "id": "effect-session-renew",
                "display_name": "Renew expired session",
                "type": "effect:session:renew-if-expired",
                "session_id": "session-1",
                "renew_if_expires_within": "soon",
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
    artifact_import = ArtifactImportPublishedEffectDefinition.model_validate(
        {
            "id": "effect-artifact-import",
            "display_name": "Import artifact",
            "type": "effect:artifact:import-published",
            "session_id": "session-1",
            "key": "runs/session-1/report",
        }
    )

    assert stage.path == "repos/project/report.html"
    assert publish.key == "runs/session-1/report"
    assert artifact_import.overwrite is False
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
    with pytest.raises(ValueError, match="target, session_id, or vm_name"):
        ArtifactImportPublishedEffectDefinition.model_validate(
            {
                "id": "effect-artifact-import",
                "display_name": "Import artifact",
                "type": "effect:artifact:import-published",
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
