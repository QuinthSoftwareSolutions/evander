"""Ordered SQLite schema upgrades for Team state."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from evander.models import utc_now

from .case_context_store import CONTEXT_SCHEMA_SQL
from .case_context_history import HISTORY_SCHEMA_SQL, backfill_snapshot_index
from .models import SCHEMA_VERSION, TeamState
from .sqlite_codec import (
    _ensure_column,
    _json_loads,
)
from .sqlite_migrations_acceptance import (
    _normalize_rejected_acceptance_override_rows,
    _reconcile_rejected_acceptance_overrides,
)
from .sqlite_migrations_git import (
    _has_dependency_composed_release_base_candidates,
    _migrate_release_canonical_base_snapshots,
    _migrate_release_lineage,
    _migrate_task_git_base_snapshots,
    _reconcile_release_base_outbox_records,
)
from .sqlite_migrations_membership import (
    _ensure_project_assignments_from_legacy,
    _ensure_team_agents_nullable_membership,
    _ensure_team_conversations_operator_scope,
    _migrate_human_review_relationships,
    _migrate_role_task_queue,
)
from .sqlite_migrations_outcomes import (
    _migrate_legacy_outcome_kind_definitions,
)
from .sqlite_migrations_worktrees import (
    _migrate_worktree_cleanup_context_ids,
    _migrate_worktree_cleanup_directory_scopes,
    _migrate_worktree_cleanup_evidence,
    _migrate_worktree_cleanup_retry_dispositions,
)
from .sqlite_schema_sql import (
    _LIFECYCLE_INDEX_SQL,
    _SCOPED_LIFECYCLE_INDEX_SQL,
    _SCHEMA_SQL,
    SQLITE_SCHEMA_VERSION,
)


_ACTIVITY_RETENTION_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_team_dispatch_activity_retention "
    "ON team_dispatch_activity(created_at, id)"
)


def check_team_activity_retention_index(path: Path | str) -> bool:
    """Read-only readiness check, suitable for an offline migration report."""
    from evander.repositories.sqlite_runtime import open_sqlite_connection

    if not Path(path).is_file():
        return False
    with open_sqlite_connection(path, read_only=True, operation="team_retention_readiness") as connection:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_team_dispatch_activity_retention'"
        ).fetchone() is not None


def prepare_team_activity_retention_index(path: Path | str) -> None:
    """Explicit offline preparation; the caller must stop every Team DB user."""
    from evander.repositories.sqlite_runtime import open_sqlite_connection

    with open_sqlite_connection(path, timeout_ms=300000, operation="team_retention_index_offline") as connection:
        connection.execute(_ACTIVITY_RETENTION_INDEX_SQL)


def _prepare_small_activity_retention_index(connection: sqlite3.Connection) -> None:
    # Probe at most 10,001 rows. Large existing stores require explicit offline
    # indexing, and appends skip pruning until the index is ready.
    count = connection.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM team_dispatch_activity LIMIT 10001)"
    ).fetchone()[0]
    if count <= 10000:
        connection.execute(_ACTIVITY_RETENTION_INDEX_SQL)


def migrate_schema(
    connection: sqlite3.Connection,
    *,
    read_payload: Callable[[sqlite3.Connection], dict[str, Any]],
    write_state: Callable[[sqlite3.Connection, TeamState], None],
) -> None:
    """Upgrade one store connection, retaining the historical migration order.

    The caller owns its lock and transaction. Column additions remain ordered
    around table rebuilds and data rewrites; callbacks reuse that connection.
    """
    # v83 is indexes only. An already valid v82 store must not replay historical
    # normalization/backfill passes or rewrite business records for this upgrade.
    try:
        versions = dict(connection.execute(
            "SELECT key,value FROM team_state_metadata WHERE key IN ('sqlite_schema_version','state_schema_version')"
        ))
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error):
            raise
        versions = {}
    index_only = False
    if int(versions.get("sqlite_schema_version", 0)) >= 82 and int(versions.get("state_schema_version", 0)) >= SCHEMA_VERSION:
        try:
            index_only = connection.execute("SELECT 1 FROM team_case_context_snapshot_pending LIMIT 1").fetchone() is None
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                raise
    if index_only:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        for statement in (_LIFECYCLE_INDEX_SQL + _SCOPED_LIFECYCLE_INDEX_SQL).split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            "UPDATE team_state_metadata SET value=? WHERE key='sqlite_schema_version'",
            (str(max(SQLITE_SCHEMA_VERSION, int(versions["sqlite_schema_version"]))),),
        )
        return
    connection.executescript(_SCHEMA_SQL)
    connection.executescript(CONTEXT_SCHEMA_SQL)
    connection.executescript(HISTORY_SCHEMA_SQL)
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    backfill_snapshot_index(connection)
    for context_column in ("case_note_ids_json", "context_snapshot_ids_json"):
        _ensure_column(
            connection,
            table_name="team_deliverables",
            column_name=context_column,
            definition="TEXT NOT NULL DEFAULT '[]'",
        )
    schema_version_row = connection.execute(
        """
        SELECT value
        FROM team_state_metadata
        WHERE key = 'sqlite_schema_version'
        """
    ).fetchone()
    previous_schema_version = (
        int(schema_version_row["value"]) if schema_version_row is not None else 0
    )
    state_schema_version_row = connection.execute(
        """
        SELECT value
        FROM team_state_metadata
        WHERE key = 'state_schema_version'
        """
    ).fetchone()
    previous_state_schema_version = (
        int(state_schema_version_row["value"]) if state_schema_version_row is not None else 0
    )
    _upgrade_directory_scopes(connection, previous_schema_version)
    _upgrade_runtime_and_membership_columns(connection)
    _upgrade_work_and_review_columns(connection)
    _upgrade_git_context_columns(connection, previous_schema_version)
    _reconcile_legacy_rows(connection, previous_schema_version)
    _reconcile_legacy_state(
        connection,
        previous_schema_version,
        previous_state_schema_version,
        read_payload=read_payload,
        write_state=write_state,
    )
    _prepare_small_activity_retention_index(connection)
    for statement in (_LIFECYCLE_INDEX_SQL + _SCOPED_LIFECYCLE_INDEX_SQL).split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute(
        """
        INSERT INTO team_state_metadata(key, value)
        VALUES ('sqlite_schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(SQLITE_SCHEMA_VERSION),),
    )
    if previous_state_schema_version < SCHEMA_VERSION:
        connection.execute(
            """
            INSERT INTO team_state_metadata(key, value)
            VALUES ('state_schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(SCHEMA_VERSION),),
        )


def _upgrade_directory_scopes(connection: sqlite3.Connection, previous_schema_version: int) -> None:
    _migrate_role_task_queue(connection)
    for table_name, column_name, definition in (
        ("team_projects", "directory_path", "TEXT NOT NULL DEFAULT 'root'"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    if previous_schema_version < 63:
        for project_row in connection.execute("SELECT id, git_json FROM team_projects"):
            git_payload = (
                _json_loads(project_row["git_json"])
                if project_row["git_json"] is not None
                else None
            )
            git_directory_path = (
                str(git_payload.get("directory_path") or "").strip()
                if isinstance(git_payload, dict)
                else ""
            )
            if git_directory_path:
                connection.execute(
                    "UPDATE team_projects SET directory_path = ? WHERE id = ?",
                    (git_directory_path, project_row["id"]),
                )
    if previous_schema_version < 64:
        _migrate_worktree_cleanup_directory_scopes(connection)


def _upgrade_runtime_and_membership_columns(connection: sqlite3.Connection) -> None:
    for table_name, column_name, definition in (
        ("team_governance_group_goals", "goal_name", "TEXT NOT NULL DEFAULT ''"),
        ("team_teams", "cost_warning_ratio", "TEXT NOT NULL DEFAULT '0.8'"),
        ("team_teams", "cost_budget_renewal_mode", "TEXT NOT NULL DEFAULT 'manual'"),
        ("team_teams", "cost_budget_windows_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_action_records", "feedback_delivered_at", "TEXT"),
        ("team_conversation_messages", "attachment_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_chat_attachments", "external_source_json", "TEXT"),
        ("team_conversation_messages", "source_message_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_conversation_messages", "seen_at", "TEXT"),
        ("team_conversation_messages", "seen_dispatch_id", "TEXT"),
        ("team_action_records", "feedback_delivered_by_dispatch_id", "TEXT"),
        ("team_inbox", "response_requested", "INTEGER NOT NULL DEFAULT 1"),
        ("team_dispatches", "claimed_role_task_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_dispatches", "review_routing_authorities_json", "TEXT NOT NULL DEFAULT '[]'"),
        (
            "team_dispatches",
            "initial_release_decision_deliverable_ids_json",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
        ("team_dispatches", "mcp_capability_receipt_json", "TEXT"),
        ("team_blockers", "mcp_capability_identity_json", "TEXT"),
        ("team_dispatches", "chat_message_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_dispatches", "preparation_expires_at", "TEXT"),
        ("team_dispatches", "queued_at", "TEXT"),
        ("team_dispatches", "requested_lease_seconds", "INTEGER NOT NULL DEFAULT 900"),
        ("team_dispatches", "activation_completed_at", "TEXT"),
        ("team_dispatches", "session_metadata_mirrored_at", "TEXT"),
        ("team_dispatches", "role_description_fingerprint", "TEXT"),
        ("team_dispatches", "wake_work_fingerprint", "TEXT"),
        ("team_dispatches", "wake_work_record_fingerprints_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("team_dispatches", "action_feedback_dispatch_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_deliverables", "initial_release_disposition_json", "TEXT"),
        ("team_inbox", "priority", "TEXT NOT NULL DEFAULT 'normal'"),
        ("team_teams", "escalate_to_governance", "INTEGER NOT NULL DEFAULT 0"),
        ("team_agent_runtimes", "low_priority_batch_started_at", "TEXT"),
        ("team_agent_runtimes", "low_priority_batch_cycle_count", "INTEGER NOT NULL DEFAULT 0"),
        ("team_agent_runtimes", "low_priority_batch_due_after_cycles", "INTEGER"),
        ("team_agent_runtimes", "context_clearing_policy", "TEXT NOT NULL DEFAULT 'off'"),
        (
            "team_agent_runtimes",
            "context_clearing_idle_grace_seconds",
            "INTEGER NOT NULL DEFAULT 0",
        ),
        ("team_agent_runtimes", "context_idle_since_at", "TEXT"),
        ("team_agent_runtimes", "context_idle_rotation_due_at", "TEXT"),
        (
            "team_agent_runtimes",
            "context_handoff_preparation_pending",
            "INTEGER NOT NULL DEFAULT 0",
        ),
        ("team_agent_runtimes", "context_handoff_preparation_status", "TEXT"),
        ("team_agent_runtimes", "last_agent_handoff_summary", "TEXT"),
        ("team_agent_runtimes", "last_agent_handoff_at", "TEXT"),
        ("team_agent_runtimes", "last_delivered_role_description_fingerprint", "TEXT"),
        ("team_agent_runtimes", "work_lineage_thread_ids_json", "TEXT NOT NULL DEFAULT '{}'"),
        (
            "team_agent_runtimes",
            "work_lineage_thread_dispatch_ids_json",
            "TEXT NOT NULL DEFAULT '{}'",
        ),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    for column_name in ("native_thread_id", "native_thread_observed_at"):
        _ensure_column(
            connection,
            table_name="team_dispatches",
            column_name=column_name,
            definition="TEXT",
        )
    for table_name, column_name, definition in (
        ("team_release_packages", "cancellation_json", "TEXT"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    connection.execute(
        """
        UPDATE team_agent_runtimes
        SET context_clearing_policy = CASE
            WHEN auto_context_clearing_enabled != 0
                AND (
                    context_clearing_policy IS NULL
                    OR context_clearing_policy = ''
                    OR context_clearing_policy = 'off'
                )
                THEN 'idle'
            ELSE COALESCE(NULLIF(context_clearing_policy, ''), 'off')
        END
        """
    )
    for table_name, column_name, definition in (
        ("team_agents", "avatar_json", "TEXT"),
        ("team_agents", "voice_profile_json", "TEXT"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    _ensure_team_agents_nullable_membership(connection)
    for table_name, column_name, definition in (("team_agents", "retired_team_id", "TEXT"),):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    _ensure_project_assignments_from_legacy(connection)
    _ensure_team_conversations_operator_scope(connection)


def _upgrade_work_and_review_columns(connection: sqlite3.Connection) -> None:
    for table_name, column_name, definition in (
        (
            "team_governance_runtimes",
            "last_cost_signal_fingerprints_json",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
        (
            "team_governance_runtimes",
            "last_blocked_signal_fingerprints_json",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
        ("team_governance_runtimes", "preparation_expires_at", "TEXT"),
        ("team_governance_runtimes", "queued_at", "TEXT"),
        ("team_governance_runtimes", "requested_lease_seconds", "INTEGER NOT NULL DEFAULT 900"),
        ("team_tasks", "state_entered_at", "TEXT"),
        ("team_tasks", "case_id", "TEXT"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    for column_name, definition in (
        ("task_kind", "TEXT NOT NULL DEFAULT 'support'"),
        ("outcome_contract_json", "TEXT"),
        ("integration_boundary", "INTEGER NOT NULL DEFAULT 0"),
        ("deliverable_contract_json", "TEXT"),
        ("completion_json", "TEXT"),
        ("authored_by_json", "TEXT"),
        ("work_item_attributions_json", "TEXT"),
        ("originating_task_id", "TEXT"),
        ("release_base_package_id", "TEXT"),
        ("cancellation_json", "TEXT"),
        ("git_base_branch", "TEXT"),
        ("git_base_commit_sha", "TEXT"),
        ("git_constraints_json", "TEXT"),
        ("mutation_policy_json", "TEXT"),
    ):
        _ensure_column(
            connection,
            table_name="team_tasks",
            column_name=column_name,
            definition=definition,
        )
    for table_name, column_name, definition in (
        ("team_cases", "source_evidence_snapshot_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_tasks", "source_evidence_snapshot_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_tasks", "work_lineage_id", "TEXT"),
        ("team_dispatches", "source_evidence_snapshot_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_deliverables", "branch_context_id", "TEXT"),
        ("team_deliverables", "supersedes_deliverable_id", "TEXT"),
        ("team_deliverables", "superseded_by_deliverable_id", "TEXT"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    for column_name, definition in (
        ("supersession_reason", "TEXT"),
        ("superseded_at", "TEXT"),
        ("superseded_by_actor", "TEXT"),
        ("review_cohort_id", "TEXT"),
        ("review_cohort_sealed_at", "TEXT"),
        ("rework_requested_at", "TEXT"),
        ("rework_requested_by_agent_id", "TEXT"),
        ("rework_reason", "TEXT"),
    ):
        _ensure_column(
            connection,
            table_name="team_deliverables",
            column_name=column_name,
            definition=definition,
        )
    for table_name, column_name, definition in (
        ("team_review_requests", "review_cohort_id", "TEXT"),
        ("team_deliverables", "verification_json", "TEXT"),
        ("team_deliverables", "outcomes_json", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    for column_name in (
        "submitted_commit_sha",
        "reviewed_commit_sha",
        "feature_commit_sha",
        "release_commit_sha",
    ):
        _ensure_column(
            connection,
            table_name="team_deliverables",
            column_name=column_name,
            definition="TEXT",
        )
    for table_name, column_name, definition in (
        ("team_review_request_criteria", "evidence_refs_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_deliverables", "carried_findings_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_deliverables", "review_manifest_json", "TEXT"),
        ("team_deliverables", "acceptance_recipient_agent_id", "TEXT"),
        ("team_deliverables", "acceptance_decision_json", "TEXT"),
        ("team_deliverables", "completion_json", "TEXT"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    for column_name, definition in (
        ("reason_code", "TEXT NOT NULL DEFAULT 'unspecified'"),
        ("reporter_actor", "TEXT"),
        ("resolution_scope", "TEXT NOT NULL DEFAULT 'task'"),
    ):
        _ensure_column(
            connection,
            table_name="team_blockers",
            column_name=column_name,
            definition=definition,
        )
    for table_name, column_name, definition in (
        ("team_review_requests", "reviewed_commit_sha", "TEXT"),
        ("team_review_requests", "repository_snapshot_json", "TEXT"),
        ("team_review_profiles", "required_outcome_kinds_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_review_requests", "release_package_id", "TEXT"),
        ("team_review_requests", "candidate_commit_sha", "TEXT"),
        ("team_review_requests", "criteria_snapshot_json", "TEXT"),
        (
            "team_review_requests",
            "required_outcome_kind_definitions_json",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    for column_name in (
        "requested_by_agent_id",
        "requested_by_actor",
        "supersedes_review_request_id",
        "superseded_by_review_request_id",
        "retry_reason",
        "retry_requested_by",
        "block_kind",
        "retired_at",
        "retired_by",
        "retirement_reason",
    ):
        _ensure_column(
            connection,
            table_name="team_review_requests",
            column_name=column_name,
            definition="TEXT",
        )
    for table_name, column_name, definition in (
        ("team_projects", "release_policy_json", "TEXT"),
        ("team_release_packages", "summary", "TEXT"),
        ("team_release_packages", "work_lineage_id", "TEXT"),
        ("team_dispatches", "work_lineage_id", "TEXT"),
        ("team_release_packages", "owner_agent_id", "TEXT"),
        ("team_release_packages", "policy_version", "INTEGER NOT NULL DEFAULT 1"),
        ("team_release_packages", "verification_waivers_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_release_packages", "gate_obligation_waivers_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_release_packages", "scope_validation_json", "TEXT"),
        ("team_release_packages", "git_base_branch", "TEXT"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    for column_name in (
        "base_commit_sha",
        "canonical_base_commit_sha",
        "candidate_commit_sha",
        "candidate_tree_hash",
        "approved_commit_sha",
        "review_correction_task_id",
    ):
        _ensure_column(
            connection,
            table_name="team_release_packages",
            column_name=column_name,
            definition="TEXT",
        )
    for table_name, column_name, definition in (
        ("team_release_packages", "review_cohort_id", "TEXT"),
        ("team_release_packages", "review_cohort_sealed_at", "TEXT"),
        ("team_release_packages", "verification_run_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_release_packages", "review_request_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_release_packages", "manifest_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("team_release_packages", "gate_failure_json", "TEXT"),
        ("team_release_packages", "supersedes_release_package_id", "TEXT"),
        ("team_release_packages", "lineage_root_release_package_id", "TEXT"),
        ("team_release_packages", "lineage_sequence", "INTEGER NOT NULL DEFAULT 0"),
        ("team_human_review_findings", "resolved_by_human_review_request_id", "TEXT"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    _migrate_human_review_relationships(connection)


def _upgrade_git_context_columns(
    connection: sqlite3.Connection, previous_schema_version: int
) -> None:
    for table_name, column_name, definition in (
        ("team_worktree_cleanup_candidates", "worktree_path", "TEXT"),
        ("team_worktree_cleanup_candidates", "git_work_context_id", "TEXT"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    if previous_schema_version < 65:
        _migrate_worktree_cleanup_context_ids(connection)
    connection.execute("DROP INDEX IF EXISTS idx_team_worktree_cleanup_identity")
    connection.execute("DROP INDEX IF EXISTS idx_team_worktree_cleanup_identity_v2")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_team_worktree_cleanup_identity_v3
        ON team_worktree_cleanup_candidates(
            git_work_context_id, task_id, managed_repo_id, session_id, worktree_path
        )
        """
    )
    for table_name, column_name, definition in (
        ("team_git_promotions", "sources_json", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )
    for column_name in ("git_base_branch", "base_commit_sha"):
        _ensure_column(
            connection,
            table_name="team_git_promotions",
            column_name=column_name,
            definition="TEXT",
        )
    for table_name, column_name, definition in (
        ("team_git_work_contexts", "checkout_id", "TEXT"),
        ("team_git_work_contexts", "dependency_git_sources_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("team_git_work_contexts", "dependency_composition_recovery_id", "TEXT"),
        (
            "team_git_work_contexts",
            "manual_dependency_git_sources_json",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
        ("team_git_work_contexts", "dependency_composition_manifest_json", "TEXT"),
        ("team_git_work_contexts", "preparation_generation", "TEXT"),
        ("team_deliverables", "accepted_pinned_ref", "TEXT"),
        ("team_deliverables", "dependency_composition_manifest_json", "TEXT"),
        ("team_deliverables", "acceptance_policy_version", "INTEGER NOT NULL DEFAULT 1"),
    ):
        _ensure_column(
            connection, table_name=table_name, column_name=column_name, definition=definition
        )


def _reconcile_legacy_rows(connection: sqlite3.Connection, previous_schema_version: int) -> None:
    connection.execute(
        """
        UPDATE team_tasks
        SET state_entered_at = COALESCE(NULLIF(state_entered_at, ''), updated_at, created_at)
        WHERE state_entered_at IS NULL OR state_entered_at = ''
        """
    )
    connection.execute(
        """
        UPDATE team_governance_group_goals
        SET goal_name = goal_id
        WHERE goal_name IS NULL OR goal_name = ''
        """
    )
    connection.execute(
        """
        UPDATE team_release_packages
        SET state = 'rejected',
            error = 'legacy_unpinned_release: recreate this nonterminal release under the current immutable candidate policy',
            updated_at = COALESCE(updated_at, created_at)
        WHERE policy_version < 2
          AND state NOT IN ('accepted', 'rejected', 'cancelled')
        """
    )
    if previous_schema_version < 31:
        _migrate_legacy_outcome_kind_definitions(connection)
    if previous_schema_version < 43:
        _migrate_worktree_cleanup_evidence(connection)
    if previous_schema_version < 61:
        _migrate_worktree_cleanup_retry_dispositions(connection)
    if previous_schema_version < 45:
        _migrate_task_git_base_snapshots(connection)
    if previous_schema_version < 46:
        _migrate_release_canonical_base_snapshots(connection)
    if previous_schema_version < 49:
        connection.execute(
            """
            UPDATE team_blockers
            SET source_kind = 'candidate_correction_successor',
                resolution_scope = 'downstream'
            WHERE source_kind = 'explicit'
              AND source_id LIKE 'candidate-correction-successor:%'
              AND reason_code = 'candidate_correction_successor_failed'
            """
        )
    if previous_schema_version < 66:
        _migrate_release_lineage(connection)


def _reconcile_legacy_state(
    connection: sqlite3.Connection,
    previous_schema_version: int,
    previous_state_schema_version: int,
    *,
    read_payload: Callable[[sqlite3.Connection], dict[str, Any]],
    write_state: Callable[[sqlite3.Connection, TeamState], None],
) -> None:
    if previous_schema_version < 72 and _has_dependency_composed_release_base_candidates(
        connection
    ):
        from .services.domain.release_packages import (
            reconcile_dependency_composed_release_bases,
        )

        migrated_state = TeamState.model_validate(read_payload(connection))
        release_base_result: dict[str, Any] = {"events": []}
        if reconcile_dependency_composed_release_bases(
            migrated_state,
            result=release_base_result,
        ):
            write_state(connection, migrated_state)
            _reconcile_release_base_outbox_records(
                connection,
                state=migrated_state,
                result=release_base_result,
            )
    if (
        previous_schema_version < SQLITE_SCHEMA_VERSION
        or previous_state_schema_version < SCHEMA_VERSION
    ):
        affected_deliverable_ids = _normalize_rejected_acceptance_override_rows(connection)
        if affected_deliverable_ids:
            migrated_state = TeamState.model_validate(read_payload(connection))
            _reconcile_rejected_acceptance_overrides(
                migrated_state,
                deliverable_ids=affected_deliverable_ids,
                now=utc_now(),
            )
            write_state(connection, migrated_state)
