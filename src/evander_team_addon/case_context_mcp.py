"""Narrow MCP adapters for the shared case-context service."""

from mcp.server.fastmcp.exceptions import ToolError

from .services.case_context_service import CaseContextAccess, CaseContextService, legacy_notes

CASE_CONTEXT_TOOLS = (
    "team_list_notes",
    "team_get_note",
    "team_get_case_context",
    "team_get_case_context_snapshot",
)


def register_case_context_tools(server, resolver, repository, *, artifact_reader=None):
    service = CaseContextService(repository, artifact_reader=artifact_reader)

    def team_list_notes(
        case_id: str | None = None,
        task_id: str | None = None,
        kind: str | None = None,
        cursor: int = 0,
        limit: int = 20,
        session_id: str | None = None,
    ):
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name="team_list_notes",
        )
        if cursor < 0 or not 1 <= limit <= 50:
            raise ToolError("invalid note page")
        access = CaseContextAccess(caller.state, actor_id=caller.agent.id)
        notes = [
            n
            for n in legacy_notes(caller.state).values()
            if access.note_visible(n)
            and (not case_id or n.case_id == case_id)
            and (not task_id or task_id in n.task_ids)
            and (not kind or n.kind == kind)
        ]
        notes.sort(key=lambda n: (n.created_at, n.id), reverse=True)
        return {
            "success": True,
            "revision": caller.state.revision,
            "records": [
                {
                    "id": n.id,
                    "kind": n.kind,
                    "summary": n.summary or n.body[:240],
                    "case_id": n.case_id,
                    "task_ids": n.task_ids,
                    "supersedes_note_id": n.supersedes_note_id,
                    "created_by_agent_id": n.created_by_agent_id,
                    "created_at": n.created_at.isoformat(),
                }
                for n in notes[cursor : cursor + limit]
            ],
            "total": len(notes),
            "next_cursor": cursor + limit if cursor + limit < len(notes) else None,
        }

    def team_get_note(note_id: str, session_id: str | None = None):
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name="team_get_note",
        )
        note = legacy_notes(caller.state).get(note_id)
        if note is None or not CaseContextAccess(
            caller.state, actor_id=caller.agent.id
        ).note_visible(note):
            raise ToolError("note is unavailable in this scope")
        return {"success": True, "note": note.model_dump(mode="json")}

    def team_get_case_context(
        case_id: str | None = None,
        task_id: str | None = None,
        release_package_id: str | None = None,
        view_id: str = "case-overview",
        cursor: int = 0,
        session_id: str | None = None,
    ):
        caller = resolver.require_persistent_read_context(
            session_id=session_id,
            tool_name="team_get_case_context",
        )
        try:
            project_ids = (
                {caller.state.cases[case_id].project_id} if case_id in caller.state.cases else set()
            )
            if task_id in caller.state.tasks:
                project_ids.add(caller.state.tasks[task_id].project_id)
            if release_package_id in caller.state.release_packages:
                project_ids.add(caller.state.release_packages[release_package_id].project_id)
            paths = {
                caller.state.projects[p].directory_path
                for p in project_ids
                if p in caller.state.projects
            }
            return {
                "success": True,
                **service.assemble(
                    state=caller.state,
                    actor_id=caller.agent.id,
                    directory_path=next(iter(paths)) if len(paths) == 1 else "root",
                    case_ids=[case_id] if case_id else [],
                    task_id=task_id,
                    release_package_id=release_package_id,
                    view_id=view_id,
                    cursor=cursor,
                ),
            }
        except (ValueError, PermissionError) as exc:
            raise ToolError(str(exc)) from exc

    def team_get_case_context_snapshot(
        snapshot_id: str,
        section: str | None = None,
        cursor: int = 0,
        limit: int = 20,
        session_id: str | None = None,
    ):
        # Detached consumers need only an exact snapshot grant, not Team membership.
        caller = resolver.resolve(session_id=session_id, tool_name="team_get_case_context_snapshot")
        try:
            return {
                "success": True,
                **service.get_snapshot(
                    snapshot_id,
                    session_id=caller.session_id,
                    actor_id=caller.agent_id,
                    section=section,
                    cursor=cursor,
                    limit=limit,
                ),
            }
        except (ValueError, PermissionError) as exc:
            raise ToolError(str(exc)) from exc

    for function, description in (
        (team_list_notes, "List authorized case/task journal notes; compact, paginated records."),
        (team_get_note, "Read one authorized journal note, including its decision rationale."),
        (
            team_get_case_context,
            "Read source-backed case history using a configurable view. "
            "Shows omissions; use note/source/artifact detail tools for full evidence.",
        ),
        (
            team_get_case_context_snapshot,
            "Read a frozen case context granted to this session. "
            "Use section and cursor to retrieve complete source records beyond the brief.",
        ),
    ):
        server.add_tool(function, name=function.__name__, description=description)
