from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette

from evander.admin.web.privacy import privacy_routes
from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
    BUILTIN_GROUP_DEFINITIONS,
    OPERATOR_GROUP_ID,
    PERMISSION_CATALOG,
)
from evander.models import DirectoryPath
from evander.privacy.models import PrivacyConflict, PrivacyError, PrivacyUnsupported


class PrivacyStub:
    def __init__(self):
        self.calls = []
        self.failure = None

    def snapshot(self, scope):
        return {"directory_path": scope, "revision": "rev1", "coverage_complete": False}

    def mutate(self, action, scope, payload, *, expected_revision, actor):
        self.calls.append((action, scope, payload, expected_revision, actor))
        if self.failure:
            raise self.failure
        if expected_revision != "rev1":
            raise PrivacyConflict("Privacy revision changed")
        return {"status": "previewed"}

    def export_bytes(self, scope, job_id, *, actor):
        self.calls.append(("export", scope, job_id, actor))
        if self.failure:
            raise self.failure
        return b'{"records": [{"review": "private"}]}'

    def job_detail(self, scope, job_id, *, actor):
        self.calls.append(("job_detail", scope, job_id, actor))
        if self.failure:
            raise self.failure
        return {
            "id": job_id,
            "scope": scope,
            "revision": "rev1",
            "status": "preview",
            "records": [
                {"dataset_id": "voice", "record_id": "record1", "fingerprint": "fingerprint1"}
            ],
        }

    def record_detail(self, scope, kind, record_id, *, actor):
        self.calls.append(("record_detail", scope, kind, record_id, actor))
        if self.failure:
            raise self.failure
        return {"id": record_id, "scope": scope, "record_kind": kind, "revision": "rev1"}


class AuthStub:
    def __init__(self, permissions=(), *, mode="local_password", unlocked=True, scope="root/team"):
        self.permissions = set(permissions)
        self.config = SimpleNamespace(auth_mode=mode)
        self.unlocked = unlocked
        self.scope = scope
        self.authenticated = True

    def require(self, request):
        if not self.authenticated:
            raise PermissionError("Authentication required")
        return {"login": "operator@example.test"}

    def require_permission(self, request, permission, directory_path=None):
        if not self.can(request, permission, directory_path):
            raise PermissionError("Permission required")

    def can(self, request, permission, directory_path=None):
        return directory_path == self.scope and (
            permission in self.permissions or ADMIN_OWNER_PERMISSION in self.permissions
        )

    def privileged_for_request(self, request):
        return self.unlocked


def request(
    auth, *, path="/api/privacy?directory_path=root/team", method="GET", body=None, privacy=None
):
    privacy = privacy or PrivacyStub()
    service = SimpleNamespace(
        privacy_admin=privacy,
        teams_admin=SimpleNamespace(
            normalize_directory_reference=lambda scope: DirectoryPath(segments=scope).as_posix()
        ),
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=Starlette(routes=privacy_routes(service, auth))),
            base_url="http://test",
        ) as client:
            return await client.request(method, path, json=body)

    return asyncio.run(run())


def action_request(auth, action="retention.preview", body=None, privacy=None):
    return request(
        auth,
        path=f"/api/privacy/actions/{action}?directory_path=root/team",
        method="POST",
        body=body
        if body is not None
        else {"expected_revision": "rev1", "payload": {"dataset": "voice"}},
        privacy=privacy,
    )


def test_privacy_permissions_are_explicit_and_not_operator_defaults():
    privacy_permissions = {
        "privacy.read",
        "privacy.configure",
        "privacy.cases",
        "privacy.approve",
        "privacy.export",
    }
    assert privacy_permissions <= PERMISSION_CATALOG
    assert not privacy_permissions.intersection(
        BUILTIN_GROUP_DEFINITIONS[OPERATOR_GROUP_ID]["permissions"]
    )


def test_privacy_snapshot_is_scoped_and_not_cacheable():
    auth = AuthStub({"privacy.read"})
    response = request(auth)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["directory_path"] == "root/team"
    assert response.json()["permissions"] == ["privacy.read"]
    assert not response.json()["coverage_complete"]
    assert request(auth, path="/api/privacy?directory_path=root/other").status_code == 403


@pytest.mark.parametrize("kind,permission", [
    ("case", "privacy.cases"), ("incident", "privacy.cases"),
    ("followup", "privacy.cases"), ("hold", "privacy.approve"),
])
def test_evidence_detail_requires_scoped_privilege_and_records_authenticated_actor(kind, permission):
    path = f"/api/privacy/records/{kind}/record1?directory_path=root/team"
    privacy = PrivacyStub()
    assert request(AuthStub({"privacy.read"}), path=path, privacy=privacy).status_code == 403
    assert not privacy.calls
    auth = AuthStub({permission}, unlocked=False)
    response = request(auth, path=path, privacy=privacy)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert privacy.calls == [("record_detail", "root/team", kind, "record1", "operator@example.test")]
    assert request(auth, path=path.replace("root/team", "root/other")).status_code == 403
    assert request(AuthStub({permission}, mode="none"), path=path).status_code == 403
    root = path.replace("root/team", "root")
    assert request(AuthStub({permission}, scope="root"), path=root).status_code == 403
    assert request(AuthStub({ADMIN_OWNER_PERMISSION}, scope="root"), path=root).status_code == 200


def test_evidence_detail_does_not_expose_unexpected_errors_or_accept_unknown_kinds():
    privacy = PrivacyStub()
    privacy.failure = RuntimeError("confidential stored evidence")
    response = request(AuthStub({"privacy.cases"}), path="/api/privacy/records/case/one?directory_path=root/team", privacy=privacy)
    assert response.status_code == 500 and "confidential" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert request(AuthStub({"privacy.cases"}), path="/api/privacy/records/unknown/one").status_code == 404


@pytest.mark.parametrize("mode", ["none", "local_password"])
def test_privacy_rejects_anonymous_web_even_local_dev(mode):
    auth = AuthStub({ADMIN_OWNER_PERMISSION}, mode=mode)
    if mode != "none":
        auth.authenticated = False
    response = request(auth)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"


def test_installation_inventory_requires_installation_owner():
    auth = AuthStub({"privacy.read"}, scope="root")
    assert request(auth, path="/api/privacy?directory_path=root").status_code == 403
    auth.permissions.add(ADMIN_OWNER_PERMISSION)
    assert request(auth, path="/api/privacy?directory_path=root").status_code == 200


def test_mutation_requires_unlock_permission_and_revision():
    auth = AuthStub({"privacy.configure"}, unlocked=False)
    privacy = PrivacyStub()
    assert action_request(auth, privacy=privacy).status_code == 403
    assert not privacy.calls
    auth.unlocked = True
    assert action_request(auth, body={"payload": {}}).status_code == 428
    assert action_request(auth, body={"expected_revision": "old"}).status_code == 409
    assert action_request(AuthStub({"privacy.read"})).status_code == 403


def test_actor_is_authenticated_not_supplied_and_scope_is_from_query():
    auth = AuthStub({"privacy.configure"})
    privacy = PrivacyStub()
    response = action_request(
        auth,
        body={
            "expected_revision": "rev1",
            "payload": {"dataset": "voice"},
            "actor": "forged",
            "directory_path": "root/other",
        },
        privacy=privacy,
    )
    assert response.status_code == 200
    assert privacy.calls == [
        ("retention.preview", "root/team", {"dataset": "voice"}, "rev1", "operator@example.test")
    ]


def test_export_preview_requires_separate_permission():
    auth = AuthStub({"privacy.cases"})
    body = {"expected_revision": "rev1", "payload": {"case_id": "case1", "operation": "export"}}
    assert action_request(auth, "cases.preview", body).status_code == 403
    auth.permissions.add("privacy.export")
    assert action_request(auth, "cases.preview", body).status_code == 200


def test_approval_permission_is_not_configuration_permission():
    auth = AuthStub({"privacy.configure"})
    assert action_request(auth, "jobs.approve").status_code == 403
    auth.permissions.add("privacy.approve")
    assert action_request(auth, "jobs.approve").status_code == 200


def test_job_cancellation_requires_approval_unlock_and_revision():
    privacy = PrivacyStub()
    payload = {"expected_revision": "rev1", "payload": {"job_id": "job1"}}
    assert (
        action_request(
            AuthStub({"privacy.configure"}), "jobs.cancel", payload, privacy=privacy
        ).status_code
        == 403
    )
    assert (
        action_request(
            AuthStub({"privacy.approve"}, unlocked=False), "jobs.cancel", payload, privacy=privacy
        ).status_code
        == 403
    )
    assert not privacy.calls
    auth = AuthStub({"privacy.approve"})
    assert action_request(auth, "jobs.cancel", {"payload": {"job_id": "job1"}}).status_code == 428
    assert action_request(auth, "jobs.cancel", payload, privacy=privacy).status_code == 200
    assert privacy.calls == [
        ("jobs.cancel", "root/team", {"job_id": "job1"}, "rev1", "operator@example.test")
    ]


def test_bad_payload_and_unknown_or_unsupported_actions_fail_explicitly():
    auth = AuthStub({"privacy.configure"})
    assert action_request(auth, body=[]).status_code == 400
    assert action_request(auth, "unknown").status_code == 404
    assert (
        action_request(auth, body={"expected_revision": "rev1", "payload": []}).status_code == 400
    )
    privacy = PrivacyStub()
    privacy.failure = PrivacyUnsupported("Dataset requires manual follow-up")
    response = action_request(auth, privacy=privacy)
    assert response.status_code == 422
    assert response.json()["unsupported"] is True
    assert response.headers["cache-control"] == "no-store"


def test_unexpected_service_failure_does_not_expose_content():
    privacy = PrivacyStub()
    privacy.failure = RuntimeError("stored confidential transcript")
    response = action_request(AuthStub({"privacy.configure"}), privacy=privacy)
    assert response.status_code == 500
    assert "confidential" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_request_limit_is_enforced_before_service_mutation():
    privacy = PrivacyStub()
    response = action_request(
        AuthStub({"privacy.configure"}),
        body={
            "expected_revision": "rev1",
            "payload": {"reason": "x" * (256 * 1024)},
        },
        privacy=privacy,
    )
    assert response.status_code == 413
    assert not privacy.calls


@pytest.mark.parametrize("permissions", [[], ["privacy.cases"], ["privacy.export"]])
def test_export_download_requires_both_export_and_case_permissions(permissions):
    privacy = PrivacyStub()
    response = request(
        AuthStub(permissions),
        path="/api/privacy/exports/job1?directory_path=root/team",
        privacy=privacy,
    )
    assert response.status_code == 403
    assert not privacy.calls


def test_reviewer_download_is_scoped_private_attachment():
    privacy = PrivacyStub()
    auth = AuthStub({"privacy.export", "privacy.cases"})
    response = request(
        auth, path="/api/privacy/exports/job1?directory_path=root/team", privacy=privacy
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="evander-privacy-review.json"'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert privacy.calls == [("export", "root/team", "job1", "operator@example.test")]
    assert (
        request(auth, path="/api/privacy/exports/job1?directory_path=root/other").status_code == 403
    )


def test_expired_export_does_not_return_content():
    privacy = PrivacyStub()
    privacy.failure = PrivacyError("Export has expired")
    response = request(
        AuthStub({"privacy.export", "privacy.cases"}),
        path="/api/privacy/exports/job1?directory_path=root/team",
        privacy=privacy,
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Export has expired"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("permissions", [["privacy.approve"], ["privacy.export"]])
def test_export_release_requires_both_approval_and_export_permissions(permissions):
    privacy = PrivacyStub()
    response = action_request(
        AuthStub(permissions),
        "exports.release",
        {
            "expected_revision": "rev1",
            "payload": {"job_id": "job1", "review_reference": "review-1"},
        },
        privacy=privacy,
    )
    assert response.status_code == 403
    assert not privacy.calls


def test_export_release_delegates_independent_review_to_service():
    privacy = PrivacyStub()
    auth = AuthStub({"privacy.approve", "privacy.export"})
    response = action_request(
        auth,
        "exports.release",
        {
            "expected_revision": "rev1",
            "payload": {"job_id": "job1", "review_reference": "review-1"},
        },
        privacy=privacy,
    )
    assert response.status_code == 200
    assert privacy.calls[0][0] == "exports.release"
    assert privacy.calls[0][-1] == "operator@example.test"
    privacy.failure = PrivacyConflict("Independent reviewer required")
    assert action_request(auth, "exports.release", privacy=privacy).status_code == 409


def test_job_detail_requires_approver_permission_and_exact_scope():
    privacy = PrivacyStub()
    path = "/api/privacy/jobs/job1?directory_path=root/team"
    assert request(AuthStub({"privacy.read"}), path=path, privacy=privacy).status_code == 403
    assert not privacy.calls
    auth = AuthStub({"privacy.approve"})
    response = request(auth, path=path, privacy=privacy)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["revision"] == "rev1"
    assert response.json()["records"][0]["fingerprint"] == "fingerprint1"
    assert privacy.calls == [("job_detail", "root/team", "job1", "operator@example.test")]
    assert request(auth, path="/api/privacy/jobs/job1?directory_path=root/other").status_code == 403


def test_job_detail_requires_installation_owner_at_root_and_sanitizes_errors():
    path = "/api/privacy/jobs/job1?directory_path=root"
    auth = AuthStub({"privacy.approve"}, scope="root")
    assert request(auth, path=path).status_code == 403
    auth.permissions.add(ADMIN_OWNER_PERMISSION)
    privacy = PrivacyStub()
    privacy.failure = RuntimeError("secret source content")
    response = request(auth, path=path, privacy=privacy)
    assert response.status_code == 500
    assert "secret source" not in response.text
    assert response.headers["cache-control"] == "no-store"
