from pathlib import Path


def test_bare_repo_flow_includes_hook_and_azure_support() -> None:
    create_remote = Path("/home/jonathan/agent-sandbox/scripts/create_git_remote.sh").read_text(
        encoding="utf-8"
    )
    mirror_issue = Path("/home/jonathan/agent-sandbox/scripts/mirror_issue_repo.sh").read_text(
        encoding="utf-8"
    )
    handle_push = Path("/home/jonathan/agent-sandbox/scripts/handle_agent_push.sh").read_text(
        encoding="utf-8"
    )
    open_pr = Path("/home/jonathan/agent-sandbox/scripts/open_agent_pr.sh").read_text(
        encoding="utf-8"
    )

    assert "hooks/post-receive" in create_remote
    assert "agent-sandbox.trustedRepoPath" in mirror_issue
    assert "agent-sandbox.baseBranch" in mirror_issue
    assert "runtime/agent-events.log" in handle_push
    assert "AZURE_DEVOPS_PAT" in open_pr
    assert "ssh.dev.azure.com" in open_pr
    assert '"api-version": "7.1"' in open_pr or "?api-version=7.1" in open_pr


def test_issue_vm_setup_configures_agent_remote_for_push_alias() -> None:
    setup_issue_vm = Path("/home/jonathan/agent-sandbox/scripts/setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )

    assert 'git -C "${TARGET_DIR}" remote add agent "${REMOTE_URL}"' in setup_issue_vm
    assert "git push agent HEAD:refs/heads/agents/" in setup_issue_vm
