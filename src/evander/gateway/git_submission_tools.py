"""The session-bound MCP interface for committed Git work."""

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import ToolName


def register_git_submission_tools(
    server, context, deps, *, transport_session_map, audit_success, audit_failure
):
    @server.tool()
    def submit_git_changes(
        expected_commit_sha: str,
        summary_markdown: str,
        managed_repo_id: str | None = None,
        checkout_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict:
        """Submit your committed work and complete Markdown PR summary for host review/publication.

        Use the full SHA from git rev-parse HEAD; this never stages, commits, merges,
        resets, or creates an upstream branch/PR. It submits that exact commit and
        its summary (up to 32 KiB of UTF-8 text) to your authorized managed session
        ref. Supply checkout_id from your prepared task/worktree context when there is more than one
        checkout. Optional managed_repo_id narrows repository selection. Omitted
        selectors work only for one eligible writable checkout; ambiguity returns
        the available IDs without running Git. On success, use commit_sha and
        agent_branch in deliverables. On failure, follow the structured code and
        message; transport failures can be retried with the same SHA and summary.
        Uncommitted files remain untouched. Upstream publication follows the
        configured host workflow and does not confer arbitrary branch authority.
        """
        sid = _resolve_session_id_from_context(
            None, ctx, transport_session_map=transport_session_map
        )
        args = {
            "expected_commit_sha": expected_commit_sha,
            "managed_repo_id": managed_repo_id,
            "checkout_id": checkout_id,
        }
        try:
            session = context.policy.require_tool(sid, ToolName.SUBMIT_GIT_CHANGES)
            service = deps.admin_runtime_factory().git_submission_admin
            result = service.submit(
                session,
                expected_commit_sha=expected_commit_sha,
                summary_markdown=summary_markdown,
                managed_repo_id=managed_repo_id,
                checkout_id=checkout_id,
                authorize=lambda resolved: context.policy.require_tool_arguments(
                    context.policy.get_session(sid), ToolName.SUBMIT_GIT_CHANGES, resolved
                ),
            )
            if result.get("success"):
                audit_success(sid, ToolName.SUBMIT_GIT_CHANGES, args, result)
            else:
                audit_failure(sid, ToolName.SUBMIT_GIT_CHANGES, args, result.get("code"))
            return result
        except Exception as exc:
            audit_failure(sid, ToolName.SUBMIT_GIT_CHANGES, args, str(exc))
            raise ToolError(str(exc)) from exc
