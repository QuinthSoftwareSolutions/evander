from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from evander.privacy import cli
from evander.privacy.models import PrivacyConflict


def test_status_delegates_scoped_metadata(monkeypatch):
    monkeypatch.setattr(
        cli,
        "service",
        lambda config: SimpleNamespace(
            snapshot=lambda scope: {"directory_path": scope, "coverage_complete": False}
        ),
    )
    result = CliRunner().invoke(cli.app, ["status", "--directory-path", "root/team"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"directory_path": "root/team", "coverage_complete": False}


def test_action_uses_explicit_revision_and_os_actor(monkeypatch, tmp_path):
    calls = []

    def mutate(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "previewed"}

    monkeypatch.setattr(cli, "service", lambda config: SimpleNamespace(mutate=mutate))
    monkeypatch.setattr(cli.os, "getuid", lambda: 123)
    monkeypatch.setattr(cli.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="real-user"))
    monkeypatch.setenv("USER", "forged")
    payload = tmp_path / "payload.json"
    payload.write_text('{"dataset":"voice"}', encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "action",
            "retention.preview",
            "--payload-file",
            str(payload),
            "--expected-revision",
            "rev1",
            "--directory-path",
            "root/team",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [
        (
            ("retention.preview", "root/team", {"dataset": "voice"}),
            {
                "expected_revision": "rev1",
                "actor": "host:123:real-user",
            },
        )
    ]


def test_conflict_is_not_silently_retried(monkeypatch, tmp_path):
    calls = []

    def mutate(*args, **kwargs):
        calls.append(args)
        raise PrivacyConflict("Refresh and review current revision")

    monkeypatch.setattr(cli, "service", lambda config: SimpleNamespace(mutate=mutate))
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "action",
            "jobs.run",
            "--payload-file",
            str(payload),
            "--expected-revision",
            "old",
        ],
    )
    assert result.exit_code != 0
    assert len(calls) == 1


def test_maintenance_is_bounded_and_uses_trusted_actor(monkeypatch):
    calls = []

    def maintenance(**kwargs):
        calls.append(kwargs)
        return {"examined": 0}

    monkeypatch.setattr(cli, "service", lambda config: SimpleNamespace(maintenance=maintenance))
    monkeypatch.setattr(cli, "host_actor", lambda: "host:1:operator")
    result = CliRunner().invoke(cli.app, ["maintenance", "--limit", "3"])
    assert result.exit_code == 0
    assert calls == [{"limit": 3, "actor": "host:1:operator"}]
    assert CliRunner().invoke(cli.app, ["maintenance", "--limit", "0"]).exit_code != 0


def test_export_review_is_private_scoped_and_never_overwrites(monkeypatch, tmp_path):
    calls = []

    def export_bytes(scope, job_id, *, actor):
        calls.append((scope, job_id, actor))
        return b'{"records":[]}'

    monkeypatch.setattr(cli, "service", lambda config: SimpleNamespace(export_bytes=export_bytes))
    monkeypatch.setattr(cli, "host_actor", lambda: "host:123:operator")
    destination = tmp_path / "review.json"
    args = [
        "export-review",
        "job1",
        "--destination",
        str(destination),
        "--directory-path",
        "root/team",
    ]
    first = CliRunner().invoke(cli.app, args)
    assert first.exit_code == 0, first.stdout
    assert calls == [("root/team", "job1", "host:123:operator")]
    assert destination.read_bytes() == b'{"records":[]}'
    assert destination.stat().st_mode & 0o777 == 0o600
    assert json.loads(first.stdout)["subject_delivery"] is False
    destination.write_text("preserve existing export", encoding="utf-8")
    assert CliRunner().invoke(cli.app, args).exit_code != 0
    assert destination.read_text(encoding="utf-8") == "preserve existing export"


def test_job_inspection_returns_exact_scoped_selection(monkeypatch):
    calls = []

    def detail(scope, job_id, *, actor):
        calls.append((scope, job_id, actor))
        return {"scope": scope, "id": job_id, "revision": "rev1", "records": []}

    monkeypatch.setattr(cli, "service", lambda config: SimpleNamespace(job_detail=detail))
    monkeypatch.setattr(cli, "host_actor", lambda: "host:123:operator")
    result = CliRunner().invoke(cli.app, ["job", "job1", "--directory-path", "root/team"])
    assert result.exit_code == 0, result.stdout
    assert calls == [("root/team", "job1", "host:123:operator")]
    assert json.loads(result.stdout)["revision"] == "rev1"


@pytest.mark.parametrize("kind", ["case", "hold", "incident", "followup"])
def test_evidence_inspection_uses_exact_scope_and_host_identity(monkeypatch, kind):
    calls = []

    def detail(scope, record_kind, record_id, *, actor):
        calls.append((scope, record_kind, record_id, actor))
        return {"id": record_id, "record_kind": record_kind, "evidence_reference": "saved/1"}

    monkeypatch.setattr(cli, "service", lambda config: SimpleNamespace(record_detail=detail))
    monkeypatch.setattr(cli, "host_actor", lambda: "host:123:operator")
    result = CliRunner().invoke(cli.app, ["record", kind, "record1", "--directory-path", "root/team"])
    assert result.exit_code == 0, result.stdout
    assert calls == [("root/team", kind, "record1", "host:123:operator")]
    assert json.loads(result.stdout)["evidence_reference"] == "saved/1"
