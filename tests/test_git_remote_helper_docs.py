from pathlib import Path


def test_issue_mirror_helper_is_documented() -> None:
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    git_remotes = Path("/home/jonathan/agent-sandbox/docs/git-remotes.md").read_text(
        encoding="utf-8"
    )

    assert "mirror_issue_repo.sh" in readme
    assert "mirror_issue_repo.sh" in ops
    assert "mirror_issue_repo.sh" in git_remotes
    assert "bootstrap_git_vm.sh" in git_remotes
    assert "setup_issue_git_vm.sh" in readme
    assert "setup_issue_git_vm.sh" in git_remotes
    assert "promote_agent_branch.sh" in git_remotes
    assert "open_agent_pr.sh" in git_remotes
