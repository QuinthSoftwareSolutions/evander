from __future__ import annotations

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from evander.code_index.access import authorize_session
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import ToolName


def register_code_index_tools(
    server, context, deps, *, transport_session_map=None, audit_success, audit_failure
):
    def invoke(tool, operation, repo, sha, session_id, ctx, **kwargs):
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"managed_repo_id": repo, "commit_sha": sha, **kwargs}
        try:
            session = context.policy.require_tool(session_id, tool)
            components = deps.admin_runtime_factory()
            sha = authorize_session(components, session, repo, sha)
            service = components.repository_index_admin
            result = getattr(service, operation)(repo, session.directory_path, sha, **kwargs)
            audit_success(
                session_id,
                tool,
                args,
                {
                    "snapshot_id": result.get("snapshot_id"),
                    "commit_sha": result.get("commit_sha"),
                    "requested_commit_sha": sha,
                    "match": result.get("match"),
                    "status": result.get("status"),
                    "hit_count": len(result.get("hits", [])),
                },
            )
            return result
        except Exception as exc:
            audit_failure(session_id, tool, args, str(exc))
            raise ToolError(str(exc)) from exc

    @server.tool()
    def code_index_status(
        managed_repo_id: str,
        commit_sha: str | None = None,
        allow_baseline: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        """Read existing exact or ancestor coverage for the assigned commit; never starts a build."""
        return invoke(
            ToolName.CODE_INDEX_STATUS,
            "query_status",
            managed_repo_id,
            commit_sha,
            session_id,
            ctx,
            allow_baseline=allow_baseline,
        )

    @server.tool()
    def code_search(
        managed_repo_id: str,
        query: str,
        commit_sha: str | None = None,
        mode: str = "hybrid",
        limit: int = 10,
        offset: int = 0,
        allow_baseline: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        """Search existing exact or ancestor coverage. Baseline hits exclude changed files;
        commit_sha identifies the index, requested_commit_sha the task. Search changes locally.
        """
        return invoke(
            ToolName.CODE_SEARCH,
            "search",
            managed_repo_id,
            commit_sha,
            session_id,
            ctx,
            query=query,
            mode=mode,
            limit=limit,
            offset=offset,
            allow_baseline=allow_baseline,
        )

    @server.tool()
    def code_navigate(
        managed_repo_id: str,
        symbol: str,
        commit_sha: str | None = None,
        kind: str = "definitions",
        limit: int = 20,
        offset: int = 0,
        allow_baseline: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        """Navigate indexed symbols. Ancestor relationships require checkout verification,
        even in unchanged files; new and changed files need local inspection.
        """
        return invoke(
            ToolName.CODE_NAVIGATE,
            "navigate",
            managed_repo_id,
            commit_sha,
            session_id,
            ctx,
            symbol=symbol,
            kind=kind,
            limit=limit,
            offset=offset,
            allow_baseline=allow_baseline,
        )
