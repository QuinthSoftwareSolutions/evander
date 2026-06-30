from __future__ import annotations

from pathlib import Path

import pytest

from agent_sandbox.admin.services import session_helpers as admin_service
from agent_sandbox.admin.services.local_issue_identity import LocalIssueIdentityResolver
from agent_sandbox.models import LocalIssueRecord, RemoteIssueReference


class FakeStore:
    def __init__(self, items: dict[str, LocalIssueRecord] | None = None) -> None:
        self.items = dict(items or {})

    def read_all(self) -> dict[str, LocalIssueRecord]:
        return dict(self.items)

    def get(self, key: str) -> LocalIssueRecord | None:
        return self.items.get(key)


def _issue(
    *,
    issue_id: str,
    issue_number: str,
    yaml_path: Path,
    remote_reference: RemoteIssueReference | None = None,
    metadata: dict[str, object] | None = None,
) -> LocalIssueRecord:
    return LocalIssueRecord(
        issue_id=issue_id,
        issue_number=issue_number,
        title=f"Issue {issue_number}",
        body="Body",
        yaml_path=str(yaml_path),
        remote_reference=remote_reference,
        metadata=dict(metadata or {}),
    )


def test_resolve_key_handles_canonical_ids_and_unique_issue_numbers(tmp_path: Path) -> None:
    store = FakeStore(
        {
            "root::local::1": _issue(
                issue_id="root::local::1",
                issue_number="1",
                yaml_path=tmp_path / "root-1.yaml",
            ),
        }
    )
    resolver = LocalIssueIdentityResolver(
        local_issue_store=store,
        read_issue_number_from_yaml=admin_service._read_issue_number_from_yaml,
    )

    assert resolver.resolve_key("root::local::1") == "root::local::1"
    assert resolver.resolve_key("1") == "root::local::1"


def test_resolve_key_lists_sorted_canonical_ids_for_ambiguous_issue_numbers(
    tmp_path: Path,
) -> None:
    store = FakeStore(
        {
            "root::local::7": _issue(
                issue_id="root::local::7",
                issue_number="7",
                yaml_path=tmp_path / "root-7.yaml",
            ),
            "team::local::7": _issue(
                issue_id="team::local::7",
                issue_number="7",
                yaml_path=tmp_path / "team-7.yaml",
            ),
        }
    )
    resolver = LocalIssueIdentityResolver(
        local_issue_store=store,
        read_issue_number_from_yaml=admin_service._read_issue_number_from_yaml,
    )

    with pytest.raises(
        ValueError,
        match=(
            r"Ambiguous local issue identifier: 7\. Matches: root::local::7, "
            r"team::local::7\. Use a scoped issue identifier\."
        ),
    ):
        resolver.resolve_key("7")


def test_resolve_record_matches_yaml_path_and_reports_missing_yaml_issue_number(
    tmp_path: Path,
) -> None:
    issue_path = tmp_path / "issues" / "issue-11.yaml"
    issue_path.parent.mkdir(parents=True, exist_ok=True)
    issue_path.write_text("issue_number: 11\ntitle: YAML issue\nbody: Body\n", encoding="utf-8")
    store = FakeStore(
        {
            "root::local::11": _issue(
                issue_id="root::local::11",
                issue_number="11",
                yaml_path=issue_path,
            )
        }
    )
    resolver = LocalIssueIdentityResolver(
        local_issue_store=store,
        read_issue_number_from_yaml=admin_service._read_issue_number_from_yaml,
    )

    assert resolver.resolve_record(str(issue_path)) == store.items["root::local::11"]

    missing_path = tmp_path / "missing-issue.yaml"
    missing_path.write_text("issue_number: 99\ntitle: Missing\nbody: Body\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"Unknown local issue: 99"):
        resolver.resolve_record(str(missing_path))


def test_resolve_record_supports_proxy_identifiers_from_remote_reference_and_metadata(
    tmp_path: Path,
) -> None:
    remote_issue = _issue(
        issue_id="root::local::42",
        issue_number="1",
        yaml_path=tmp_path / "remote.yaml",
        remote_reference=RemoteIssueReference(
            provider="azure_devops",
            organization="https://dev.azure.com/example",
            project="sample",
            work_item_id="42",
        ),
    )
    legacy_issue = _issue(
        issue_id="root::local::77",
        issue_number="2",
        yaml_path=tmp_path / "legacy.yaml",
        metadata={"remote": {"provider": "ado", "issue_id": "77"}},
    )
    store = FakeStore(
        {
            remote_issue.issue_id: remote_issue,
            legacy_issue.issue_id: legacy_issue,
        }
    )
    resolver = LocalIssueIdentityResolver(
        local_issue_store=store,
        read_issue_number_from_yaml=admin_service._read_issue_number_from_yaml,
    )

    assert resolver.resolve_record("proxy:ado/42") == remote_issue
    assert resolver.resolve_record("ado:77") == legacy_issue


def test_find_by_remote_identifier_lists_sorted_canonical_ids_for_ambiguous_matches(
    tmp_path: Path,
) -> None:
    store = FakeStore(
        {
            "root::local::42": _issue(
                issue_id="root::local::42",
                issue_number="1",
                yaml_path=tmp_path / "root-42.yaml",
                remote_reference=RemoteIssueReference(
                    provider="azure_devops",
                    organization="https://dev.azure.com/example",
                    project="sample-a",
                    work_item_id="42",
                ),
            ),
            "team::local::42": _issue(
                issue_id="team::local::42",
                issue_number="2",
                yaml_path=tmp_path / "team-42.yaml",
                remote_reference=RemoteIssueReference(
                    provider="azure_devops",
                    organization="https://dev.azure.com/example",
                    project="sample-b",
                    work_item_id="42",
                ),
            ),
        }
    )
    resolver = LocalIssueIdentityResolver(
        local_issue_store=store,
        read_issue_number_from_yaml=admin_service._read_issue_number_from_yaml,
    )

    with pytest.raises(
        ValueError,
        match=(
            r"Ambiguous proxy issue identifier: proxy:ado/42\. Matches: "
            r"root::local::42, team::local::42\. Use a canonical proxy issue identifier\."
        ),
    ):
        resolver.find_by_remote_identifier("proxy:ado/42")


def test_resolve_record_rejects_unknown_identifier(tmp_path: Path) -> None:
    store = FakeStore(
        {
            "root::local::1": _issue(
                issue_id="root::local::1",
                issue_number="1",
                yaml_path=tmp_path / "root-1.yaml",
            )
        }
    )
    resolver = LocalIssueIdentityResolver(
        local_issue_store=store,
        read_issue_number_from_yaml=admin_service._read_issue_number_from_yaml,
    )

    with pytest.raises(ValueError, match=r"Unknown local issue identifier: missing"):
        resolver.resolve_record("missing")
