"""Team migrations git."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from evander.models import utc_now

from .models import TeamOutboxRecord, TeamOutboxStatus, TeamState
from .sqlite_codec import (
    _datetime_text,
    _json_dumps,
    _json_loads,
)
from .sqlite_journal import (
    CommandJournalStoreMixin,
)
from .sqlite_schema_sql import (
    SQLITE_SCHEMA_VERSION,
)

_LEGACY_MIXED_GIT_BASE_ERROR = (
    "mixed_git_base_branches: legacy active release has ambiguous Git base provenance"
)


def _legacy_release_candidate_snapshot(
    release: sqlite3.Row | None,
) -> tuple[str | None, str | None]:
    if release is None:
        return None, None
    manifest = _json_loads(release["manifest_json"])
    manifest = manifest if isinstance(manifest, dict) else {}
    branch = (
        str(release["release_branch"] or "").strip()
        or str(manifest.get("release_branch") or "").strip()
        or None
    )
    commit = (
        str(release["candidate_commit_sha"] or "").strip()
        or str(manifest.get("candidate_commit_sha") or "").strip()
        or None
    )
    return branch, commit


def _legacy_correction_release_ids_by_task(
    connection: sqlite3.Connection,
) -> dict[str, set[str]]:
    release_ids_by_task: dict[str, set[str]] = {}

    def _add(task_id: object, release_id: object) -> None:
        normalized_task_id = str(task_id or "").strip()
        normalized_release_id = str(release_id or "").strip()
        if normalized_task_id and normalized_release_id:
            release_ids_by_task.setdefault(normalized_task_id, set()).add(normalized_release_id)

    request_release_ids = {
        str(row["id"]): str(row["release_package_id"])
        for row in connection.execute(
            "SELECT id, release_package_id FROM team_human_review_requests"
        )
    }
    for row in connection.execute(
        """
        SELECT finding.correction_task_id, request.release_package_id
        FROM team_human_review_findings AS finding
        JOIN team_human_review_requests AS request
          ON request.id = finding.source_human_review_request_id
        """
    ):
        _add(row["correction_task_id"], row["release_package_id"])
    for row in connection.execute(
        """
        SELECT review_correction_task_id, id AS release_package_id
        FROM team_release_packages
        WHERE review_correction_task_id IS NOT NULL
          AND trim(review_correction_task_id) != ''
        """
    ):
        _add(row["review_correction_task_id"], row["release_package_id"])
    for row in connection.execute("SELECT payload_json FROM team_release_verification_runs"):
        payload = _json_loads(row["payload_json"])
        if isinstance(payload, dict):
            _add(payload.get("correction_task_id"), payload.get("release_package_id"))
    for row in connection.execute(
        """
        SELECT task.id AS task_id, case_record.source, case_record.source_ref,
               case_record.metadata_json
        FROM team_tasks AS task
        JOIN team_cases AS case_record ON case_record.id = task.case_id
        WHERE case_record.source IN ('human_release_review', 'post_release_feedback')
        """
    ):
        if row["source"] == "human_release_review":
            _add(row["task_id"], request_release_ids.get(str(row["source_ref"] or "")))
            continue
        metadata = _json_loads(row["metadata_json"])
        if isinstance(metadata, dict):
            _add(row["task_id"], metadata.get("release_package_id"))
    return release_ids_by_task


def _legacy_task_has_authoritative_git_work(
    connection: sqlite3.Connection,
    *,
    task_id: str,
) -> bool:
    for statement in (
        "SELECT 1 FROM team_git_work_contexts WHERE task_id = ? LIMIT 1",
        "SELECT 1 FROM team_deliverables WHERE task_id = ? LIMIT 1",
        "SELECT 1 FROM team_git_promotions WHERE task_id = ? LIMIT 1",
    ):
        if connection.execute(statement, (task_id,)).fetchone() is not None:
            return True
    return False


def _block_legacy_task_git_base_conflict(
    connection: sqlite3.Connection,
    *,
    task: sqlite3.Row,
    details: str,
) -> None:
    now = utc_now().isoformat()
    connection.execute(
        """
        UPDATE team_tasks
        SET state = 'blocked',
            state_entered_at = CASE
                WHEN state = 'blocked' THEN state_entered_at
                ELSE ?
            END,
            updated_at = ?
        WHERE id = ?
        """,
        (now, now, task["id"]),
    )
    project = connection.execute(
        "SELECT team_id FROM team_projects WHERE id = ?",
        (task["project_id"],),
    ).fetchone()
    if project is None:
        return
    token = hashlib.sha256(str(task["id"]).encode("utf-8")).hexdigest()[:20]
    blocker_id = f"blocker-legacy-git-base-{token}"
    source_id = f"legacy-git-base-conflict:{task['id']}"
    connection.execute(
        """
        INSERT OR IGNORE INTO team_blockers(
            id, team_id, project_id, task_id, source_kind, source_id,
            reporter_agent_id, reporter_actor, reason_code, owner_agent_id,
            delegate_agent_id, state, route, summary, details, priority,
            escalation_depth, active_dispatch_id, follow_up_at, last_progress_at,
            terminal_reference_ids_json, created_at, updated_at, resolved_at
        )
        VALUES (?, ?, ?, ?, 'explicit', ?, NULL, 'system:sqlite-migration',
                'mixed_git_base_branches', NULL, NULL, 'open', 'operator', ?, ?,
                'high', 0, NULL, NULL, NULL, '[]', ?, ?, NULL)
        """,
        (
            blocker_id,
            project["team_id"],
            task["project_id"],
            task["id"],
            source_id,
            f"Repair ambiguous Git base for task {task['id']}",
            details,
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO team_blocker_affected_tasks(
            blocker_id, position, task_id
        ) VALUES (?, 0, ?)
        """,
        (blocker_id, task["id"]),
    )
    event_id = f"blockerevent-legacy-git-base-{token}"
    connection.execute(
        """
        INSERT OR IGNORE INTO team_blocker_events(
            id, blocker_id, event_type, actor_agent_id, from_agent_id,
            to_agent_id, dispatch_id, note, reference_ids_json,
            metadata_json, created_at
        ) VALUES (?, ?, 'reported', NULL, NULL, NULL, NULL, ?, '[]', ?, ?)
        """,
        (
            event_id,
            blocker_id,
            details,
            _json_dumps({"sqlite_migration": SQLITE_SCHEMA_VERSION}),
            now,
        ),
    )


def _legacy_deliverable_git_snapshots(
    connection: sqlite3.Connection,
    deliverables: Sequence[sqlite3.Row],
) -> tuple[set[tuple[str, str]], bool]:
    snapshots: set[tuple[str, str]] = set()
    unresolved = False
    for deliverable in deliverables:
        contexts: list[sqlite3.Row] = []
        if deliverable["branch_context_id"]:
            contexts = connection.execute(
                """
                SELECT base_branch, base_commit
                FROM team_git_work_contexts
                WHERE id = ? AND task_id = ?
                """,
                (deliverable["branch_context_id"], deliverable["task_id"]),
            ).fetchall()
        if not contexts:
            contexts = connection.execute(
                """
                SELECT DISTINCT base_branch, base_commit
                FROM team_git_work_contexts
                WHERE task_id = ?
                """,
                (deliverable["task_id"],),
            ).fetchall()
        if not contexts:
            unresolved = True
            continue
        complete_for_deliverable = False
        for context in contexts:
            branch = str(context["base_branch"] or "").strip()
            commit = str(context["base_commit"] or "").strip()
            if not branch or not commit:
                unresolved = True
                continue
            complete_for_deliverable = True
            snapshots.add((branch, commit))
        if not complete_for_deliverable:
            unresolved = True
    return snapshots, unresolved


def _block_legacy_release_git_base_conflict(
    connection: sqlite3.Connection,
    *,
    release_id: str,
) -> None:
    connection.execute(
        """
        UPDATE team_release_packages
        SET state = 'blocked',
            error = ?,
            updated_at = COALESCE(updated_at, created_at)
        WHERE id = ?
        """,
        (_LEGACY_MIXED_GIT_BASE_ERROR, release_id),
    )


def _migrate_task_git_base_snapshots(connection: sqlite3.Connection) -> None:
    """Backfill active Git workstreams without inventing ambiguous provenance."""

    project_defaults: dict[str, str | None] = {}
    for row in connection.execute("SELECT id, git_json FROM team_projects ORDER BY id"):
        git_payload = _json_loads(row["git_json"]) if row["git_json"] else None
        project_defaults[row["id"]] = (
            str(git_payload.get("base_branch") or "").strip() or None
            if isinstance(git_payload, dict)
            else None
        )

    correction_release_ids = _legacy_correction_release_ids_by_task(connection)
    active_task_states = (
        "pending",
        "in_progress",
        "waiting_for_input",
        "waiting_for_review",
        "blocked",
    )
    task_rows = connection.execute(
        """
        SELECT id, project_id, owner_agent_id, state, title, git_base_branch,
               state_entered_at, created_at, updated_at
        FROM team_tasks
        WHERE state IN (?, ?, ?, ?, ?)
        ORDER BY id
        """,
        active_task_states,
    ).fetchall()
    for task in task_rows:
        current_branch = str(task["git_base_branch"] or "").strip() or None
        linked_release_ids = correction_release_ids.get(str(task["id"]), set())
        context_branches = {
            str(row["base_branch"]).strip()
            for row in connection.execute(
                """
                SELECT base_branch
                FROM team_git_work_contexts
                WHERE task_id = ? AND base_branch IS NOT NULL AND trim(base_branch) != ''
                """,
                (task["id"],),
            )
        }

        if linked_release_ids:
            if len(linked_release_ids) != 1:
                _block_legacy_task_git_base_conflict(
                    connection,
                    task=task,
                    details=(
                        f"Legacy correction task {task['id']} is linked to multiple "
                        "release candidates and requires an explicit Git-base repair."
                    ),
                )
                continue
            release_id = next(iter(linked_release_ids))
            linked_release = connection.execute(
                """
                SELECT id, project_id, release_branch, git_base_branch,
                       candidate_commit_sha, manifest_json
                FROM team_release_packages
                WHERE id = ?
                """,
                (release_id,),
            ).fetchone()
            candidate_branch, _candidate_commit = _legacy_release_candidate_snapshot(linked_release)
            if (
                linked_release is None
                or linked_release["project_id"] != task["project_id"]
                or candidate_branch is None
                or len(context_branches) > 1
                or (context_branches and candidate_branch not in context_branches)
                or (
                    current_branch not in {None, candidate_branch}
                    and _legacy_task_has_authoritative_git_work(
                        connection,
                        task_id=str(task["id"]),
                    )
                )
            ):
                _block_legacy_task_git_base_conflict(
                    connection,
                    task=task,
                    details=(
                        f"Legacy correction task {task['id']} does not have one "
                        "unambiguous rejected-candidate Git base and requires "
                        "explicit repair."
                    ),
                )
                continue
            if current_branch != candidate_branch:
                connection.execute(
                    "UPDATE team_tasks SET git_base_branch = ? WHERE id = ?",
                    (candidate_branch, task["id"]),
                )
            continue

        if len(context_branches) > 1 or (
            current_branch is not None
            and context_branches
            and current_branch not in context_branches
        ):
            _block_legacy_task_git_base_conflict(
                connection,
                task=task,
                details=(
                    f"Legacy task {task['id']} has conflicting persisted Git-base "
                    "provenance and requires an explicit repair."
                ),
            )
            continue
        if current_branch is not None:
            continue
        branch = (
            next(iter(context_branches))
            if context_branches
            else project_defaults.get(task["project_id"])
        )
        if branch is not None:
            connection.execute(
                "UPDATE team_tasks SET git_base_branch = ? WHERE id = ?",
                (branch, task["id"]),
            )

    active_release_states = ("pending", "in_progress", "blocked")
    release_rows = connection.execute(
        """
        SELECT id, project_id, supersedes_release_package_id, base_commit_sha,
               error
        FROM team_release_packages
        WHERE state IN (?, ?, ?)
          AND git_base_branch IS NULL
        ORDER BY id
        """,
        active_release_states,
    ).fetchall()
    for release in release_rows:
        deliverables = connection.execute(
            """
            SELECT d.*
            FROM team_release_deliverables AS link
            JOIN team_deliverables AS d ON d.id = link.deliverable_id
            WHERE link.package_id = ?
            ORDER BY link.position
            """,
            (release["id"],),
        ).fetchall()
        git_deliverables = [
            row
            for row in deliverables
            if any(
                row[column]
                for column in (
                    "branch_context_id",
                    "agent_branch",
                    "review_branch",
                    "feature_branch",
                    "release_branch",
                    "commit_sha",
                )
            )
        ]
        predecessor_id = str(release["supersedes_release_package_id"] or "").strip()
        has_git_release = (
            bool(git_deliverables)
            or bool(predecessor_id)
            or connection.execute(
                """
                SELECT 1
                FROM team_release_feature_branches
                WHERE package_id = ?
                LIMIT 1
                """,
                (release["id"],),
            ).fetchone()
            is not None
        )
        if not has_git_release:
            continue

        if predecessor_id:
            predecessor = connection.execute(
                """
                SELECT id, project_id, release_branch, git_base_branch,
                       candidate_commit_sha, manifest_json
                FROM team_release_packages
                WHERE id = ?
                """,
                (predecessor_id,),
            ).fetchone()
            branch, commit = _legacy_release_candidate_snapshot(predecessor)
            inherited_deliverable_ids = {
                str(row["deliverable_id"])
                for row in connection.execute(
                    """
                    SELECT deliverable_id
                    FROM team_release_deliverables
                    WHERE package_id = ?
                    """,
                    (predecessor_id,),
                )
            }
            correction_deliverables = [
                row for row in git_deliverables if str(row["id"]) not in inherited_deliverable_ids
            ]
            inferred, unresolved = _legacy_deliverable_git_snapshots(
                connection,
                correction_deliverables,
            )
            if inferred:
                if len(inferred) != 1 or unresolved:
                    _block_legacy_release_git_base_conflict(
                        connection,
                        release_id=str(release["id"]),
                    )
                    continue
                inferred_branch, inferred_commit = next(iter(inferred))
                if (branch is not None and branch != inferred_branch) or (
                    commit is not None and commit != inferred_commit
                ):
                    _block_legacy_release_git_base_conflict(
                        connection,
                        release_id=str(release["id"]),
                    )
                    continue
                branch = branch or inferred_branch
                commit = commit or inferred_commit
            elif unresolved:
                _block_legacy_release_git_base_conflict(
                    connection,
                    release_id=str(release["id"]),
                )
                continue
            if (
                predecessor is None
                or predecessor["project_id"] != release["project_id"]
                or branch is None
                or commit is None
            ):
                _block_legacy_release_git_base_conflict(
                    connection,
                    release_id=str(release["id"]),
                )
                continue
            connection.execute(
                """
                UPDATE team_release_packages
                SET git_base_branch = ?,
                    base_commit_sha = ?,
                    state = CASE WHEN error = ? THEN 'pending' ELSE state END,
                    error = CASE WHEN error = ? THEN NULL ELSE error END
                WHERE id = ?
                """,
                (
                    branch,
                    commit,
                    _LEGACY_MIXED_GIT_BASE_ERROR,
                    _LEGACY_MIXED_GIT_BASE_ERROR,
                    release["id"],
                ),
            )
            continue

        snapshots, unresolved = _legacy_deliverable_git_snapshots(
            connection,
            git_deliverables,
        )
        if len(snapshots) == 1 and not unresolved:
            branch, commit = next(iter(snapshots))
            existing_commit = str(release["base_commit_sha"] or "").strip() or None
            if existing_commit is not None and existing_commit != commit:
                _block_legacy_release_git_base_conflict(
                    connection,
                    release_id=str(release["id"]),
                )
                continue
            connection.execute(
                """
                UPDATE team_release_packages
                SET git_base_branch = ?,
                    base_commit_sha = COALESCE(base_commit_sha, ?)
                WHERE id = ?
                """,
                (branch, commit, release["id"]),
            )
            continue
        _block_legacy_release_git_base_conflict(
            connection,
            release_id=str(release["id"]),
        )

    promotion_rows = connection.execute(
        """
        SELECT id, task_id, release_package_id, outputs_json
        FROM team_git_promotions
        WHERE state IN ('pending', 'failed')
          AND git_base_branch IS NULL
        ORDER BY id
        """
    ).fetchall()
    for promotion in promotion_rows:
        branch = None
        commit = None
        if promotion["release_package_id"]:
            release = connection.execute(
                """
                SELECT git_base_branch, base_commit_sha
                FROM team_release_packages
                WHERE id = ?
                """,
                (promotion["release_package_id"],),
            ).fetchone()
            if release is not None:
                branch = release["git_base_branch"]
                commit = release["base_commit_sha"]
        if branch is None and promotion["task_id"]:
            task = connection.execute(
                "SELECT git_base_branch FROM team_tasks WHERE id = ?",
                (promotion["task_id"],),
            ).fetchone()
            branch = task["git_base_branch"] if task is not None else None
            context_commits = {
                str(row["base_commit"]).strip()
                for row in connection.execute(
                    """
                    SELECT base_commit FROM team_git_work_contexts
                    WHERE task_id = ? AND base_commit IS NOT NULL AND base_commit != ''
                    """,
                    (promotion["task_id"],),
                )
            }
            if len(context_commits) == 1:
                commit = next(iter(context_commits))
        outputs = _json_loads(promotion["outputs_json"])
        if commit is None and isinstance(outputs, dict):
            commit = str(outputs.get("base_commit_sha") or "").strip() or None
        if branch is not None:
            connection.execute(
                """
                UPDATE team_git_promotions
                SET git_base_branch = ?, base_commit_sha = ?
                WHERE id = ?
                """,
                (branch, commit, promotion["id"]),
            )


def _migrate_release_canonical_base_snapshots(
    connection: sqlite3.Connection,
) -> None:
    """Backfill the immutable pre-publication SHA for each release lineage."""

    connection.execute(
        """
        UPDATE team_release_packages
        SET canonical_base_commit_sha = base_commit_sha
        WHERE canonical_base_commit_sha IS NULL
          AND supersedes_release_package_id IS NULL
          AND base_commit_sha IS NOT NULL
        """
    )
    while True:
        changed = 0
        rows = connection.execute(
            """
            SELECT child.id,
                   child.canonical_branch,
                   child.base_commit_sha,
                   predecessor.canonical_branch AS predecessor_canonical_branch,
                   predecessor.canonical_base_commit_sha AS predecessor_canonical_base,
                   predecessor.base_commit_sha AS predecessor_base,
                   predecessor.published_commit_sha AS predecessor_published,
                   predecessor.approved_commit_sha AS predecessor_approved,
                   predecessor.candidate_commit_sha AS predecessor_candidate,
                   predecessor.state AS predecessor_state
            FROM team_release_packages AS child
            JOIN team_release_packages AS predecessor
              ON predecessor.id = child.supersedes_release_package_id
            WHERE child.canonical_base_commit_sha IS NULL
            ORDER BY child.created_at, child.id
            """
        ).fetchall()
        for row in rows:
            if row["canonical_branch"] == row["predecessor_canonical_branch"]:
                if row["predecessor_state"] == "accepted":
                    canonical_base = (
                        row["predecessor_published"]
                        or row["predecessor_approved"]
                        or row["predecessor_candidate"]
                    )
                else:
                    canonical_base = row["predecessor_canonical_base"] or row["predecessor_base"]
            else:
                canonical_base = row["base_commit_sha"]
            if not canonical_base:
                continue
            connection.execute(
                """
                UPDATE team_release_packages
                SET canonical_base_commit_sha = ?
                WHERE id = ? AND canonical_base_commit_sha IS NULL
                """,
                (canonical_base, row["id"]),
            )
            changed += 1
        if changed == 0:
            break
    connection.execute(
        """
        UPDATE team_git_promotions
        SET base_commit_sha = (
            SELECT release.canonical_base_commit_sha
            FROM team_release_packages AS release
            WHERE release.id = team_git_promotions.release_package_id
        )
        WHERE promotion_kind = 'canonical_push'
          AND state IN ('pending', 'failed')
          AND EXISTS (
              SELECT 1
              FROM team_release_packages AS release
              WHERE release.id = team_git_promotions.release_package_id
                AND release.canonical_base_commit_sha IS NOT NULL
          )
        """
    )


def _reconcile_release_base_outbox_records(
    connection: sqlite3.Connection,
    *,
    state: TeamState,
    result: Mapping[str, Any],
) -> None:
    """Retire stale queued intents and durably enqueue reconciled promotions."""

    now = utc_now()
    now_text = _datetime_text(now)
    retired_ids = {
        str(promotion_id) for promotion_id in result.get("retired_git_promotion_ids", [])
    }
    if retired_ids:
        placeholders = ", ".join("?" for _promotion_id in retired_ids)
        connection.execute(
            f"""
            UPDATE team_outbox
            SET status = ?,
                completed_at = COALESCE(completed_at, ?),
                updated_at = ?
            WHERE aggregate_type = 'git_promotion'
              AND aggregate_id IN ({placeholders})
              AND (
                    status = ?
                    OR (
                        status = ?
                        AND claim_expires_at IS NOT NULL
                        AND claim_expires_at <= ?
                    )
              )
            """,
            (
                TeamOutboxStatus.COMPLETED.value,
                now_text,
                now_text,
                *sorted(retired_ids),
                TeamOutboxStatus.PENDING.value,
                TeamOutboxStatus.CLAIMED.value,
                now_text,
            ),
        )

    for promotion_id in result.get("updated_git_promotion_ids", []):
        promotion = state.git_promotions.get(str(promotion_id))
        if promotion is None:
            continue
        rows = connection.execute(
            """
            SELECT id, payload_json
            FROM team_outbox
            WHERE aggregate_type = 'git_promotion'
              AND aggregate_id = ?
              AND status IN (?, ?)
            """,
            (
                promotion.id,
                TeamOutboxStatus.PENDING.value,
                TeamOutboxStatus.CLAIMED.value,
            ),
        ).fetchall()
        for row in rows:
            outbox_payload = _json_loads(row["payload_json"])
            if not isinstance(outbox_payload, dict):
                continue
            event_payload = outbox_payload.get("payload")
            if not isinstance(event_payload, dict):
                continue
            event_payload["base_branch"] = promotion.git_base_branch
            event_payload["base_commit_sha"] = promotion.base_commit_sha
            connection.execute(
                "UPDATE team_outbox SET payload_json = ?, updated_at = ? WHERE id = ?",
                (_json_dumps(outbox_payload), now_text, row["id"]),
            )

    for event in result.get("events", []):
        if not isinstance(event, Mapping):
            continue
        event_type = str(event.get("event_type") or "").strip()
        event_payload = event.get("payload")
        if not event_type or not isinstance(event_payload, Mapping):
            continue
        payload = dict(event_payload)
        aggregate_id = str(payload.get("promotion_id") or "").strip() or None
        CommandJournalStoreMixin._insert_outbox_record(
            connection,
            TeamOutboxRecord(
                id=f"outbox-{uuid4().hex}",
                intent_type=event_type,
                aggregate_type="git_promotion",
                aggregate_id=aggregate_id,
                idempotency_key=(str(event.get("idempotency_key") or "").strip() or None),
                payload={
                    "event_type": event_type,
                    "correlation_key": event.get("correlation_key"),
                    "payload": payload,
                },
                created_at=now,
                updated_at=now,
            ),
        )


def _has_dependency_composed_release_base_candidates(
    connection: sqlite3.Connection,
) -> bool:
    return (
        connection.execute(
            """
            SELECT 1
            FROM team_release_packages AS release
            JOIN team_release_deliverables AS link
              ON link.package_id = release.id
            JOIN team_deliverables AS deliverable
              ON deliverable.id = link.deliverable_id
            WHERE release.state NOT IN ('accepted', 'rejected', 'cancelled')
              AND release.base_commit_sha IS NOT NULL
              AND deliverable.dependency_composition_manifest_json IS NOT NULL
            LIMIT 1
            """
        ).fetchone()
        is not None
    )


def _migrate_release_lineage(connection: sqlite3.Connection) -> None:
    """Backfill only explicit successor ancestry and unique exact task bases."""

    connection.execute(
        """
        UPDATE team_release_packages
        SET lineage_root_release_package_id = id,
            lineage_sequence = 0
        WHERE supersedes_release_package_id IS NULL
          AND lineage_root_release_package_id IS NULL
        """
    )
    while True:
        rows = connection.execute(
            """
            SELECT child.id,
                   predecessor.lineage_root_release_package_id AS lineage_root,
                   predecessor.lineage_sequence AS predecessor_sequence
            FROM team_release_packages AS child
            JOIN team_release_packages AS predecessor
              ON predecessor.id = child.supersedes_release_package_id
            WHERE child.lineage_root_release_package_id IS NULL
              AND predecessor.lineage_root_release_package_id IS NOT NULL
            ORDER BY child.created_at, child.id
            """
        ).fetchall()
        if not rows:
            break
        for row in rows:
            connection.execute(
                """
                UPDATE team_release_packages
                SET lineage_root_release_package_id = ?,
                    lineage_sequence = ?
                WHERE id = ? AND lineage_root_release_package_id IS NULL
                """,
                (row["lineage_root"], int(row["predecessor_sequence"]) + 1, row["id"]),
            )

    releases_by_exact_base: dict[tuple[str, str, str], list[str]] = {}
    for row in connection.execute(
        """
        SELECT id, project_id, release_branch, manifest_json, candidate_commit_sha
        FROM team_release_packages
        WHERE candidate_commit_sha IS NOT NULL
        ORDER BY created_at, id
        """
    ):
        manifest = _json_loads(row["manifest_json"] or "{}")
        manifest_branch = manifest.get("release_branch") if isinstance(manifest, dict) else None
        branch = row["release_branch"] or manifest_branch
        if not branch:
            continue
        key = (row["project_id"], branch, str(row["candidate_commit_sha"]).casefold())
        releases_by_exact_base.setdefault(key, []).append(row["id"])

    for task in connection.execute(
        """
        SELECT id, project_id, git_base_branch, git_base_commit_sha
        FROM team_tasks
        WHERE release_base_package_id IS NULL
          AND git_base_branch IS NOT NULL
          AND git_base_commit_sha IS NOT NULL
        ORDER BY id
        """
    ):
        key = (
            task["project_id"],
            task["git_base_branch"],
            str(task["git_base_commit_sha"]).casefold(),
        )
        release_ids = releases_by_exact_base.get(key, [])
        if len(release_ids) == 1:
            connection.execute(
                "UPDATE team_tasks SET release_base_package_id = ? WHERE id = ?",
                (release_ids[0], task["id"]),
            )
