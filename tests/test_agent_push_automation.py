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
    assert 'PR_ARGS+=(--session-id "${session_id}")' in handle_push
    assert "AGENT_PR_NOTES" in handle_push
    assert "refs/notes/agent-pr" in handle_push
    assert "AGENT_SANDBOX_PR_FIXED_BODY_FILE" in handle_push
    assert "AGENT_SANDBOX_PR_BODY_MAX_BYTES" in handle_push
    assert "resolve_pr_template_body_file()" in handle_push
    assert "pr-template.json" in handle_push
    assert "## Agent Summary" in handle_push
    assert "compose_pr_body_file()" in handle_push
    assert '--body-file "${pr_body_file}"' in handle_push
    assert "AZURE_DEVOPS_PR_PAT" in open_pr
    assert "ssh.dev.azure.com" in open_pr
    assert '"api-version": "7.1"' in open_pr or "?api-version=7.1" in open_pr
    assert "AGENT_PR_RESULT " in open_pr
    assert "runtime/pr-map.json" in open_pr


def test_issue_vm_setup_configures_agent_remote_for_push_alias() -> None:
    setup_issue_vm = Path("/home/jonathan/agent-sandbox/scripts/setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )
    guest_agents_template = Path(
        "/home/jonathan/agent-sandbox/templates/guest-AGENTS.md.tmpl"
    ).read_text(encoding="utf-8")

    assert 'git -C "${TARGET_DIR}" remote add agent "${REMOTE_URL}"' in setup_issue_vm
    assert 'AGENT_PR_NOTE_HELPER_B64="$(' in setup_issue_vm
    assert "base64.b64encode(Path(sys.argv[1]).read_bytes()).decode()" in setup_issue_vm
    assert 'AGENT_PR_NOTE_HELPER_B64=\'${AGENT_PR_NOTE_HELPER_B64}\'' in setup_issue_vm
    assert 'python3 - "${TARGET_DIR}/.agent-sandbox/bin/agent-pr-note" "${AGENT_PR_NOTE_HELPER_B64}"' in setup_issue_vm
    assert "target_path.write_bytes(base64.b64decode(sys.argv[2]))" in setup_issue_vm
    assert 'config alias.agent-pr-note' in setup_issue_vm
    assert 'config agent-sandbox.sessionId "${SESSION_ID}"' in setup_issue_vm
    assert 'config agent-sandbox.branchSuffix "${AGENT_BRANCH_SUFFIX}"' in setup_issue_vm
    assert '"${repo_root}/.agent-sandbox/bin/agent-pr-note" ensure-head' in setup_issue_vm
    assert "templates/guest-AGENTS.md.tmpl" in setup_issue_vm
    assert "Check the repository's root for an `AGENTS.md` file containing instructions local to the repository." in guest_agents_template
    assert "git push agent HEAD:refs/heads/agents/" in guest_agents_template
    assert "refs/notes/agent-pr" in guest_agents_template
    assert "SESSION_ID is not set; refusing ambiguous agent push." in setup_issue_vm
    assert "AGENT_BRANCH_SUFFIX/HOSTNAME is empty; refusing ambiguous agent push." in setup_issue_vm
    assert 'session_id=${SESSION_ID:-$(git config --get agent-sandbox.sessionId || true)}' in setup_issue_vm
    assert 'suffix_source=${AGENT_BRANCH_SUFFIX:-$(git config --get agent-sandbox.branchSuffix || true)}' in setup_issue_vm
    assert 'if [ -z "${session_id}" ]; then' in setup_issue_vm
    assert 'if [ -z "${suffix}" ]; then' in setup_issue_vm
    assert 'HEAD:refs/heads/agents/${session_id}/${branch}-${suffix}' in setup_issue_vm
    assert 'HEAD:refs/heads/agents/${SESSION_ID}/$(git rev-parse --abbrev-ref HEAD)-${AGENT_BRANCH_SUFFIX:-${HOSTNAME}} refs/notes/agent-pr' in guest_agents_template


def test_guest_agent_pr_note_helper_supports_additive_updates() -> None:
    helper = Path("/home/jonathan/agent-sandbox/scripts/guest/agent_pr_note.sh").read_text(
        encoding="utf-8"
    )

    assert "append (-m TEXT | -F FILE)" in helper
    assert "ensure-head" in helper
    assert "agent-sandbox/agent-pr-cache" in helper
    assert "git notes --ref=agent-pr add -f -F" in helper
    assert "Run 'git agent-pr-note set -m" in helper
