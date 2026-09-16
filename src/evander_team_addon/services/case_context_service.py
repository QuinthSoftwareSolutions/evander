"""Case-scoped history: one assembler for tools, workflows, wakes and operators.

External providers are deliberately absent here. Only already captured evidence
is composed. A view is a projection, never an authorization mechanism.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Callable

from evander.models import DirectoryPath

from ..case_context_models import (
    CaseContextSection,
    CaseContextSnapshot,
    CaseContextView,
    SECTION_NAMES,
    TeamNote,
)
from ..models import TeamState, external_source_evidence_snapshot_payload
from ..repository import TeamStateRepository


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def directory(value: str | None) -> str:
    return DirectoryPath(segments=value or "root").as_posix()


def within(path: str, scope: str) -> bool:
    return path == scope or path.startswith(scope + "/")


class CaseContextAccess:
    def __init__(self, state: TeamState, *, directory_path="root", actor_id=None):
        self.state = state
        self.directory_path = directory(directory_path)
        self.actor_id = actor_id
        self.reader = None
        if actor_id is not None:
            from .read_service import TeamReadService

            actor = state.agents.get(actor_id)
            if actor is None or actor.status.value != "active":
                raise PermissionError("case context requires an active Team agent")
            self.reader = TeamReadService(state=state, actor=actor)

    def project_visible(self, project_id):
        project = self.state.projects.get(project_id)
        return bool(project and within(project.directory_path, self.directory_path))

    def case_visible(self, case_id):
        case = self.state.cases.get(case_id)
        if case is None:
            return False
        scoped = (
            self.project_visible(case.project_id)
            if case.project_id
            else (self.directory_path == "root")
        )
        return scoped and (self.reader is None or self.reader.get_case(case_id) is not None)

    def task_visible(self, task_id):
        task = self.state.tasks.get(task_id)
        return bool(
            task
            and self.project_visible(task.project_id)
            and (self.reader is None or self.reader._task_visible(task))
        )

    def release_visible(self, release_id):
        release = self.state.release_packages.get(release_id)
        return bool(
            release
            and self.project_visible(release.project_id)
            and (self.reader is None or self.reader.get_release_package(release_id) is not None)
        )

    def source_visible(self, source_id):
        source = self.state.external_source_evidence_snapshots.get(source_id)
        if source is None:
            return False
        scoped = (
            self.project_visible(source.project_id)
            if source.project_id
            else (self.directory_path == "root")
        )
        return scoped and (
            self.reader is None or self.reader.get_source_evidence_snapshot(source_id) is not None
        )

    def note_visible(self, note: TeamNote):
        if note.case_id:
            return self.case_visible(note.case_id) and all(
                self.task_visible(task_id) for task_id in note.task_ids
            )
        if note.task_ids:
            return all(self.task_visible(task_id) for task_id in note.task_ids)
        # Unattributed legacy/dispatch notes must not become project-wide history.
        return (
            self.reader is None and self.directory_path == "root"
        ) or note.created_by_agent_id == self.actor_id


def make_note(
    state,
    *,
    note_id,
    agent_id,
    dispatch_id,
    body,
    kind="note",
    summary="",
    rationale="",
    case_id=None,
    task_ids=(),
    deliverable_ids=(),
    review_request_ids=(),
    source_evidence_snapshot_ids=(),
    source_refs=(),
    supersedes_note_id=None,
    created_at=None,
    legacy=False,
) -> TeamNote:
    agent = state.agents.get(agent_id)
    dispatch = state.dispatches.get(dispatch_id)
    if not legacy and (agent is None or dispatch is None or dispatch.agent_id != agent_id):
        raise ValueError("note requires the caller's dispatch")
    access = CaseContextAccess(state, actor_id=agent_id) if not legacy else None
    tasks = set(task_ids)
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            raise ValueError("unknown deliverable reference")
        tasks.add(deliverable.task_id)
    for review_id in review_request_ids:
        review = state.review_requests.get(review_id)
        if review is None or (access and access.reader.get_review_request(review_id) is None):
            raise ValueError("review reference is unavailable")
        if review.deliverable_id in state.deliverables:
            tasks.add(state.deliverables[review.deliverable_id].task_id)
    if not tasks and not case_id and dispatch:
        candidates = [state.tasks[t] for t in dispatch.task_ids if t in state.tasks]
        cases = {t.case_id for t in candidates}
        if len(candidates) == 1:
            tasks = {candidates[0].id}
        elif len(cases) == 1 and None not in cases:
            case_id = next(iter(cases))
    if any(t not in state.tasks or (access and not access.task_visible(t)) for t in tasks):
        raise ValueError("task reference is unavailable")
    cases = {state.tasks[t].case_id for t in tasks if state.tasks[t].case_id}
    if len(cases) > 1 or (case_id and cases and cases != {case_id}):
        raise ValueError("a note must belong to one case; task references conflict")
    case_id = case_id or (next(iter(cases)) if cases else None)
    if case_id and (case_id not in state.cases or (access and not access.case_visible(case_id))):
        raise ValueError("case reference is unavailable")
    if access and any(not access.source_visible(s) for s in source_evidence_snapshot_ids):
        raise ValueError("source evidence is outside the current dispatch scope")
    if case_id and any(
        case_id not in state.external_source_evidence_snapshots[s].case_ids
        and not set(state.external_source_evidence_snapshots[s].task_ids) & tasks
        for s in source_evidence_snapshot_ids
    ):
        raise ValueError("source evidence must be linked to the note case or tasks")
    project_ids = {state.tasks[t].project_id for t in tasks}
    if case_id and state.cases[case_id].project_id:
        project_ids.add(state.cases[case_id].project_id)
    if len(project_ids) > 1:
        raise ValueError("note references must belong to one project")
    if supersedes_note_id:
        previous = (
            legacy_notes(state).get(supersedes_note_id)
            if not legacy
            else state.notes.get(supersedes_note_id)
        )
        owner = state.cases[case_id].owner_agent_id if case_id else None
        if (
            previous is None
            or previous.case_id != case_id
            or agent_id not in {previous.created_by_agent_id, owner}
        ):
            raise ValueError("only the author or case owner may supersede this note")
    values = dict(
        id=note_id,
        team_id=state.cases[case_id].team_id if case_id else (agent.team_id if agent else None),
        project_id=next(iter(project_ids)) if project_ids else None,
        case_id=case_id,
        task_ids=sorted(tasks),
        deliverable_ids=sorted(set(deliverable_ids)),
        review_request_ids=sorted(set(review_request_ids)),
        source_evidence_snapshot_ids=sorted(set(source_evidence_snapshot_ids)),
        source_refs=list(dict.fromkeys(source_refs)),
        kind=kind,
        summary=summary,
        body=body,
        rationale=rationale,
        supersedes_note_id=supersedes_note_id,
        created_by_agent_id=agent_id,
        dispatch_id=dispatch_id,
        legacy=legacy,
    )
    if created_at is not None:
        values["created_at"] = created_at
    return TeamNote(**values)


def legacy_notes(state: TeamState) -> dict[str, TeamNote]:
    """Pure legacy projection; migration persists exactly these original IDs."""
    result = dict(state.notes)
    for action in state.action_records.values():
        if (
            action.id in result
            or not action.success
            or action.action.get("tool_name") != "team_record_note"
            or not str(action.action.get("body") or "").strip()
        ):
            continue
        values = dict(
            note_id=action.id,
            agent_id=action.agent_id,
            dispatch_id=action.dispatch_id,
            body=action.action["body"],
            created_at=action.created_at,
            legacy=True,
        )
        try:
            result[action.id] = make_note(state, **values)
        except ValueError:
            # Broken/ambiguous old links are preserved without invented attribution.
            result[action.id] = TeamNote(
                id=action.id,
                created_by_agent_id=action.agent_id,
                dispatch_id=action.dispatch_id,
                body=action.action["body"],
                created_at=action.created_at,
                legacy=True,
            )
    return result


def builtin_views() -> dict[str, CaseContextView]:
    result = {}
    for name, title, sections, budget in (
        ("case-overview", "Case overview", SECTION_NAMES, 16000),
        ("task-brief", "Task brief", ("request", "sources", "decisions", "plans", "review"), 6000),
        (
            "review-brief",
            "Review brief",
            ("request", "decisions", "delivery", "review", "verification", "sources", "plans"),
            6000,
        ),
        ("app-release-brief", "APP release brief", SECTION_NAMES, 24000),
    ):
        result[name] = CaseContextView(
            id=f"builtin:{name}:1",
            view_id=name,
            title=title,
            text_budget=budget,
            sections=[CaseContextSection(name=s) for s in sections],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    return result


def effective_view(state, *, view_id="case-overview", directory_path="root", version=None):
    path = directory(directory_path)
    candidates = [
        v
        for v in state.case_context_views.values()
        if v.view_id == view_id and within(path, v.directory_path)
    ]
    if candidates:
        nearest = max(len(v.directory_path.split("/")) for v in candidates)
        candidates = [v for v in candidates if len(v.directory_path.split("/")) == nearest]
        if version is not None:
            candidates = [v for v in candidates if v.version == version]
        if candidates:
            return max(candidates, key=lambda v: v.version)
    else:
        default = builtin_views().get(view_id)
        if default and version in {None, default.version}:
            return default
    raise ValueError("unknown context view or version")


def resolve_scope(state, *, case_ids=(), task_id=None, release_package_id=None):
    cases, tasks, deliverables, releases = set(case_ids), set(), set(), set()
    requested_cases = set(case_ids)
    if task_id:
        if task_id not in state.tasks:
            raise ValueError("unknown context task")
        tasks.add(task_id)
        if state.tasks[task_id].case_id:
            requested_cases.add(state.tasks[task_id].case_id)
    if not release_package_id and not task_id:
        tasks.update(t.id for t in state.tasks.values() if t.case_id in cases)
    pending = [release_package_id] if release_package_id else []
    if not release_package_id:
        seed_deliverables = {d for t in tasks for d in state.tasks[t].deliverable_ids}
        pending.extend(
            r.id
            for r in state.release_packages.values()
            if set(r.deliverable_ids) & seed_deliverables
        )
        pending.extend(
            state.tasks[t].release_base_package_id
            for t in tasks
            if state.tasks[t].release_base_package_id
        )
    while pending:
        rid = pending.pop()
        if rid in releases:
            continue
        release = state.release_packages.get(rid)
        if release is None:
            raise ValueError("unknown context release")
        releases.add(rid)
        deliverables.update(release.deliverable_ids)
        if release.manifest:
            manifest = release.manifest
            deliverables.update(d.deliverable_id for d in manifest.deliverables)
            deliverables.update(manifest.correction_deliverable_ids)
            for key, values in manifest.contribution_graph.items():
                deliverables.add(key)
                deliverables.update(values)
            predecessor = manifest.predecessor_release_package_id
            if predecessor:
                pending.append(predecessor)
        elif release.supersedes_release_package_id:
            pending.append(release.supersedes_release_package_id)
        if release.review_correction_task_id:
            tasks.add(release.review_correction_task_id)
        tasks.update(
            run.correction_task_id
            for run in state.release_verification_runs.values()
            if run.release_package_id == rid and run.correction_task_id
        )
    if not release_package_id:
        # Task-scoped work can already include contributions before a release exists.
        # Explicit release scope must remain pinned to its selected deliverables.
        deliverables.update(
            did for tid in tasks if tid in state.tasks for did in state.tasks[tid].deliverable_ids
        )
    traversed_deliverables = set()

    def include_contributions(seed_ids):
        seed_ids = set(seed_ids)
        deliverables.update(seed_ids)
        pending_deliverables = list(seed_ids - traversed_deliverables)
        contributing_tasks = set()
        while pending_deliverables:
            did = pending_deliverables.pop()
            if did in traversed_deliverables:
                continue
            traversed_deliverables.add(did)
            item = state.deliverables.get(did)
            if item:
                contributing_tasks.add(item.task_id)
                deliverables.update(item.source_deliverable_ids)
                pending_deliverables.extend(
                    sid for sid in item.source_deliverable_ids if sid not in traversed_deliverables
                )
        return contributing_tasks

    tasks.update(include_contributions(deliverables))
    if not release_package_id and requested_cases:
        tasks = {
            t
            for t in tasks
            if t in state.tasks
            and (not state.tasks[t].case_id or state.tasks[t].case_id in requested_cases)
        }
    # Ancestors/dependencies carry plans and source requirements, never arbitrary siblings.
    queue = list(tasks)
    while queue:
        tid = queue.pop()
        task = state.tasks.get(tid)
        if task is None:
            continue
        if task.case_id:
            cases.add(task.case_id)
        for ancestor in [task.parent_task_id, task.originating_task_id, *task.dependency_task_ids]:
            if ancestor and ancestor not in tasks:
                tasks.add(ancestor)
                queue.append(ancestor)
        if not release_package_id:
            # Ancestors/dependencies can introduce more contributions; visit their
            # tasks too, retaining the case boundary used for the initial traversal.
            for related_id in include_contributions(task.deliverable_ids):
                related = state.tasks.get(related_id)
                if (
                    related_id not in tasks
                    and related
                    and (
                        not requested_cases
                        or not related.case_id
                        or related.case_id in requested_cases
                    )
                ):
                    tasks.add(related_id)
                    queue.append(related_id)
    if not cases:
        raise ValueError("context scope has no linked case")
    if len(cases) > 100:
        raise ValueError("context scope exceeds 100 cases; narrow the selection")
    return cases, tasks, deliverables, releases


def _row(kind, record, content=None):
    value = record.model_dump(mode="json") if hasattr(record, "model_dump") else deepcopy(record)
    return {
        "entity_type": kind,
        "id": value["id"],
        "content": content or value,
        "provenance": {
            "id": value["id"],
            "type": kind,
            "digest": digest(value),
            "created_at": value.get("created_at"),
        },
    }


def collect_evidence(
    state,
    *,
    access,
    case_ids=(),
    task_id=None,
    release_package_id=None,
    artifact_reader: Callable | None = None,
):
    cases, tasks, deliverables, releases = resolve_scope(
        state,
        case_ids=case_ids,
        task_id=task_id,
        release_package_id=release_package_id,
    )
    if any(not access.case_visible(c) for c in case_ids):
        raise PermissionError("case context is unavailable in this scope")
    if task_id and not access.task_visible(task_id):
        raise PermissionError("task context is unavailable in this scope")
    if release_package_id and not access.release_visible(release_package_id):
        raise PermissionError("release context is unavailable in this scope")
    warnings = []
    # Filter roots before using them to select any associated review/verification evidence.
    visible_releases = {rid for rid in releases if access.release_visible(rid)}
    if visible_releases != releases:
        warnings.append({"kind": "restricted", "section": "verification"})
    releases = visible_releases
    visible_cases = {c for c in cases if access.case_visible(c)}
    if visible_cases != cases:
        warnings.append({"kind": "restricted", "section": "request"})
    cases = visible_cases
    if not cases:
        raise PermissionError("case context is unavailable in this scope")
    visible_tasks = {t for t in tasks if access.task_visible(t)}
    if visible_tasks != tasks:
        warnings.append({"kind": "restricted", "section": "tasks"})
    tasks = visible_tasks
    visible_deliverables = {
        did
        for did in deliverables
        if did in state.deliverables and state.deliverables[did].task_id in tasks
    }
    if visible_deliverables != deliverables:
        warnings.append({"kind": "unavailable", "section": "delivery"})
    deliverables = visible_deliverables
    evidence = {s: [] for s in SECTION_NAMES}
    sources = set()
    for cid in sorted(cases):
        case = state.cases[cid]
        evidence["request"].append(_row("case", case))
        sources.update(case.source_evidence_snapshot_ids)
        if case.source and not case.source_evidence_snapshot_ids:
            warnings.append({"kind": "missing", "section": "sources", "case_id": cid})
        capture = case.metadata.get("case_source_capture") or {}
        if capture.get("status") == "unavailable":
            warnings.append({"kind": "capture_unavailable", "section": "sources", "case_id": cid})
    for tid in sorted(tasks):
        task = state.tasks[tid]
        evidence["plans"].append(
            _row(
                "task",
                task,
                {
                    k: v
                    for k, v in task.model_dump(mode="json").items()
                    if k
                    in {
                        "id",
                        "case_id",
                        "title",
                        "description",
                        "state",
                        "parent_task_id",
                        "originating_task_id",
                        "dependency_task_ids",
                        "created_at",
                        "updated_at",
                    }
                },
            )
        )
        sources.update(task.source_evidence_snapshot_ids)
    for sid in sorted(sources):
        if not access.source_visible(sid):
            warnings.append({"kind": "unavailable", "section": "sources"})
            continue
        source = state.external_source_evidence_snapshots[sid]
        payload = external_source_evidence_snapshot_payload(source)
        evidence["sources"].append(_row("source", source, payload))
        if payload.get("effective_freshness_status") != "current":
            warnings.append({"kind": "stale", "section": "sources", "source_id": sid})
        discussion_capture = source.metadata.get("discussion_capture")
        if discussion_capture is not None and not discussion_capture.get("complete"):
            warnings.append(
                {"kind": "incomplete_discussion", "section": "sources", "source_id": sid}
            )
    notes = legacy_notes(state)
    superseded = {n.supersedes_note_id for n in notes.values() if access.note_visible(n)}
    for note in sorted(notes.values(), key=lambda n: (n.created_at, n.id)):
        relevant = (note.case_id in cases and not note.task_ids) or bool(set(note.task_ids) & tasks)
        if relevant and access.note_visible(note):
            content = note.model_dump(mode="json")
            content["status"] = "superseded" if note.id in superseded else "active"
            evidence["decisions"].append(_row("note", note, content))
    artifact_deliverables = {did for tid in tasks for did in state.tasks[tid].deliverable_ids}
    for did in sorted(deliverables | artifact_deliverables):
        item = state.deliverables.get(did)
        if not item or item.task_id not in tasks:
            warnings.append({"kind": "unavailable", "section": "delivery"})
            continue
        if did in deliverables:
            evidence["delivery"].append(_row("deliverable", item))
        for ref in item.artifact_refs:
            if artifact_reader:
                document = artifact_reader(ref, item)
                if document:
                    document = {
                        **document,
                        "relationship": (
                            "selected_deliverable_artifact"
                            if did in deliverables
                            else "supporting_task_artifact"
                        ),
                    }
                    evidence["plans"].append(_row("artifact", document))
                    warnings.extend(
                        {
                            "kind": "artifact_omission",
                            "section": "plans",
                            "artifact_ref": ref,
                            "detail": str(omission),
                        }
                        for omission in document.get("omissions", [])
                    )
                    continue
            warnings.append({"kind": "unavailable", "section": "plans", "artifact_ref": ref})
    review_ids = set()
    for review in state.review_requests.values():
        if review.deliverable_id in deliverables or review.release_package_id in releases:
            if access.reader and access.reader.get_review_request(review.id) is None:
                continue
            review_ids.add(review.id)
            evidence["review"].append(_row("review", review))
    for collection, kind, section in (
        (state.release_verification_runs, "verification", "verification"),
        (state.human_review_requests, "human_review", "review"),
    ):
        for item in collection.values():
            if item.release_package_id in releases:
                # Full human-review identities require the operator surface.
                if kind == "human_review" and access.reader:
                    warnings.append({"kind": "restricted", "section": "review"})
                    continue
                evidence[section].append(_row(kind, item))
    human_ids = {r["id"] for r in evidence["review"] if r["entity_type"] == "human_review"}
    for item in state.human_review_findings.values():
        if item.source_human_review_request_id in human_ids:
            evidence["review"].append(_row("human_finding", item))
    for item in state.human_review_decisions.values():
        if item.human_review_request_id in human_ids:
            evidence["review"].append(_row("human_decision", item))
    for rid in sorted(releases):
        release = state.release_packages[rid]
        payload = (
            access.reader.get_release_package(rid)
            if access.reader
            else release.model_dump(mode="json")
        )
        if payload is None or not access.project_visible(release.project_id):
            warnings.append({"kind": "restricted", "section": "verification"})
            continue
        evidence["verification"].append(
            _row(
                "release",
                release,
                {
                    key: payload.get(key)
                    for key in ("id", "state", "summary", "candidate_commit_sha", "manifest")
                },
            )
        )
    for item in state.inbox.values():
        if item.task_id in tasks and (access.reader is None or access.reader._inbox_visible(item)):
            evidence["conversation"].append(_row("inbox", item))
    # Linked Chat anchors are selected explicitly; unrelated conversations stay private.
    lineages = {state.tasks[t].work_lineage_id for t in tasks}
    for lid in sorted(value for value in lineages if value):
        lineage = state.work_lineages.get(lid)
        if not lineage:
            continue
        for ref in lineage.chat_source_refs:
            conversation = state.conversations.get(ref.conversation_id)
            if not conversation or (access.actor_id and conversation.agent_id != access.actor_id):
                continue
            for mid in ref.message_ids:
                message = state.conversation_messages.get(mid)
                if message and message.conversation_id == conversation.id:
                    evidence["conversation"].append(_row("chat_message", message))
    for name, records in evidence.items():
        evidence[name] = list({(r["entity_type"], r["id"]): r for r in records}.values())
    if release_package_id:
        release = state.release_packages[release_package_id]
        cutoff = release.manifest.created_at if release.manifest else release.created_at
        for rows in evidence.values():
            for record in rows:
                created = record["provenance"].get("created_at")
                if created:
                    record["content"] = {
                        **record["content"],
                        "temporal_relation": (
                            "after_candidate_manifest"
                            if datetime.fromisoformat(created) > cutoff
                            else "at_or_before_candidate_manifest"
                        ),
                    }
    return evidence, sorted(cases), warnings


def select_records(records, spec):
    selected = [
        r
        for r in records
        if (not spec.kinds or r["content"].get("kind") in spec.kinds)
        and (
            not spec.statuses
            or r["content"].get("status", r["content"].get("state")) in spec.statuses
        )
    ]
    return sorted(
        selected,
        key=lambda r: (r["provenance"].get("created_at") or "", r["id"]),
        reverse=spec.order == "newest_first",
    )


def render_context(evidence, *, view, revision, case_ids, warnings=(), cursor=0):
    if cursor < 0:
        raise ValueError("cursor must be nonnegative")
    sections, omissions, text_parts = [], list(warnings), []
    remaining = view.text_budget
    for spec in view.sections:
        records = select_records(evidence[spec.name], spec)
        page = records[cursor : cursor + spec.limit]
        rendered = []
        for record in page:
            content = json.dumps(record["content"], ensure_ascii=False, sort_keys=True)
            limit = min(spec.text_limit, max(0, remaining - len(record["id"]) - 30))
            excerpt = content[:limit]
            if len(excerpt) < len(content):
                omissions.append({"kind": "truncated", "section": spec.name, "id": record["id"]})
            rendered.append(
                {
                    **{k: v for k, v in record.items() if k != "content"},
                    "text": excerpt,
                    "truncated": len(excerpt) < len(content),
                }
            )
            if remaining > len(record["id"]) + 30:
                line = f"[{spec.name}:{record['id']}] {excerpt}\n"
                text_parts.append(line)
                remaining -= len(line)
        next_cursor = cursor + len(page) if cursor + len(page) < len(records) else None
        if next_cursor is not None:
            omissions.append(
                {
                    "kind": "paginated",
                    "section": spec.name,
                    "omitted_count": len(records) - cursor - len(page),
                }
            )
        sections.append(
            {
                "name": spec.name,
                "records": rendered,
                "total": len(records),
                "next_cursor": next_cursor,
            }
        )
    return {
        "schema_version": 1,
        "revision": revision,
        "case_ids": case_ids,
        "view": view.model_dump(mode="json"),
        "sections": sections,
        "text": "".join(text_parts),
        "omissions": omissions,
        "complete": not omissions,
        "evidence_digest": digest(evidence),
    }


def delta_context(evidence, baseline, *, view, revision, case_ids, warnings):
    """Compare record content, not snapshot IDs or changing capture revisions."""
    changed = {name: [] for name in SECTION_NAMES}
    removed = {}
    for name, records in evidence.items():
        previous = {
            (r["entity_type"], r["id"]): digest(r["content"])
            for r in baseline.evidence.get(name, [])
        }
        keys = set()
        for record in records:
            key = (record["entity_type"], record["id"])
            keys.add(key)
            if previous.get(key) != digest(record["content"]):
                changed[name].append(record)
        if previous.keys() - keys:
            removed[name] = len(previous.keys() - keys)
    prior_warnings = [
        w for w in baseline.context["omissions"] if w["kind"] not in {"truncated", "paginated"}
    ]
    context = render_context(
        changed,
        view=view,
        revision=revision,
        case_ids=case_ids,
        warnings=warnings if warnings != prior_warnings else [],
    )
    context.update(
        mode="delta",
        baseline_snapshot_id=baseline.id,
        changed_record_count=sum(map(len, changed.values())),
        removed_record_counts=removed,
    )
    if not context["changed_record_count"] and not removed and not context["omissions"]:
        context["text"] = (
            "Case context unchanged since the previous completed dispatch in this conversation."
        )
    elif removed:
        # Removed/restricted source text must not be reintroduced from the old snapshot.
        context["omissions"].append({"kind": "removed_or_out_of_scope", "counts": removed})
        context["complete"] = False
    return context


class CaseContextService:
    def __init__(self, repository: TeamStateRepository, *, artifact_reader=None):
        self.repository = repository
        self.artifact_reader = artifact_reader

    def evidence_page(
        self,
        *,
        section,
        state=None,
        directory_path="root",
        actor_id=None,
        case_ids=(),
        view_id="case-overview",
        view_version=None,
        cursor=0,
        limit=20,
    ):
        from .case_context_artifacts import artifact_reader

        if section not in SECTION_NAMES or cursor < 0 or not 1 <= limit <= 50:
            raise ValueError("invalid evidence section or page")
        state = state or self.repository.read()
        view = effective_view(
            state, view_id=view_id, directory_path=directory_path, version=view_version
        )
        spec = next((s for s in view.sections if s.name == section), None)
        if spec is None:
            raise ValueError("section is not part of this view")
        evidence, _, warnings = collect_evidence(
            state,
            access=CaseContextAccess(state, directory_path=directory_path, actor_id=actor_id),
            case_ids=case_ids,
            artifact_reader=self.artifact_reader
            or artifact_reader(self.repository, state, actor_id=actor_id),
        )
        records = select_records(evidence[section], spec)
        return {
            "revision": state.revision,
            "records": records[cursor : cursor + limit],
            "total": len(records),
            "omissions": warnings,
            "next_cursor": cursor + limit if cursor + limit < len(records) else None,
        }

    def assemble(
        self,
        *,
        state=None,
        directory_path="root",
        actor_id=None,
        case_ids=(),
        task_id=None,
        release_package_id=None,
        view_id="case-overview",
        view_version=None,
        cursor=0,
    ):
        state = state or self.repository.read()
        from .case_context_artifacts import artifact_reader

        view = effective_view(
            state, view_id=view_id, directory_path=directory_path, version=view_version
        )
        evidence, cases, warnings = collect_evidence(
            state,
            access=CaseContextAccess(state, directory_path=directory_path, actor_id=actor_id),
            case_ids=case_ids,
            task_id=task_id,
            release_package_id=release_package_id,
            artifact_reader=self.artifact_reader
            or artifact_reader(self.repository, state, actor_id=actor_id),
        )
        return render_context(
            evidence,
            view=view,
            revision=state.revision,
            case_ids=cases,
            warnings=warnings,
            cursor=cursor,
        )

    def save_view(self, *, directory_path, values, expected_revision, created_by):
        path = directory(directory_path)
        allowed = {"view_id", "title", "sections", "text_budget"}
        if set(values) - allowed:
            raise ValueError("unknown context view fields")
        result = None

        def mutate(state):
            nonlocal result
            version = 1 + max(
                (
                    v.version
                    for v in state.case_context_views.values()
                    if v.view_id == values.get("view_id") and v.directory_path == path
                ),
                default=0,
            )
            result = CaseContextView(
                **values,
                id=f"{path}:{values['view_id']}:{version}",
                version=version,
                directory_path=path,
                created_by=created_by,
            )
            state.case_context_views[result.id] = result

        state = self.repository.update(mutate, expected_revision=expected_revision)
        return {"revision": state.revision, "view": result.model_dump(mode="json")}

    def snapshot_in_state(
        self,
        state,
        *,
        request_id,
        directory_path="root",
        actor_id=None,
        case_ids=(),
        task_id=None,
        release_package_id=None,
        view_id="case-overview",
        view_version=None,
        dispatch_id=None,
        recipient_session_id=None,
        candidate_commit_sha=None,
        analysis_base_commit_sha=None,
        baseline_snapshot_id=None,
    ):
        if not request_id or not request_id.strip():
            raise ValueError("snapshot request_id is required")
        request = dict(
            directory_path=directory(directory_path),
            actor_id=actor_id,
            case_ids=sorted(set(case_ids)),
            task_id=task_id,
            release_package_id=release_package_id,
            view_id=view_id,
            view_version=view_version,
            dispatch_id=dispatch_id,
            recipient_session_id=recipient_session_id,
            candidate_commit_sha=candidate_commit_sha,
            analysis_base_commit_sha=analysis_base_commit_sha,
        )
        request_digest = digest(request)
        key = "case-context-" + digest([directory(directory_path), actor_id, request_id])[:32]
        existing = state.case_context_snapshots.get(key)
        if existing:
            if existing.request_digest != request_digest:
                raise ValueError("snapshot request_id reused with different inputs")
            return existing
        if release_package_id and candidate_commit_sha:
            release = state.release_packages.get(release_package_id)
            if not release or release.candidate_commit_sha != candidate_commit_sha:
                raise ValueError("snapshot candidate SHA does not match release")
        access = CaseContextAccess(state, directory_path=directory_path, actor_id=actor_id)
        from .case_context_artifacts import artifact_reader

        evidence, cases, warnings = collect_evidence(
            state,
            access=access,
            case_ids=case_ids,
            task_id=task_id,
            release_package_id=release_package_id,
            artifact_reader=self.artifact_reader
            or artifact_reader(self.repository, state, actor_id=actor_id),
        )
        view = effective_view(
            state, view_id=view_id, directory_path=directory_path, version=view_version
        )
        # Do not retain unselected sections under a narrow-view grant.
        selected = {s.name for s in view.sections}
        evidence = {name: records if name in selected else [] for name, records in evidence.items()}
        for spec in view.sections:
            evidence[spec.name] = select_records(evidence[spec.name], spec)
        context = render_context(
            evidence, view=view, revision=state.revision, case_ids=cases, warnings=warnings
        )
        context["release_focus"] = {
            "release_package_id": release_package_id,
            "candidate_commit_sha": candidate_commit_sha,
            "analysis_base_commit_sha": analysis_base_commit_sha,
        }
        baseline = state.case_context_snapshots.get(baseline_snapshot_id)
        delta = None
        if (
            baseline
            and actor_id
            and baseline.created_by_agent_id == actor_id
            and baseline.recipient_session_id == recipient_session_id
            and baseline.view == view
        ):
            delta = delta_context(
                evidence,
                baseline,
                view=view,
                revision=state.revision,
                case_ids=cases,
                warnings=warnings,
            )
        snapshot = CaseContextSnapshot(
            id=key,
            request_id=request_id,
            request_digest=request_digest,
            directory_path=directory(directory_path),
            team_ids=sorted({state.cases[c].team_id for c in cases}),
            case_ids=cases,
            task_id=task_id,
            release_package_id=release_package_id,
            dispatch_id=dispatch_id,
            recipient_session_id=recipient_session_id,
            created_by_agent_id=actor_id,
            source_revision=state.revision,
            view=view,
            content_digest=digest({"context": context, "evidence": evidence}),
            context=context,
            evidence=evidence,
            baseline_snapshot_id=baseline.id if delta else None,
            wake_delta_context=delta,
        )
        state.case_context_snapshots[key] = snapshot
        return snapshot

    def create_snapshot(self, **kwargs):
        snapshot = None

        def mutate(state):
            nonlocal snapshot
            snapshot = self.snapshot_in_state(state, **kwargs)

        self.repository.update(mutate)
        return snapshot

    def get_snapshot(
        self,
        snapshot_id,
        *,
        directory_path="root",
        actor_id=None,
        session_id=None,
        section=None,
        cursor=0,
        limit=20,
    ):
        state = self.repository.read()
        snapshot = state.case_context_snapshots.get(snapshot_id)
        if snapshot is None or not within(snapshot.directory_path, directory(directory_path)):
            raise PermissionError("snapshot is unavailable in this scope")
        granted = bool(session_id and snapshot.recipient_session_id == session_id)
        if actor_id:
            actor = state.agents.get(actor_id)
            if actor is None or actor.status.value != "active":
                raise PermissionError("case context requires an active Team agent")
        if actor_id and not granted:
            access = CaseContextAccess(state, directory_path=directory_path, actor_id=actor_id)
            if snapshot.created_by_agent_id != actor_id or any(
                not access.case_visible(c) for c in snapshot.case_ids
            ):
                raise PermissionError("snapshot is unavailable in this scope")
        if session_id and not granted and actor_id is None:
            raise PermissionError("snapshot is not granted to this session")
        result = {
            "snapshot_id": snapshot.id,
            "content_digest": snapshot.content_digest,
            "source_revision": snapshot.source_revision,
            "context": deepcopy(snapshot.context),
        }
        if section is not None:
            if section not in SECTION_NAMES or cursor < 0 or not 1 <= limit <= 50:
                raise ValueError("invalid snapshot section or page")
            records = snapshot.evidence[section]
            result["evidence"] = deepcopy(records[cursor : cursor + limit])
            result["next_cursor"] = cursor + limit if cursor + limit < len(records) else None
        return result

    def backfill_notes(self, *, expected_revision=None, dry_run=True):
        state = self.repository.read()
        notes = legacy_notes(state)
        added = sorted(set(notes) - set(state.notes))
        report = {
            "revision": state.revision,
            "note_ids": added,
            "unattributed_note_ids": [n for n in added if not notes[n].case_id],
        }
        if not dry_run:

            def mutate(current):
                current.notes.update({n: notes[n] for n in added})

            report["revision"] = self.repository.update(
                mutate,
                expected_revision=state.revision
                if expected_revision is None
                else expected_revision,
            ).revision
        return report
