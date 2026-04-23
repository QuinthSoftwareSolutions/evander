from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_git_remote_docs_and_script_are_present() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    git_docs = (ROOT / "docs" / "git-remotes.md").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "create_git_remote.sh").read_text(encoding="utf-8")
    promote_script = (ROOT / "scripts" / "promote_agent_branch.sh").read_text(encoding="utf-8")
    pr_script = (ROOT / "scripts" / "open_agent_pr.sh").read_text(encoding="utf-8")

    assert (
        "create_git_remote.sh" in readme or "setup_issue_git_vm.sh" in readme
    )
    assert ("create_git_remote.sh" in ops or "setup_issue_git_vm.sh" in ops)
    assert "bootstrap_git_vm.sh" in readme
    assert "bootstrap_git_vm.sh" in ops
    assert "agent-sandbox-cli list-vms" in readme
    assert "agent-sandbox-cli list-vms" in ops
    assert "agent-sandbox-cli logs" in readme
    assert "agent-sandbox-cli logs" in ops
    assert "check_issue_vm.sh" in readme
    assert "check_issue_vm.sh" in ops
    assert "AGENT_TASK.yaml" in readme
    assert "AGENT_TASK.yaml" in ops
    assert "create-issue-vm" in readme
    assert "create-issue-vm" in ops
    assert "setup_vm.sh" in ops
    assert ".agent-sandbox" in readme
    assert ".agent-sandbox" in ops
    assert "promote_agent_branch.sh" in readme
    assert "promote_agent_branch.sh" in ops
    assert "open_agent_pr.sh" in readme
    assert "open_agent_pr.sh" in ops
    assert "post-receive" in git_docs
    assert "AZURE_DEVOPS_PR_PAT" in git_docs
    assert "refs/heads/agents/" in git_docs
    assert "review/" in git_docs
    assert "git init --bare" in script
    assert "switch -c" in promote_script
    assert "gh" in pr_script
