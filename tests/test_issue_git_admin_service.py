from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest
import yaml

import agent_sandbox.admin.services.issue_git_service as issue_git_module
from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.services.issue_git_service import (
    IssueGitAdminDependencies,
    IssueGitAdminService,
)
from agent_sandbox.models import LocalIssueRecord, RemoteIssueReference, SessionManifest, ToolName


class FakeStore:
    def __init__(self, initial: dict[str, object] | None = None) -> None:
        self.items: dict[str, object] = dict(initial or {})

    def read_all(self) -> dict[str, object]:
        return dict(self.items)

    def get(self, key: str) -> object | None:
        return self.items.get(key)

    def put(self, key: str, value: object) -> None:
        self.items[key] = value

    def get_session(self, key: str) -> object | None:
        return self.get(key)

    def upsert_session(self, value: object) -> None:
        session_id = str(getattr(value, "session_id", "") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        self.put(session_id, value)

    def delete(self, key: str) -> None:
        self.items.pop(key, None)


class FakeIssueGitFacade:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _record(self, name: str, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append((name, args, kwargs))
        return {"name": name, "args": args, "kwargs": kwargs}

    def pr_create(self, **kwargs: object) -> dict[str, object]:
        return self._record("pr_create", **kwargs)

    def git_pr_note_query(self, **kwargs: object) -> dict[str, object]:
        return self._record("git_pr_note_query", **kwargs)

    def session_update_metadata(self, **kwargs: object) -> dict[str, object]:
        return self._record("session_update_metadata", **kwargs)

    def create_issue(self, **kwargs: object) -> dict[str, object]:
        return self._record("create_issue", **kwargs)

    def update_issue(self, *args: object, **kwargs: object) -> dict[str, object]:
        return self._record("update_issue", *args, **kwargs)

    def delete_local_issue(self, issue_identifier: str) -> dict[str, object]:
        return self._record("delete_local_issue", issue_identifier)


def _build_issue_admin(tmp_path: Path) -> tuple[SimpleNamespace, FakeStore]:
    store = FakeStore()
    runtime = SimpleNamespace(
        session_repository=FakeStore(),
        approvals=FakeStore(),
        audit=SimpleNamespace(log_decision=lambda **kwargs: None),
    )

    def resolve_local_issue(issue_identifier: str) -> LocalIssueRecord:
        issue = store.get(issue_identifier)
        if issue is None:
            raise ValueError(f"Unknown local issue identifier: {issue_identifier}")
        return issue  # type: ignore[return-value]

    def write_yaml(issue: LocalIssueRecord, *, updated_at: datetime | None = None) -> None:
        yaml_path = Path(issue.yaml_path)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "issue_number": issue.issue_number,
            "title": issue.title,
            "body": issue.body,
            "state": issue.state,
            "source": issue.source,
        }
        if updated_at is not None:
            payload["updated_at"] = updated_at.isoformat()
        yaml_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    admin = SimpleNamespace(
        project_dir=tmp_path,
        runtime=runtime,
        local_issue_store=store,
        local_issue_yaml_dir=tmp_path / "issues",
        _local_issue_creation_lock=lambda: nullcontext(),
        _normalize_directory_path=lambda directory_path: directory_path or "root",
        _next_local_issue_number=lambda: "1",
        _canonical_local_issue_id=lambda issue_number, directory_path: (
            f"{directory_path}::local::{issue_number}"
        ),
        _resolve_local_issue_record=resolve_local_issue,
        _resolve_local_issue_key=lambda identifier: identifier,
        _proxy_target_for_issue=lambda issue: None,
        _refresh_proxy_cache_from_remote=lambda issue, remote_payload: issue,
        _write_local_issue_yaml=write_yaml,
        _direct_owned_entity_ids=lambda directory_path, kind: set(),
        issue_service=SimpleNamespace(
            read_remote_issue=lambda **kwargs: {},
            post_remote_comment=lambda **kwargs: {"comment_added": True},
            update_remote_issue=lambda **kwargs: {"updated": True},
        ),
        config=SimpleNamespace(audit_log_path=tmp_path / "audit.jsonl"),
        local_issue_yaml_dir_path=tmp_path / "issues",
    )
    admin.local_issue_yaml_dir.mkdir(parents=True, exist_ok=True)
    return admin, store


def _issue_git_dependencies(admin: SimpleNamespace) -> IssueGitAdminDependencies:
    return IssueGitAdminDependencies(
        project_dir=admin.project_dir,
        runtime=admin.runtime,
        config=admin.config,
        local_issue_store=admin.local_issue_store,
        local_issue_yaml_dir=admin.local_issue_yaml_dir,
        issue_service=admin.issue_service,
        normalize_directory_path=lambda directory_path: admin._normalize_directory_path(
            directory_path
        ),
        local_issue_creation_lock=lambda: admin._local_issue_creation_lock(),
        next_local_issue_number=lambda: admin._next_local_issue_number(),
        canonical_local_issue_id=lambda issue_number, directory_path: (
            admin._canonical_local_issue_id(issue_number, directory_path)
        ),
        resolve_local_issue_record=lambda issue_identifier: admin._resolve_local_issue_record(
            issue_identifier
        ),
        resolve_local_issue_key=lambda issue_identifier: admin._resolve_local_issue_key(
            issue_identifier
        ),
        proxy_target_for_issue=lambda issue: admin._proxy_target_for_issue(issue),
        refresh_proxy_cache_from_remote=lambda issue, remote_payload: (
            admin._refresh_proxy_cache_from_remote(issue, remote_payload)
        ),
        write_local_issue_yaml=lambda issue, **kwargs: admin._write_local_issue_yaml(
            issue, **kwargs
        ),
        direct_owned_entity_ids=lambda directory_path, **kwargs: admin._direct_owned_entity_ids(
            directory_path, **kwargs
        ),
    )


def test_issue_git_admin_service_pr_create_invokes_script_and_parses_result(
    tmp_path: Path, monkeypatch: object
) -> None:
    admin, _store = _build_issue_admin(tmp_path)
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    captured: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout='AGENT_PR_RESULT {"pr_number": 42}\n',
            stderr="prepared\n",
        )

    monkeypatch.setattr(issue_git_module._admin_service, "_run_subprocess", fake_run)

    result = service.pr_create(
        host_repo_path="/repo", local_branch="feature", title="Add PR", draft=True
    )

    assert captured == [
        [
            str(tmp_path / "scripts" / "open_agent_pr.sh"),
            "--create-pr",
            "--draft",
            "--title",
            "Add PR",
            "/repo",
            "feature",
        ]
    ]
    assert result["success"] is True
    assert result["pr_number"] == 42


def test_issue_git_admin_service_session_update_metadata_updates_store(tmp_path: Path) -> None:
    admin, _store = _build_issue_admin(tmp_path)
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-1",
        model="gpt-5",
        directory_path="root",
        allowed_tools=[ToolName.CREATE_LOCAL_ISSUE],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"nested": {"one": 1}, "keep": True},
    )
    admin.runtime.session_repository.upsert_session(manifest)

    result = service.session_update_metadata(
        target="session-1", metadata={"nested": {"two": 2}, "new": 3}
    )

    updated = admin.runtime.session_repository.get_session("session-1")
    assert isinstance(updated, SessionManifest)
    assert updated.metadata == {"nested": {"one": 1, "two": 2}, "keep": True, "new": 3}
    assert result["session_id"] == "session-1"
    assert result["metadata"] == updated.metadata

    context = service.session_context_query(target="session-1")
    assert context["session_id"] == "session-1"
    assert context["vm_name"] == "vm-1"
    assert context["metadata"] == updated.metadata


def test_issue_git_admin_service_create_update_and_delete_local_issue(
    tmp_path: Path, monkeypatch: object
) -> None:
    admin, store = _build_issue_admin(tmp_path)
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    monkeypatch.setattr(
        issue_git_module._admin_service, "_dominfo_field", lambda *args, **kwargs: None
    )

    created = service.create_issue(title="Fix dashboard", body="Build it")
    created_path = Path(created.yaml_path)
    assert created.issue_id == "root::local::1"
    assert created.title == "Fix dashboard"
    assert created_path.exists()
    assert store.get(created.issue_id) == created

    updated = service.update_local_issue(
        created.issue_id,
        title="Fix dashboard v2",
        body="Improve it",
        state="Approved",
    )
    assert updated.title == "Fix dashboard v2"
    assert updated.state == "Approved"
    assert "Fix dashboard v2" in created_path.read_text(encoding="utf-8")

    delete_result = service.delete_local_issue(created.issue_id)
    assert delete_result.success is True
    assert store.get(created.issue_id) is None
    assert not created_path.exists()


def test_issue_git_admin_service_list_actionable_local_issues_filters_proxy_records(
    tmp_path: Path,
) -> None:
    admin, store = _build_issue_admin(tmp_path)
    service = IssueGitAdminService(_issue_git_dependencies(admin))

    local_issue = service.create_issue(title="Local", body="body")
    proxy_issue = local_issue.model_copy(
        update={
            "issue_id": "root::local::2",
            "issue_number": "2",
            "title": "Proxy",
            "source_type": "remote_proxy",
            "state": "Committed",
        }
    )
    store.put(proxy_issue.issue_id, proxy_issue)
    admin._proxy_target_for_issue = lambda issue: (
        ("azure_devops", "42") if issue.issue_id == proxy_issue.issue_id else None
    )

    actionable = service.list_actionable_local_issues(directory_path="root")

    assert [issue.issue_id for issue in actionable] == [local_issue.issue_id]


def test_issue_git_admin_service_get_issue_passes_remote_reference_project(
    tmp_path: Path,
) -> None:
    admin, store = _build_issue_admin(tmp_path)
    remote_calls: list[dict[str, object]] = []
    admin.issue_service = SimpleNamespace(
        read_remote_issue=lambda **kwargs: remote_calls.append(kwargs)
        or {
            "title": "Remote",
            "description": "Body",
            "state": "In Progress",
        },
        post_remote_comment=lambda **kwargs: {"comment_added": True},
        update_remote_issue=lambda **kwargs: {"updated": True},
    )
    admin._proxy_target_for_issue = lambda issue: ("azure_devops", "15446")
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    issue = LocalIssueRecord(
        issue_id="root::azure_devops::15446",
        issue_number="15446",
        title="Issue",
        body="Body",
        yaml_path=str(tmp_path / "issues" / "15446.yaml"),
        directory_path="root/example/truman/auto-dev",
        source_type="remote_proxy",
        remote_reference=RemoteIssueReference(
            provider="azure_devops",
            organization="https://dev.azure.com/exampleindustry",
            project="Example Industry APP",
            work_item_id="15446",
        ),
    )
    store.put(issue.issue_id, issue)

    service.get_issue(issue.issue_id)

    assert remote_calls == [
        {
            "provider": "azure_devops",
            "remote_issue_id": "15446",
            "directory_path": "root/example/truman/auto-dev",
            "project": "Example Industry APP",
            "include_comments": True,
        }
    ]


def test_issue_git_admin_service_update_issue_passes_remote_reference_project(
    tmp_path: Path,
) -> None:
    admin, store = _build_issue_admin(tmp_path)
    update_calls: list[dict[str, object]] = []
    read_calls: list[dict[str, object]] = []
    admin.issue_service = SimpleNamespace(
        read_remote_issue=lambda **kwargs: read_calls.append(kwargs)
        or {
            "title": "Remote",
            "description": "Body",
            "state": "In Progress",
        },
        post_remote_comment=lambda **kwargs: {"comment_added": True},
        update_remote_issue=lambda **kwargs: update_calls.append(kwargs) or {"updated": True},
    )
    admin._proxy_target_for_issue = lambda issue: ("azure_devops", "15446")
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    issue = LocalIssueRecord(
        issue_id="root::azure_devops::15446",
        issue_number="15446",
        title="Issue",
        body="Body",
        yaml_path=str(tmp_path / "issues" / "15446.yaml"),
        directory_path="root/example/truman/auto-dev",
        source_type="remote_proxy",
        remote_reference=RemoteIssueReference(
            provider="azure_devops",
            organization="https://dev.azure.com/exampleindustry",
            project="Example Industry APP",
            work_item_id="15446",
        ),
    )
    store.put(issue.issue_id, issue)

    service.update_issue(issue.issue_id, state="In Progress")

    assert update_calls == [
        {
            "provider": "azure_devops",
            "remote_issue_id": "15446",
            "directory_path": "root/example/truman/auto-dev",
            "project": "Example Industry APP",
            "state": "In Progress",
            "fields": None,
        }
    ]
    assert read_calls == [
        {
            "provider": "azure_devops",
            "remote_issue_id": "15446",
            "directory_path": "root/example/truman/auto-dev",
            "project": "Example Industry APP",
            "include_comments": True,
        }
    ]


def test_admin_service_delegates_issue_git_methods() -> None:
    service = AdminService.__new__(AdminService)
    fake = FakeIssueGitFacade()
    service.issue_git_admin = fake

    assert (
        service.pr_create(host_repo_path="/repo", local_branch="feature", title="Add PR")["name"]
        == "pr_create"
    )
    assert (
        service.git_pr_note_query(host_repo_path="/repo", local_branch="review/session-1/topic")[
            "name"
        ]
        == "git_pr_note_query"
    )
    assert (
        service.session_update_metadata(target="session-1", metadata={"foo": "bar"})["name"]
        == "session_update_metadata"
    )
    assert service.create_issue(title="New issue")["name"] == "create_issue"
    assert service.update_issue("issue-1", state="In Progress")["name"] == "update_issue"
    assert service.delete_local_issue("issue-7")["name"] == "delete_local_issue"

    assert fake.calls == [
        (
            "pr_create",
            (),
            {
                "host_repo_path": "/repo",
                "local_branch": "feature",
                "origin_branch": None,
                "canonical_origin_url": None,
                "provider": None,
                "project": None,
                "base_branch": None,
                "session_id": None,
                "work_item_id": None,
                "title": "Add PR",
                "body": None,
                "body_file": None,
                "body_artifact_ids": None,
                "draft": None,
                "fill": True,
            },
        ),
        (
            "git_pr_note_query",
            (),
            {
                "host_repo_path": "/repo",
                "local_branch": "review/session-1/topic",
                "remote_name": None,
                "notes_ref": None,
            },
        ),
        ("session_update_metadata", (), {"target": "session-1", "metadata": {"foo": "bar"}}),
        (
            "create_issue",
            (),
            {
                "title": "New issue",
                "body": "",
                "repo_source": None,
                "yaml_path": None,
                "directory_path": "root",
                "metadata": None,
            },
        ),
        (
            "update_issue",
            ("issue-1",),
            {"state": "In Progress", "fields": None, "sync_proxy_cache": True},
        ),
        ("delete_local_issue", ("issue-7",), {}),
    ]


def test_issue_git_admin_service_update_issue_metadata_adds_property_and_audits(
    tmp_path: Path,
) -> None:
    admin, store = _build_issue_admin(tmp_path)
    audit_calls: list[dict[str, object]] = []
    admin.runtime.audit = SimpleNamespace(log_decision=lambda **kwargs: audit_calls.append(kwargs))
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    issue = LocalIssueRecord(
        issue_id="root::local::1",
        issue_number="1",
        title="Issue",
        body="Body",
        yaml_path=str(tmp_path / "issues" / "1.yaml"),
        metadata={"keep": True},
    )
    store.put(issue.issue_id, issue)

    result = service.update_issue_metadata(
        issue_id="root::local::1", patch={"plan.status": "committed"}
    )

    updated = store.get("root::local::1")
    assert isinstance(updated, LocalIssueRecord)
    assert updated.metadata == {"keep": True, "plan": {"status": "committed"}}
    assert result["metadata_paths"] == ["plan.status"]
    assert result["patch_kind"] == "dot_path"
    assert audit_calls[0]["event_type"] == "issue_metadata_updated"
    assert audit_calls[0]["args"]["paths"] == ["plan.status"]


def test_issue_git_admin_service_update_issue_metadata_updates_nested_and_preserves_unrelated(
    tmp_path: Path,
) -> None:
    admin, store = _build_issue_admin(tmp_path)
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    issue = LocalIssueRecord(
        issue_id="root::local::1",
        issue_number="1",
        title="Issue",
        body="Body",
        yaml_path=str(tmp_path / "issues" / "1.yaml"),
        metadata={"plan": {"status": "draft", "version": 2}, "keep": {"nested": True}},
    )
    store.put(issue.issue_id, issue)

    service.update_issue_metadata(
        issue_id="root::local::1", patch={"plan.status": "committed"}
    )

    updated = store.get("root::local::1")
    assert isinstance(updated, LocalIssueRecord)
    assert updated.metadata == {
        "plan": {"status": "committed", "version": 2},
        "keep": {"nested": True},
    }


def test_issue_git_admin_service_update_issue_metadata_merge_patch_removes_null_property(
    tmp_path: Path,
) -> None:
    admin, store = _build_issue_admin(tmp_path)
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    issue = LocalIssueRecord(
        issue_id="root::local::1",
        issue_number="1",
        title="Issue",
        body="Body",
        yaml_path=str(tmp_path / "issues" / "1.yaml"),
        metadata={"plan": {"status": "draft", "obsolete": True}, "keep": True},
    )
    store.put(issue.issue_id, issue)

    result = service.update_issue_metadata(
        issue_id="root::local::1",
        merge_patch={"plan": {"obsolete": None, "status": "committed"}},
    )

    updated = store.get("root::local::1")
    assert isinstance(updated, LocalIssueRecord)
    assert updated.metadata == {"plan": {"status": "committed"}, "keep": True}
    assert result["metadata_paths"] == ["plan", "plan.obsolete", "plan.status"]
    assert result["patch_kind"] == "json_merge_patch"


def test_issue_git_admin_service_update_issue_metadata_rejects_concurrency_conflict(
    tmp_path: Path,
) -> None:
    admin, store = _build_issue_admin(tmp_path)
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    issue = LocalIssueRecord(
        issue_id="root::local::1",
        issue_number="1",
        title="Issue",
        body="Body",
        yaml_path=str(tmp_path / "issues" / "1.yaml"),
        metadata={"plan": {"status": "draft"}},
    )
    store.put(issue.issue_id, issue)

    with pytest.raises(RuntimeError, match="concurrency conflict"):
        service.update_issue_metadata(
            issue_id="root::local::1",
            patch={"plan.status": "committed"},
            expected_updated_at="2000-01-01T00:00:00+00:00",
        )

    updated = store.get("root::local::1")
    assert isinstance(updated, LocalIssueRecord)
    assert updated.metadata == {"plan": {"status": "draft"}}


def test_issue_git_admin_service_update_issue_metadata_works_for_ado_proxy_without_remote_update(
    tmp_path: Path,
) -> None:
    admin, store = _build_issue_admin(tmp_path)
    remote_calls: list[dict[str, object]] = []
    admin.issue_service = SimpleNamespace(
        read_remote_issue=lambda **kwargs: {},
        post_remote_comment=lambda **kwargs: {"comment_added": True},
        update_remote_issue=lambda **kwargs: remote_calls.append(kwargs) or {"updated": True},
    )
    admin._proxy_target_for_issue = lambda issue: ("azure_devops", "14084")
    service = IssueGitAdminService(_issue_git_dependencies(admin))
    issue = LocalIssueRecord(
        issue_id="root::azure_devops::14084",
        issue_number="14084",
        title="Issue",
        body="Body",
        yaml_path=str(tmp_path / "issues" / "14084.yaml"),
        source_type="remote_proxy",
        metadata={
            "remote": {"provider": "azure_devops", "issue_id": "14084"},
            "proxy_cache": {"state": "Active"},
        },
    )
    store.put(issue.issue_id, issue)

    result = service.update_issue_metadata(
        issue_id="root::azure_devops::14084", patch={"workflow.status": "ready"}
    )

    updated = store.get("root::azure_devops::14084")
    assert isinstance(updated, LocalIssueRecord)
    assert updated.metadata["remote"] == {"provider": "azure_devops", "issue_id": "14084"}
    assert updated.metadata["proxy_cache"] == {"state": "Active"}
    assert updated.metadata["workflow"] == {"status": "ready"}
    assert result["source_type"] == "proxy"
    assert remote_calls == []
