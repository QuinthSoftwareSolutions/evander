from __future__ import annotations

from types import SimpleNamespace

from agent_sandbox.issue_orchestrator import IssueOrchestratorWorker


def test_originator_prompt_requires_mcp_issue_tools_and_forbids_cli_fallback() -> None:
    worker = object.__new__(IssueOrchestratorWorker)
    session = SimpleNamespace(session_id="idea-session-123")

    prompt = worker._prompt_for_session(  # noqa: SLF001
        session,
        role="idea_generator",
        automation={},
        approved_count=0,
        new_issue_count=0,
        committed_count=0,
    )

    assert prompt is not None
    assert "list_issues MCP tool" in prompt
    assert "get_issue MCP tool" in prompt
    assert "create_local_issue MCP tool" in prompt
    assert "session_id=idea-session-123" in prompt
    assert "Do not use the local CLI" in prompt
    assert "Do not write issue files directly under runtime/" in prompt
