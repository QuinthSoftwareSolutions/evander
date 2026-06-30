from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bare_repo_flow_includes_hook_and_azure_support() -> None:
    root = Path(__file__).resolve().parents[1]
    create_remote = (root / "scripts/create_git_remote.sh").read_text(encoding="utf-8")
    mirror_issue = (root / "scripts/mirror_issue_repo.sh").read_text(encoding="utf-8")
    handle_push = (root / "scripts/handle_agent_push.sh").read_text(encoding="utf-8")
    open_pr = (root / "scripts/open_agent_pr.sh").read_text(encoding="utf-8")

    assert "hooks/post-receive" in create_remote
    assert "handle_git_push_trigger.sh" in create_remote
    assert "handle_agent_push.sh" not in create_remote
    assert 'status="\\${trigger_status}"' in create_remote
    assert "evander.trustedRepoPath" in mirror_issue
    assert "evander.baseBranch" in mirror_issue
    assert "runtime/agent-events.log" in handle_push
    assert "extract_session_id()" in handle_push
    assert 'PR_ARGS+=(--session-id "${session_id}")' in handle_push
    assert "AGENT_PR_NOTES" in handle_push
    assert "refs/notes/agent-pr" in handle_push
    assert "EVANDER_PR_FIXED_BODY_FILE" in handle_push
    assert "EVANDER_PR_BODY_MAX_BYTES" in handle_push
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
    assert "fcntl.flock" in open_pr
    assert 'lock_path = path.with_name(f".{path.name}.lock")' in open_pr
    assert "os.replace(temp_path, path)" in open_pr


def test_issue_vm_setup_configures_agent_remote_for_push_alias() -> None:
    clone_runtime = (
        ROOT / "src" / "evander" / "admin" / "services" / "vm_agent" / "vm_repo.py"
    ).read_text(encoding="utf-8")
    project_helpers = (
        ROOT / "src" / "evander" / "admin" / "services" / "project_helpers.py"
    ).read_text(encoding="utf-8")
    guest_agents_template = (ROOT / "templates" / "guest-AGENTS.md.tmpl").read_text(
        encoding="utf-8"
    )

    assert 'git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"' in clone_runtime
    assert 'AGENT_PR_NOTE_HELPER_B64="' in clone_runtime
    assert 'AGENT_PUSH_HELPER_B64="' in clone_runtime
    assert 'SYNC_BASE_HELPER_B64="' in clone_runtime
    assert "def guest_agent_pr_note_helper_b64(project_dir: Path) -> str:" in project_helpers
    assert "def guest_push_agent_helper_b64(project_dir: Path) -> str:" in project_helpers
    assert "def guest_sync_base_helper_b64(project_dir: Path) -> str:" in project_helpers
    assert 'return base64.b64encode(helper_path.read_bytes()).decode("ascii")' in project_helpers
    assert (
        'python3 - "${{TARGET_DIR}}/.evander/bin/agent-pr-note" "${{AGENT_PR_NOTE_HELPER_B64}}"'
        in clone_runtime
    )
    assert (
        'python3 - "${{TARGET_DIR}}/.evander/bin/push-agent" "${{AGENT_PUSH_HELPER_B64}}"'
        in clone_runtime
    )
    assert (
        'python3 - "${{TARGET_DIR}}/.evander/bin/sync-base" "${{SYNC_BASE_HELPER_B64}}"'
        in clone_runtime
    )
    assert "target_path.write_bytes(base64.b64decode(sys.argv[2]))" in clone_runtime
    assert "config alias.agent-pr-note" in clone_runtime
    assert "config alias.push-agent" in clone_runtime
    assert "config alias.sync-base" in clone_runtime
    assert 'config evander.sessionId "${{SESSION_ID}}"' in clone_runtime
    assert 'config evander.prNotesRef "${{PR_NOTES_REF}}"' in clone_runtime
    assert 'config evander.issueNumber "{issue_number}"' in clone_runtime
    assert 'config evander.destinationBranch "feature/issue-{issue_number}"' in clone_runtime
    assert 'config evander.branchSuffix "${VM_NAME}"' in clone_runtime
    assert 'config evander.workBranch "${{WORK_BRANCH}}"' in clone_runtime
    assert 'git -C "${{TARGET_DIR}}" switch -c "${{WORK_BRANCH}}"' in clone_runtime
    assert (
        "Check the repository's root for an `AGENTS.md` file containing instructions local to the repository."
        in guest_agents_template
    )
    assert "git push agent HEAD:refs/heads/agents/" in guest_agents_template
    assert "evander.prNotesRef" in guest_agents_template
    assert "`push-agent` refuses to run when `SESSION_ID` is missing" in guest_agents_template
    assert "suffix resolves empty" in guest_agents_template
    assert (
        "`push-agent` fetches and verifies the configured destination branch"
        in guest_agents_template
    )
    assert "`sync-base` fetches and merges the configured base branch" in guest_agents_template


def test_guest_agent_pr_note_helper_supports_additive_updates() -> None:
    helper = (ROOT / "scripts" / "guest" / "agent_pr_note.sh").read_text(encoding="utf-8")

    assert "append (-m TEXT | -F FILE)" in helper
    assert "ensure-head" in helper
    assert "evander/agent-pr-cache" in helper
    assert "git config --get evander.prNotesRef" in helper
    assert 'git notes --ref="$(pr_notes_ref)" add -f -F' in helper
    assert "Run 'git agent-pr-note set -m" in helper


def test_guest_push_agent_helper_preflights_destination_branch_before_push() -> None:
    helper = (ROOT / "scripts" / "guest" / "push_agent.sh").read_text(encoding="utf-8")

    assert "evander.baseBranch" in helper
    assert "evander.destinationBranch" in helper
    assert "issue_number_from_value" in helper
    assert "git ls-remote --exit-code" in helper
    assert "git fetch" in helper
    assert "merge-base --is-ancestor" in helper
    assert "Refusing to push: " in helper
    assert "git sync-base" in helper
    assert (
        'git push agent "HEAD:refs/heads/agents/${session_id_value}/${branch}-${suffix}"' in helper
    )


def test_guest_sync_base_helper_fetches_and_merges_configured_base_branch() -> None:
    helper = (ROOT / "scripts" / "guest" / "sync_base.sh").read_text(encoding="utf-8")

    assert "evander.baseBranch" in helper
    assert "git fetch" in helper
    assert "git merge --no-edit" in helper
    assert "git agent-pr-note append" in helper
