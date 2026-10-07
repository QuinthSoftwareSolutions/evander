"""Team-owned contracts for the event bodies produced by Team domain commands."""

from copy import deepcopy

_TEXT = {"type": "string"}
_NULLABLE_TEXT = {"type": ["string", "null"]}
_STRINGS = {"type": "array", "items": _TEXT}
_INTEGER = {"type": "integer", "minimum": 0}


def _schema(required, **properties):
    return {"type": "object", "properties": properties, "required": required.split()}


TEAM_EVENT_SCHEMAS = {
    "team.role_queue.wake_requested": _schema(
        "project_id team_id", project_id=_TEXT, team_id=_TEXT
    ),
    "team.task.role_assigned": _schema(
        "task_id selected_owner_agent_id dispatch_id assignment_policy assignment_role_id assignment_outcome",
        task_id=_TEXT,
        selected_owner_agent_id=_TEXT,
        dispatch_id=_TEXT,
        assignment_policy=_TEXT,
        assignment_role_id=_TEXT,
        assignment_outcome=_TEXT,
        previous_owner_agent_id=_NULLABLE_TEXT,
        eligible_candidate_count=_INTEGER,
        candidate_scores={"type": "array", "items": {"type": "object"}},
    ),
    "team.chat.message_queued": _schema(
        "team_id agent_id session_id conversation_id message_id dispatch_id",
        team_id=_TEXT,
        agent_id=_TEXT,
        session_id=_NULLABLE_TEXT,
        conversation_id=_TEXT,
        message_id=_TEXT,
        dispatch_id=_TEXT,
    ),
    "team.chat.external_reply_requested": _schema(
        "delivery_id conversation_id agent_reply_message_id agent_id sender_session_id",
        delivery_id=_TEXT,
        conversation_id=_TEXT,
        operator_message_id=_TEXT,
        operator_message_ids=_STRINGS,
        agent_reply_message_id=_TEXT,
        agent_id=_TEXT,
        sender_session_id=_TEXT,
        provider=_TEXT,
        communication_target_id=_TEXT,
    ),
    "team.agent.wake_requested": _schema(
        "team_id agent_id session_id reason",
        team_id=_TEXT,
        agent_id=_TEXT,
        session_id=_NULLABLE_TEXT,
        reason=_TEXT,
    ),
    "team.operator.escalation_requested": _schema(
        "escalation_id team_id title message urgency",
        escalation_id=_TEXT,
        team_id=_TEXT,
        manager_agent_id=_NULLABLE_TEXT,
        sender_session_id=_NULLABLE_TEXT,
        communication_target_id=_TEXT,
        title=_TEXT,
        message=_TEXT,
        urgency=_TEXT,
        project_id=_NULLABLE_TEXT,
        task_id=_NULLABLE_TEXT,
    ),
    "team.outbox.publish_requested": _schema("", outbox_record_ids=_STRINGS),
    "team.case.closed": _schema(
        "case_id team_id project_id owner_agent_id previous_status source source_ref closed_at case",
        case_id=_TEXT,
        team_id=_TEXT,
        project_id=_NULLABLE_TEXT,
        owner_agent_id=_NULLABLE_TEXT,
        previous_status=_TEXT,
        source=_NULLABLE_TEXT,
        source_ref=_NULLABLE_TEXT,
        closed_at=_TEXT,
        case={"type": "object"},
    ),
    "team.git.promotion_requested": _schema(
        "promotion_id project_id target_branch",
        promotion_id=_TEXT,
        promotion_kind=_TEXT,
        project_id=_TEXT,
        source_branch=_TEXT,
        source_branches=_STRINGS,
        target_branch=_TEXT,
        expected_commit_sha=_NULLABLE_TEXT,
        managed_repo_id=_TEXT,
        directory_path=_TEXT,
        assignment_id=_NULLABLE_TEXT,
        base_branch=_TEXT,
        base_commit_sha=_NULLABLE_TEXT,
    ),
    "team.git.release_recovery_requested": _schema(
        "release_package_id promotion_id",
        release_package_id=_TEXT,
        promotion_id=_TEXT,
        source_branch=_TEXT,
        target_branch=_TEXT,
        expected_commit_sha=_NULLABLE_TEXT,
        project_id=_TEXT,
        managed_repo_id=_TEXT,
        directory_path=_TEXT,
    ),
    "team.release.verification_requested": _schema(
        "release_package_id verification_run_id candidate_commit_sha",
        release_package_id=_TEXT,
        verification_run_id=_TEXT,
        candidate_commit_sha=_TEXT,
    ),
    "team.release.approved": _schema(
        "project_id release_package_id candidate_commit_sha",
        project_id=_TEXT,
        release_package_id=_TEXT,
        candidate_commit_sha=_TEXT,
        managed_repo_id=_NULLABLE_TEXT,
        directory_path=_NULLABLE_TEXT,
        base_branch=_NULLABLE_TEXT,
    ),
    "team.deliverable.acceptance_verification_requested": _schema(
        "", deliverable_id=_TEXT, verification_run_id=_TEXT
    ),
    "team.release.human_review_requested": _schema(
        "human_review_request_id release_package_id candidate_commit_sha candidate_tree_hash recipient_user_id assignment_generation",
        human_review_request_id=_TEXT,
        release_package_id=_TEXT,
        candidate_commit_sha=_TEXT,
        candidate_tree_hash=_TEXT,
        recipient_user_id=_TEXT,
        assignment_generation=_INTEGER,
    ),
    "team.release.human_review_preparation_requested": _schema(
        "human_review_request_id release_package_id candidate_commit_sha candidate_tree_hash preparation_generation",
        human_review_request_id=_TEXT,
        release_package_id=_TEXT,
        candidate_commit_sha=_TEXT,
        candidate_tree_hash=_TEXT,
        preparation_generation=_INTEGER,
    ),
}
for _name in ("team.ephemeral.requested", "team.ephemeral.completed"):
    TEAM_EVENT_SCHEMAS[_name] = _schema(
        "assignment assignment_id parent_agent_id project_id task_id session_id title prompt",
        assignment={"type": "object"},
        assignment_id=_TEXT,
        parent_agent_id=_TEXT,
        project_id=_TEXT,
        task_id=_TEXT,
        session_id=_TEXT,
        title=_TEXT,
        prompt=_TEXT,
        summary=_NULLABLE_TEXT,
        artifact_refs=_STRINGS,
        model=_NULLABLE_TEXT,
        agent_provider=_NULLABLE_TEXT,
        execution_backend=_TEXT,
        container_image=_NULLABLE_TEXT,
        allowed_tools=_STRINGS,
        agent_branch=_TEXT,
        ttl_hours={"type": ["integer", "null"]},
    )


def event_payload_schema(event_type):
    return deepcopy(TEAM_EVENT_SCHEMAS[event_type])
