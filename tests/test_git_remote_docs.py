from pathlib import Path


def test_git_remote_docs_and_script_are_present() -> None:
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    git_docs = Path("/home/jonathan/agent-sandbox/docs/git-remotes.md").read_text(encoding="utf-8")
    script = Path("/home/jonathan/agent-sandbox/scripts/create_git_remote.sh").read_text(
        encoding="utf-8"
    )
    promote_script = Path(
        "/home/jonathan/agent-sandbox/scripts/promote_agent_branch.sh"
    ).read_text(encoding="utf-8")
    pr_script = Path("/home/jonathan/agent-sandbox/scripts/open_agent_pr.sh").read_text(
        encoding="utf-8"
    )

    assert (
        "create_git_remote.sh" in readme or "setup_issue_git_vm.sh" in readme
    )
    assert ("create_git_remote.sh" in ops or "setup_issue_git_vm.sh" in ops)
    assert "bootstrap_git_vm.sh" in readme
    assert "bootstrap_git_vm.sh" in ops
    assert "setup_issue_vm.sh" in readme
    assert "setup_issue_vm.sh" in ops
    assert "promote_agent_branch.sh" in readme
    assert "promote_agent_branch.sh" in ops
    assert "open_agent_pr.sh" in readme
    assert "open_agent_pr.sh" in ops
    assert "refs/heads/agents/" in git_docs
    assert "review/" in git_docs
    assert "git init --bare" in script
    assert "switch -c" in promote_script
    assert "gh" in pr_script
