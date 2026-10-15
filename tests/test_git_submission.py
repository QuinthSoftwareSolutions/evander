from __future__ import annotations

import base64
import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
import shlex
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
import subprocess
from types import SimpleNamespace

import pytest

from evander.admin.services.git_submission_service import GitSubmissionService
from evander.config import GatewayConfig
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.provider_models import AgentExecEffectDefinition
from evander.execution.models import ExecutionStatusKind
from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime

from test_guest_push_agent_script import (
    ROOT,
    SESSION_ID,
    WORK_BRANCH,
    _create_issue_repos,
    _commit_agent_work,
    _git,
    _git_dir,
)

REPO_ID = "mr_0123456789abcdef0123"
GUEST_PATH = "/home/agent/repos/Example"
SUMMARY = (
    "## Changes\n\nSubmit a committed change.\n\n## Validation\n\nSynthetic Git checks passed."
)
UTF8_SUMMARY = "## Changes\n\n" + "é" * 16370


@pytest.fixture
def setup_submission(tmp_path):
    source, agent, bare = _create_issue_repos(tmp_path)
    _git_dir(bare, "fetch", str(source), "+refs/heads/*:refs/heads/*")
    sha = _commit_agent_work(agent)
    config = GatewayConfig(
        runtime_dir=tmp_path / "runtime",
        session_store_path=tmp_path / "runtime/sessions.json",
        audit_log_path=tmp_path / "runtime/audit.jsonl",
        approval_store_path=tmp_path / "runtime/approvals.json",
    )
    runtime = build_runtime(config)
    session = SessionManifest(
        session_id=SESSION_ID,
        vm_id="vm-example",
        allowed_tools=[ToolName.SUBMIT_GIT_CHANGES],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={
            "managed_git_access_grants": {
                REPO_ID: {
                    "managed_repo_id": REPO_ID,
                    "access_mode": "write",
                    "lifetime": "session",
                    "git_ssh_alias": "evander-example",
                }
            }
        },
    )
    runtime.session_repository.upsert_session(session)
    managed = SimpleNamespace(
        runtime_dir=config.runtime_dir,
        clone_info=lambda *a, **kw: {"clone_url": "ssh://evander-host/managed/example.git"},
        resolve_internal_repository=lambda *a: (bare, bare.parent),
    )
    calls = []

    def execute(session, command, working_directory):
        calls.append(working_directory)
        original_request = base64.b64decode(command[-1])
        request = json.loads(original_request)
        request["remote_url"] = str(bare)
        request["guest_path"] = str(agent)
        # Keep the original encoding while mapping the transport into local Git.
        # Re-encoding escaped Unicode as UTF-8 would mask argv-size failures.
        command = [
            *command[:-1],
            base64.b64encode(
                json.dumps(request, ensure_ascii=original_request.isascii()).encode()
            ).decode(),
        ]
        result = subprocess.run(command, cwd=source, capture_output=True, text=True, timeout=20)
        return dict(stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)

    service = GitSubmissionService(runtime, managed, ROOT, execute=execute)
    cid = service.register_checkout(
        session_id=SESSION_ID, managed_repo_id=REPO_ID, guest_path=GUEST_PATH, base_branch="main"
    )
    return SimpleNamespace(
        service=service,
        runtime=runtime,
        session=session,
        agent=agent,
        bare=bare,
        source=source,
        sha=sha,
        checkout_id=cid,
        calls=calls,
        execute=execute,
        config=config,
    )


def submit(s, *, summary_markdown=SUMMARY, **kwargs):
    return s.service.submit(
        s.session, expected_commit_sha=s.sha, summary_markdown=summary_markdown, **kwargs
    )


@pytest.mark.parametrize(
    "summary_markdown",
    [
        SUMMARY,
        "## Changes\n\nHard line break  \nNext line\n\n\nParagraph after two blank lines.\n\n## Validation\n\nPassed.",
        "\n\n    indented code\n    next line\n\n",
        "## Changes\r\n\r\nFirst line  \r\nNext line\r\n",
        UTF8_SUMMARY,
    ],
    ids=["standard", "markdown-whitespace", "outer-whitespace", "crlf", "utf8-near-limit"],
)
def test_submits_exact_commit_summary_and_replays_without_changing_refs(
    setup_submission, summary_markdown
):
    s = setup_submission
    (s.agent / "file.txt").write_text("uncommitted tracked work\n")
    (s.agent / "untracked.txt").write_text("private pending work\n")
    before = _git(s.agent, "status", "--porcelain").stdout
    receipt = submit(s, summary_markdown=summary_markdown)
    assert receipt["success"], receipt
    assert receipt["commit_sha"] == s.sha
    assert receipt["agent_ref"] == f"refs/evander/agents/{SESSION_ID}/{WORK_BRANCH}-vm-test"
    assert receipt["checkout_id"] == s.checkout_id
    assert receipt["changed"] is True
    note_ref = agent_pr_notes_ref_for_session(SESSION_ID)
    note_blob = _git_dir(s.bare, "notes", f"--ref={note_ref}", "list", s.sha).stdout.strip()
    assert subprocess.run(
        ["git", f"--git-dir={s.bare}", "cat-file", "blob", note_blob],
        capture_output=True,
        check=True,
    ).stdout == summary_markdown.encode("utf-8")
    refs = _git_dir(s.bare, "show-ref").stdout
    replay = submit(s, summary_markdown=summary_markdown)
    assert replay["success"], replay
    assert replay["changed"] is False
    assert _git_dir(s.bare, "show-ref").stdout == refs
    assert _git(s.agent, "status", "--porcelain").stdout == before
    with s.service.store.connection() as conn:
        assert conn.execute("select count(*) from receipts").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["read_only", "foreign", "foreign_repo", "retired", "ambiguous"])
def test_denial_and_ambiguity_do_not_execute_git(setup_submission, kind):
    s = setup_submission
    kwargs = {}
    if kind == "read_only":
        session = s.session.model_copy(deep=True)
        session.metadata["managed_git_access_grants"][REPO_ID]["access_mode"] = "read-only"
        s.session = session
    elif kind == "foreign":
        kwargs["checkout_id"] = s.service.register_checkout(
            session_id="another-session", managed_repo_id=REPO_ID, guest_path=GUEST_PATH
        )
    elif kind == "foreign_repo":
        kwargs["managed_repo_id"] = "mr_another"
    elif kind == "retired":
        s.service.store.retire(s.checkout_id)
    else:
        s.service.register_checkout(
            session_id=SESSION_ID,
            managed_repo_id=REPO_ID,
            guest_path="/home/agent/worktrees/Example/task-two",
        )
    result = submit(s, **kwargs)
    assert result["success"] is False
    assert (
        result["code"]
        == {
            "read_only": "insufficient_access",
            "foreign": "checkout_unavailable",
            "foreign_repo": "checkout_unavailable",
            "retired": "checkout_unavailable",
            "ambiguous": "checkout_ambiguous",
        }[kind]
    )
    assert not s.calls
    assert "guest_path" not in json.dumps(result)


def test_rechecks_grants_after_selector_resolution(setup_submission):
    s = setup_submission

    def revoke(_):
        session = s.session.model_copy(deep=True)
        session.metadata["managed_git_access_grants"] = {}
        s.runtime.session_repository.upsert_session(session)

    result = s.service.submit(
        s.session, expected_commit_sha=s.sha, summary_markdown=SUMMARY, authorize=revoke
    )
    assert result["success"] is False
    assert not s.calls


def test_missing_physical_checkout_returns_reconciliation_failure(setup_submission):
    s = setup_submission
    s.agent.rename(s.agent.with_name("saved-checkout"))
    result = submit(s)
    assert result["code"] == "checkout_unavailable", result
    assert not _git_dir(s.bare, "for-each-ref", "refs/evander/agents").stdout


def test_changed_head_and_ancestry_blockers_preserve_remote(setup_submission):
    s = setup_submission
    s.sha = "0" * 40
    result = submit(s)
    assert result["code"] == "head_moved"
    assert not _git_dir(s.bare, "for-each-ref", "refs/evander/agents").stdout
    s.sha = _git(s.agent, "rev-parse", "HEAD").stdout.strip()
    _git(s.agent, "branch", "-m", "agents/another-session/task")
    assert submit(s)["code"] == "branch_mismatch"
    _git(s.agent, "branch", "-m", WORK_BRANCH)
    _git(s.agent, "checkout", "--detach", "HEAD")
    assert submit(s)["code"] == "branch_mismatch"
    _git(s.agent, "switch", WORK_BRANCH)
    _git(s.source, "checkout", "main")
    (s.source / "base-later.txt").write_text("new base\n")
    _git(s.source, "add", ".")
    _git(s.source, "commit", "-m", "Advance required base")
    _git(s.source, "push", "origin", "main")
    _git_dir(s.bare, "fetch", str(s.source), "+refs/heads/main:refs/heads/main")
    result = submit(s)
    assert result["code"] == "ancestry_blocked", result
    assert not _git_dir(s.bare, "for-each-ref", "refs/evander/agents").stdout


def test_atomic_rejection_and_retry_after_lost_receipt(setup_submission):
    s = setup_submission
    hook = s.bare / "hooks/update"
    # Without --atomic, the branch would be accepted while only notes fail.
    hook.write_text('#!/bin/sh\ncase "$1" in refs/notes/*) exit 1;; esac\nexit 0\n')
    hook.chmod(0o755)
    before = _git_dir(s.bare, "show-ref").stdout
    result = submit(s)
    assert result["success"] is False
    assert _git_dir(s.bare, "show-ref").stdout == before
    hook.unlink()

    def lose_response(*args):
        s.execute(*args)
        raise TimeoutError("connection closed after acceptance")

    s.service.execute = lose_response
    assert submit(s)["code"] == "transport_failure"
    refs = _git_dir(s.bare, "show-ref").stdout
    s.service.execute = s.execute
    result = submit(s)
    assert result["success"] and result["changed"] is False
    assert _git_dir(s.bare, "show-ref").stdout == refs


def test_retired_owner_and_prepared_branch_are_checked(setup_submission):
    s = setup_submission
    s.service.store.retire(s.checkout_id)
    active = True
    s.service.validators["synthetic-addon"] = lambda binding: active
    cid = s.service.register_checkout(
        session_id=SESSION_ID,
        managed_repo_id=REPO_ID,
        guest_path=GUEST_PATH,
        owner_kind="synthetic-addon",
        owner_id="task-1",
        generation="first",
        fixed_branch=f"agents/{SESSION_ID}/tasks/task-1",
        prepared_base=s.sha,
    )
    assert submit(s, checkout_id=cid)["code"] == "branch_mismatch"
    _git(s.agent, "branch", "-m", f"agents/{SESSION_ID}/tasks/task-1")
    result = submit(s, checkout_id=cid)
    assert result["success"], result
    assert result["agent_ref"] == f"refs/evander/agents/{SESSION_ID}/tasks/task-1"
    active = False
    count = len(s.calls)
    assert submit(s, checkout_id=cid)["code"] == "checkout_unavailable"
    assert len(s.calls) == count


@pytest.mark.parametrize(
    "backend,lose_response",
    [
        ("libvirt_vm", False),
        ("container_worker_vm", False),
        ("container_worker_vm", True),
    ],
)
@pytest.mark.parametrize(
    "summary_markdown", [SUMMARY, UTF8_SUMMARY], ids=["standard", "utf8-near-limit"]
)
def test_real_git_submission_through_execution_adapters(
    setup_submission, monkeypatch, backend, lose_response, summary_markdown
):
    s = setup_submission
    session = s.session.model_copy(deep=True)
    session.metadata["execution_backend"] = backend
    s.runtime.session_repository.upsert_session(session)
    s.session = session
    config = s.config.model_copy(
        update={"execution_backend": backend, "container_worker_base_url": "http://127.0.0.1:11888"}
    )
    destroyed = []
    timeout_pending = lose_response

    def run_command(command):
        if isinstance(command, str):
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    command.replace("cd /home/agent\n", f"cd {shlex.quote(str(s.agent))}\n"),
                ],
                cwd=s.agent,
                capture_output=True,
                text=True,
                timeout=20,
            )
            return dict(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        return s.execute(session, command, GUEST_PATH)

    class LocalWorker:
        def prepare(self, spec):
            self.spec = spec
            return SimpleNamespace(execution_id="synthetic-exec")

        def start(self, target):
            self.result = run_command(self.spec.command)
            return target

        def inspect(self, execution_id):
            nonlocal timeout_pending
            if timeout_pending:
                timeout_pending = False
                raise TimeoutError("Worker response timed out after Git accepted the push")
            return SimpleNamespace(
                status=ExecutionStatusKind.STOPPED
                if self.result["exit_code"] == 0
                else ExecutionStatusKind.FAILED,
                metadata=self.result,
            )

        def get_log_snapshot(self, *a, **kw):
            return self.result["stdout"]

        def destroy(self, execution_id):
            destroyed.append(execution_id)

    monkeypatch.setattr(
        "evander.effects.providers.agent_exec.container_backend", lambda *_: LocalWorker()
    )

    def execute(caller, command, working_directory):
        original_request = base64.b64decode(command[-1])
        request = json.loads(original_request)
        request["remote_url"] = str(s.bare)
        request["guest_path"] = str(s.agent)
        command = [
            *command[:-1],
            base64.b64encode(
                json.dumps(request, ensure_ascii=original_request.isascii()).encode()
            ).decode(),
        ]
        provider = AgentExecEffectProvider()
        provider.initialize(
            AgentExecEffectDefinition(
                id="submit",
                type="effect:agent:exec",
                display_name="Submit test work",
                session_id=SESSION_ID,
                command=command,
                working_directory=working_directory,
                check=False,
                timeout_seconds=20,
            ),
            EffectRuntimeContext(
                resolved_config=config,
                session_lookup=SimpleNamespace(read_all_sessions=lambda: {SESSION_ID: session}),
                vm_executor=SimpleNamespace(vm_exec=lambda **kw: run_command(kw["command"])),
            ),
        )
        try:
            return provider.execute()
        except RuntimeError as exc:
            pytest.fail(str(exc))

    s.service.execute = execute
    if lose_response:
        assert submit(s, summary_markdown=summary_markdown)["code"] == "transport_failure"
        assert destroyed == ["synthetic-exec"]
    result = submit(s, summary_markdown=summary_markdown)
    assert result["success"], result
    assert _git_dir(s.bare, "rev-parse", result["agent_ref"]).stdout.strip() == s.sha
    assert destroyed == (
        ["synthetic-exec"] * (2 if lose_response else 1) if backend == "container_worker_vm" else []
    )
    if lose_response:
        assert result["changed"] is False


def call_mcp(server, params, session_id=SESSION_ID, transport="connected-transport"):
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                query_params={"session_id": session_id}, headers={"mcp-session-id": transport}
            )
        )
    )
    result = asyncio.run(server._tool_manager.call_tool("submit_git_changes", params, context=ctx))
    return result


def test_mcp_schema_caller_binding_and_audit(setup_submission, monkeypatch):
    import evander.gateway as gateway

    s = setup_submission
    monkeypatch.setattr(
        gateway, "build_admin_runtime", lambda _: SimpleNamespace(git_submission_admin=s.service)
    )
    server = gateway.create_server(s.runtime)
    tool = server._tool_manager.get_tool("submit_git_changes")
    assert set(tool.parameters["required"]) == {"expected_commit_sha", "summary_markdown"}
    properties = set(tool.parameters["properties"])
    assert {"checkout_id", "managed_repo_id"} <= properties
    assert (
        not {"session_id", "remote_url", "guest_path", "command", "destination_branch", "force"}
        & properties
    )
    result = call_mcp(server, {"expected_commit_sha": s.sha, "summary_markdown": SUMMARY})
    assert result["success"], result
    events = [json.loads(line) for line in s.config.audit_log_path.read_text().splitlines()]
    assert events[-1]["session_id"] == SESSION_ID
    assert events[-1]["tool_name"] == "submit_git_changes"
    assert events[-1]["decision"] == "allowed"
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        call_mcp(
            server,
            {"expected_commit_sha": s.sha, "summary_markdown": SUMMARY},
            session_id="another-session",
        )


def test_mcp_argument_policy_uses_resolved_checkout_selectors(setup_submission, monkeypatch):
    import evander.gateway as gateway

    s = setup_submission
    payload = s.session.model_dump()
    payload["tool_argument_policies"] = {
        "submit_git_changes": [
            {
                "id": "repos",
                "name": "Allowed repositories",
                "constraints": [
                    {
                        "id": "repo",
                        "path": "managed_repo_id",
                        "operator": "allowlist",
                        "values": [REPO_ID],
                    }
                ],
            }
        ],
    }
    s.runtime.session_repository.upsert_session(SessionManifest.model_validate(payload))
    monkeypatch.setattr(
        gateway, "build_admin_runtime", lambda _: SimpleNamespace(git_submission_admin=s.service)
    )
    result = call_mcp(
        gateway.create_server(s.runtime),
        {
            "expected_commit_sha": s.sha,
            "summary_markdown": SUMMARY,
        },
    )
    assert result["success"], result
    assert result["managed_repo_id"] == REPO_ID


@pytest.mark.parametrize("denial", ["grant", "arguments", "approval"])
def test_mcp_policy_stops_submission_before_execution(setup_submission, monkeypatch, denial):
    import evander.gateway as gateway
    from mcp.server.fastmcp.exceptions import ToolError

    s = setup_submission
    session = s.session.model_copy(deep=True)
    if denial == "grant":
        session.allowed_tools = []
    elif denial == "approval":
        session.approval_required_tools = [ToolName.SUBMIT_GIT_CHANGES]
    else:
        payload = session.model_dump()
        payload["tool_argument_policies"] = {
            "submit_git_changes": [
                {
                    "id": "repos",
                    "name": "Allowed repositories",
                    "constraints": [
                        {
                            "id": "repo",
                            "path": "managed_repo_id",
                            "operator": "allowlist",
                            "values": ["mr_another"],
                        }
                    ],
                }
            ]
        }
        session = SessionManifest.model_validate(payload)
    s.runtime.session_repository.upsert_session(session)
    monkeypatch.setattr(
        gateway, "build_admin_runtime", lambda _: SimpleNamespace(git_submission_admin=s.service)
    )
    server = gateway.create_server(s.runtime)
    params = dict(expected_commit_sha=s.sha, summary_markdown=SUMMARY, managed_repo_id=REPO_ID)
    try:
        result = call_mcp(server, params)
        assert not result.get("success")
    except ToolError:
        pass
    assert not s.calls


def test_atomic_push_coalesces_events_and_preserves_notes_only_updates(setup_submission, tmp_path):
    from test_handle_git_push_trigger_script import _configure_managed_repo, _canonical_config_path
    from evander.triggers.queue import SqliteTriggerEventQueue

    s = setup_submission
    _configure_managed_repo(s.bare, managed_repo_id=REPO_ID)
    receipt = submit(s)
    assert receipt["success"]
    notes = receipt["notes_ref"]
    notes_sha = _git_dir(s.bare, "rev-parse", notes).stdout.strip()
    queue_path = tmp_path / "events.db"
    config = tmp_path / "trigger-config.json"
    config.write_text(
        json.dumps(
            {
                "runtime_dir": str(s.config.runtime_dir),
                "orchestration_store_path": str(queue_path),
                "triggers": [
                    {
                        "id": "submit",
                        "display_name": "Submit",
                        "type": "trigger:git:push",
                        "managed_repo_id": REPO_ID,
                    }
                ],
            }
        )
    )

    def notify(updates):
        result = subprocess.run(
            [str(ROOT / "scripts/handle_git_push_trigger.sh"), str(s.bare)],
            input=updates,
            capture_output=True,
            text=True,
            timeout=20,
            env={
                **os.environ,
                "EVANDER_TRIGGER_ORCHESTRATOR_CONFIG": _canonical_config_path(config),
            },
        )
        assert result.returncode == 0, result.stderr

    zero = "0" * 40
    updates = f"{zero} {s.sha} {receipt['agent_ref']}\n{zero} {notes_sha} {notes}\n"
    notify(updates)
    events = SqliteTriggerEventQueue(queue_path).read_all()
    assert len(events) == 1
    assert next(iter(events.values())).payload["commit_hash"] == s.sha
    notify(updates)
    assert len(SqliteTriggerEventQueue(queue_path).read_all()) == 1
    result = s.service.submit(
        s.session, expected_commit_sha=s.sha, summary_markdown="Revised full summary"
    )
    assert result["success"]
    later = _git_dir(s.bare, "rev-parse", notes).stdout.strip()
    notify(f"{notes_sha} {later} {notes}\n")
    events = SqliteTriggerEventQueue(queue_path).read_all()
    assert len(events) == 2
    assert any(event.payload.get("note_ref_new_rev") == later for event in events.values())


def test_non_fast_forward_and_missing_prepared_base_never_force_publication(setup_submission):
    s = setup_submission
    receipt = submit(s)
    assert receipt["success"]
    tree = _git(s.agent, "rev-parse", "HEAD^{tree}").stdout.strip()
    advanced = _git_dir(
        s.bare,
        "-c",
        "user.name=Other writer",
        "-c",
        "user.email=other@example.test",
        "commit-tree",
        tree,
        "-p",
        s.sha,
        "-m",
        "Concurrent branch work",
    ).stdout.strip()
    _git_dir(s.bare, "update-ref", receipt["agent_ref"], advanced, s.sha)
    before = _git_dir(s.bare, "show-ref").stdout
    assert submit(s)["code"] == "non_fast_forward"
    assert _git_dir(s.bare, "show-ref").stdout == before
    cid = s.service.register_checkout(
        session_id=SESSION_ID,
        managed_repo_id=REPO_ID,
        guest_path=GUEST_PATH,
        prepared_base="f" * 40,
    )
    assert submit(s, checkout_id=cid)["code"] == "ancestry_blocked"
    assert _git_dir(s.bare, "show-ref").stdout == before


def test_same_repository_submissions_serialize_without_blocking_indefinitely(setup_submission):
    s = setup_submission
    entered, release = threading.Event(), threading.Event()

    def hold(*args):
        entered.set()
        assert release.wait(10)
        return s.execute(*args)

    s.service.execute = hold
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(submit, s)
        assert entered.wait(10)
        try:
            busy = submit(s)
            assert busy["code"] == "submission_busy" and busy["retryable"]
        finally:
            release.set()
        assert first.result(timeout=20)["success"]
    s.service.execute = s.execute
    assert submit(s)["changed"] is False


@pytest.mark.parametrize("competing_note", ["unrelated", "same_commit"])
def test_notes_lease_race_preserves_concurrent_summaries(
    setup_submission, tmp_path, monkeypatch, competing_note
):
    s = setup_submission
    first = submit(s)
    assert first["success"]
    notes = first["notes_ref"]
    old_notes = _git_dir(s.bare, "rev-parse", notes).stdout.strip()
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(s.source), str(other)], check=True, capture_output=True)
    _git(other, "config", "user.name", "Other writer")
    _git(other, "config", "user.email", "other@example.test")
    _git(other, "fetch", str(s.bare), f"{notes}:{notes}")
    base = _git(s.source, "rev-parse", "main").stdout.strip()
    target = base if competing_note == "unrelated" else s.sha
    _git(other, "notes", f"--ref={notes}", "add", "-f", "-m", "Concurrent summary", target)
    note_commit = _git(other, "rev-parse", notes).stdout.strip()
    _git_dir(s.bare, "fetch", str(other), f"{notes}:refs/evander/staging/test-notes")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "raced"
    real_git = shutil.which("git")
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'for arg in "$@"; do\nif [ "$arg" = --atomic ] && [ ! -e {shlex.quote(str(marker))} ]; then\n'
        f"touch {shlex.quote(str(marker))}\n"
        f"{shlex.quote(real_git)} --git-dir={shlex.quote(str(s.bare))} update-ref {notes} {note_commit} {old_notes}\n"
        "fi\ndone\n"
        f'exec {shlex.quote(real_git)} "$@"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    result = s.service.submit(
        s.session, expected_commit_sha=s.sha, summary_markdown="Updated complete summary"
    )
    assert marker.exists()
    assert (
        _git_dir(s.bare, "notes", f"--ref={notes}", "show", target).stdout.strip()
        == "Concurrent summary"
    )
    if competing_note == "unrelated":
        assert result["success"], result
        assert (
            _git_dir(s.bare, "notes", f"--ref={notes}", "show", s.sha).stdout.strip()
            == "Updated complete summary"
        )
    else:
        assert result["code"] == "summary_conflict", result
        assert _git_dir(s.bare, "rev-parse", notes).stdout.strip() == note_commit


def test_ancestry_is_checked_against_requested_sha_during_head_race(
    setup_submission, tmp_path, monkeypatch
):
    s = setup_submission
    _git(s.source, "checkout", "main")
    (s.source / "later-base.txt").write_text("Required base advanced\n")
    _git(s.source, "add", ".")
    _git(s.source, "commit", "-m", "Required base advanced")
    required_base = _git(s.source, "rev-parse", "HEAD").stdout.strip()
    _git_dir(s.bare, "fetch", str(s.source), "+refs/heads/main:refs/heads/main")
    _git(s.agent, "fetch", str(s.source), "main")
    tree = _git(s.agent, "rev-parse", "HEAD^{tree}").stdout.strip()
    contained = _git(
        s.agent,
        "commit-tree",
        tree,
        "-p",
        s.sha,
        "-p",
        required_base,
        "-m",
        "Temporary concurrent merge",
    ).stdout.strip()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "head-raced"
    real_git = shlex.quote(shutil.which("git"))
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'for arg in "$@"; do\nif [ "$arg" = merge-base ] && [ ! -e {shlex.quote(str(marker))} ]; then\n'
        f"touch {shlex.quote(str(marker))}\n"
        f"{real_git} update-ref HEAD {contained} {s.sha}\n"
        f'{real_git} "$@"\nresult=$?\n'
        f"{real_git} update-ref HEAD {s.sha} {contained}\n"
        "exit $result\nfi\ndone\n"
        f'exec {real_git} "$@"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    result = submit(s)
    assert marker.exists()
    assert result["code"] == "ancestry_blocked", result
    assert _git(s.agent, "rev-parse", "HEAD").stdout.strip() == s.sha
    assert not _git_dir(s.bare, "for-each-ref", "refs/evander/agents").stdout
