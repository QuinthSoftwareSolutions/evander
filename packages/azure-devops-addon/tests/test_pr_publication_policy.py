from __future__ import annotations

import base64
import sqlite3
from types import SimpleNamespace

import pytest

from evander_azure_devops_addon.user_connections import AdoUserConnectionService
from evander.admin.services.managed_git_service import AzurePrCreationUncertain, AzurePrRequestError
from evander.admin.services.pr_publication_policy import PrPublicationPolicyError
from evander.effects.providers.pr_create import PrCreateEffectProvider
from evander.effects.providers.schemas import PrCreateEffectDefinition
from test_ado_user_connections import _context, _identity, _save
from test_effect_pr_create_provider import _build_context
from test_managed_git_service import _service


def publisher(tmp_path, monkeypatch, *, identity="alice", reason=None, save_pat=True):
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "bot-secret")
    monkeypatch.setattr(AdoUserConnectionService, "validate_pat", staticmethod(_identity))
    service = _service(tmp_path)
    connections = service.ado_user_connections
    if save_pat:
        _save(connections)
    context = _context(connections, identity=identity, fallback_reason=reason)
    record = {
        "provider": "azure_devops",
        "default_branch": "main",
        "directory_path": "root",
        "managed_repo_id": "repo",
        "canonical_repository_url": "https://dev.azure.com/org/project/_git/repo",
    }
    monkeypatch.setattr(service, "_require_scoped_record", lambda *a, **k: record)
    state = SimpleNamespace(calls=[], existing=[], post_error=None, reject=None, created_by="alice")
    state.work_items = {}
    state.link_writes = []

    def request(url, *, auth, payload=None, method=None, **kwargs):
        caller = (
            base64.b64decode(auth.removeprefix("Basic ")).decode().split(":", 1)[1]
            if auth.startswith("Basic ")
            else auth
        )
        method = method or ("POST" if payload is not None else "GET")
        state.calls.append((method, caller))
        if state.reject and state.reject[0] == method:
            raise AzurePrRequestError(state.reject[1])
        if "/_apis/wit/workitems/" in url:
            item_id = url.split("/workitems/", 1)[1].split("?", 1)[0]
            item = state.work_items.setdefault(item_id, {"id": int(item_id), "relations": []})
            if method == "PATCH":
                state.link_writes.append(item_id)
                item["relations"].extend(operation["value"] for operation in payload)
            return item
        if method == "GET":
            return {"value": list(state.existing)}
        if method == "PATCH":
            return dict(state.existing[0])
        pr = {
            "pullRequestId": 42,
            "lastMergeSourceCommit": {"commitId": "1" * 40},
            "createdBy": {"id": state.created_by, "displayName": state.created_by},
            "repository": {"id": "repo-id", "project": {"id": "project-id"}},
        }
        if state.post_error != "unknown":
            state.existing.append(pr)
        if state.post_error:
            raise AzurePrRequestError()
        return pr

    monkeypatch.setattr(service, "_azure_request_json", request)
    monkeypatch.setattr(
        service,
        "push_branch",
        lambda *a, **k: state.calls.append(("PUSH", None)) or {"success": True},
    )
    monkeypatch.setattr(
        service, "_persist_pr_tracking", lambda *a, **k: state.calls.append(("TRACK", None))
    )
    arguments = dict(
        managed_repo_id="repo",
        source_ref="refs/heads/release",
        expected_commit_sha="1" * 40,
        destination_branch="feature",
        directory_path="root/app",
        publication_context_id=context,
        publication_policy="require_personal",
        title="Release",
        body="Reviewed release",
    )
    return service, state, arguments


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("context", "publication_context_missing"),
        ("assignee", "missing_work_item_attribution"),
        ("mixed", "mixed_work_item_assignees"),
        ("pat", "personal_pat_missing"),
        ("scope", "personal_pat_missing"),
    ],
)
def test_required_identity_failures_stop_before_external_requests(
    tmp_path, monkeypatch, problem, reason
):
    service, state, args = publisher(
        tmp_path,
        monkeypatch,
        identity=None if problem in {"assignee", "mixed"} else "alice",
        reason=reason if problem in {"assignee", "mixed"} else None,
        save_pat=problem not in {"pat", "scope"},
    )
    if problem == "context":
        args["publication_context_id"] = None
    if problem == "scope":
        _save(service.ado_user_connections, directory="root/other")
    with pytest.raises(PrPublicationPolicyError, match=reason):
        service.pr_create(**args)
    assert state.calls == []


@pytest.mark.parametrize("policy", [None, "", "require_persnoal"])
def test_invalid_policy_cannot_downgrade_to_bot(tmp_path, monkeypatch, policy):
    service, state, args = publisher(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        service.pr_create(**{**args, "publication_policy": policy})
    assert state.calls == []


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("stage", ["GET", "POST"])
def test_rejected_personal_credentials_never_use_bot(tmp_path, monkeypatch, status, stage):
    service, state, args = publisher(tmp_path, monkeypatch)
    state.reject = (stage, status)
    with pytest.raises(PrPublicationPolicyError, match="personal_pat_(rejected|permission_denied)"):
        service.pr_create(**args)
    assert all(caller != "bot-secret" for _, caller in state.calls)
    assert not any(method == "TRACK" for method, _ in state.calls)
    if stage == "GET":
        assert not any(method == "PUSH" for method, _ in state.calls)


@pytest.mark.parametrize("creator", ["bot", None])
def test_existing_wrong_or_unknown_creator_stops_before_push_or_patch(
    tmp_path, monkeypatch, creator
):
    service, state, args = publisher(tmp_path, monkeypatch)
    state.existing = [{"pullRequestId": 9294, "createdBy": {"id": creator}}]
    with pytest.raises(PrPublicationPolicyError, match="existing_pr_creator_mismatch") as error:
        service.pr_create(**args)
    assert error.value.pr_id == "9294"
    assert state.calls == [("GET", "alice-secret")]


def test_required_personal_creation_and_reuse_are_audited(tmp_path, monkeypatch):
    service, state, args = publisher(tmp_path, monkeypatch)
    first = service.pr_create(**args)
    again = service.pr_create(**args)
    assert first["created"] is True and again["created"] is False
    assert first["actual_creator"]["id"] == first["intended_creator_id"] == "alice"
    assert first["credential_source"] == "personal_pat"
    assert first["fallback_reason"] is None
    assert sum(method == "POST" for method, _ in state.calls) == 1
    assert state.calls[:3] == [("GET", "alice-secret"), ("PUSH", None), ("GET", "alice-secret")]
    with sqlite3.connect(service.ado_user_connections.root / "connections.db") as db:
        assert (
            db.execute(
                "SELECT actual_creator_id,credential_source,fallback_reason FROM publication_results"
            ).fetchall()
            == [("alice", "personal_pat", None)] * 2
        )


def test_required_personal_may_fall_back_from_oauth_to_same_account_pat(tmp_path, monkeypatch):
    service, state, args = publisher(tmp_path, monkeypatch)
    resolve = service.ado_user_connections.resolve_publication
    request = service._azure_request_json

    def resolve_with_oauth(*a, **kwargs):
        context, auth = resolve(*a, **kwargs)
        if "personal_oauth" not in kwargs.get("excluded_methods", ()):
            return {**context, "credential_source": "personal_oauth"}, "Bearer expired-oauth"
        return context, auth

    def reject_oauth(*a, **kwargs):
        if kwargs["auth"].startswith("Bearer"):
            state.calls.append(("GET", kwargs["auth"]))
            raise AzurePrRequestError(401)
        return request(*a, **kwargs)

    monkeypatch.setattr(service.ado_user_connections, "resolve_publication", resolve_with_oauth)
    monkeypatch.setattr(service, "_azure_request_json", reject_oauth)
    result = service.pr_create(**args)
    assert result["actual_creator"]["id"] == "alice"
    assert result["credential_source"] == "personal_pat"
    assert "personal_oauth_rejected" in result["fallback_reason"]
    assert not any(caller == "bot-secret" for _, caller in state.calls)


@pytest.mark.parametrize("outcome", ["timeout", "unknown"])
def test_uncertain_required_publication_never_switches_to_bot_or_duplicates(
    tmp_path, monkeypatch, outcome
):
    service, state, args = publisher(tmp_path, monkeypatch)
    state.post_error = outcome
    if outcome == "unknown":
        for _ in range(2):
            with pytest.raises(AzurePrCreationUncertain):
                service.pr_create(**args)
    else:
        result = service.pr_create(**args)
        assert result["actual_creator"]["id"] == "alice"
        assert result["credential_source"] == "personal_pat"
    assert sum(method == "POST" for method, _ in state.calls) == 1
    assert all(caller != "bot-secret" for _, caller in state.calls)


def test_connection_disconnected_after_preflight_stops_before_pr_post(tmp_path, monkeypatch):
    service, state, args = publisher(tmp_path, monkeypatch)

    def push(*a, **kwargs):
        service.ado_user_connections.remove(
            login="alice", organization="org", directory_path="root"
        )
        return {"success": True}

    monkeypatch.setattr(service, "push_branch", push)
    with pytest.raises(PrPublicationPolicyError, match="personal_pat_missing"):
        service.pr_create(**args)
    assert state.calls == [("GET", "alice-secret")]


def test_wrong_creator_returned_by_create_is_not_reported_as_success(tmp_path, monkeypatch):
    service, state, args = publisher(tmp_path, monkeypatch)
    state.created_by = "bot"
    with pytest.raises(PrPublicationPolicyError, match="created_pr_creator_mismatch"):
        service.pr_create(**args)
    with pytest.raises(PrPublicationPolicyError, match="existing_pr_creator_mismatch"):
        service.pr_create(**args)
    assert sum(method == "POST" for method, _ in state.calls) == 1
    assert not any(method in {"PATCH", "TRACK"} for method, _ in state.calls)


def test_legacy_bot_publication_records_missing_context_reason(tmp_path, monkeypatch):
    service, state, args = publisher(tmp_path, monkeypatch)
    state.created_by = "bot"
    args.pop("publication_policy")
    args.pop("publication_context_id")
    result = service.pr_create(**args)
    assert result["credential_source"] == "bot"
    assert result["fallback_reason"] == "publication_context_missing"
    with sqlite3.connect(service.ado_user_connections.root / "connections.db") as db:
        assert db.execute(
            "SELECT context_id,credential_source,fallback_reason FROM publication_results"
        ).fetchone() == (None, "bot", "publication_context_missing")


def test_allowed_reuse_of_bot_pr_records_creator_mismatch(tmp_path, monkeypatch):
    service, state, args = publisher(tmp_path, monkeypatch)
    state.existing = [
        {
            "pullRequestId": 9294,
            "createdBy": {"id": "bot"},
            "lastMergeSourceCommit": {"commitId": "1" * 40},
        }
    ]
    result = service.pr_create(**{**args, "publication_policy": "allow_bot_fallback"})
    assert result["actual_creator"]["id"] == "bot"
    assert result["fallback_reason"] == "existing_pr_creator_mismatch"


def test_effect_schema_requires_context_and_runtime_rejects_resolved_null(tmp_path, monkeypatch):
    service, state, args = publisher(tmp_path, monkeypatch)
    config = {k: v for k, v in args.items() if k != "directory_path"}
    config.update(id="pr", display_name="Publish", type="effect:pr:create")
    config.pop("publication_context_id")
    with pytest.raises(ValueError, match="needs publication_context_id"):
        PrCreateEffectDefinition.model_validate(config)
    config["publication_context_id"] = "${{release.publication_context_id ?? null}}"
    provider = PrCreateEffectProvider()
    provider.initialize(
        PrCreateEffectDefinition.model_validate(config), _build_context(tmp_path, service)
    )
    with pytest.raises(PrPublicationPolicyError, match="publication_context_missing"):
        provider.execute(resolved_config={"publication_context_id": None})
    assert state.calls == []
