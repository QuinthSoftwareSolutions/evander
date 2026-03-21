from pathlib import Path


def test_bare_repo_flow_includes_hook_and_azure_support() -> None:
    root = Path(__file__).resolve().parents[1]
    create_remote = (root / "scripts/create_git_remote.sh").read_text(
        encoding="utf-8"
    )
    mirror_issue = (root / "scripts/mirror_issue_repo.sh").read_text(
        encoding="utf-8"
    )
    handle_push = (root / "scripts/handle_agent_push.sh").read_text(
        encoding="utf-8"
    )
    open_pr = (root / "scripts/open_agent_pr.sh").read_text(
        encoding="utf-8"
    )

    assert "hooks/post-receive" in create_remote
    assert "agent-sandbox.trustedRepoPath" in mirror_issue
    assert "agent-sandbox.baseBranch" in mirror_issue
    assert "runtime/agent-events.log" in handle_push
    assert "extract_session_id()" in handle_push
    assert 'PR_ARGS+=(--session-id "${SESSION_ID}")' in handle_push
    assert "AZURE_DEVOPS_PAT" in open_pr
    assert "ssh.dev.azure.com" in open_pr
    assert '"api-version": "7.1"' in open_pr or "?api-version=7.1" in open_pr
    assert "AGENT_PR_RESULT " in open_pr
    assert "runtime/pr-map.json" in open_pr
