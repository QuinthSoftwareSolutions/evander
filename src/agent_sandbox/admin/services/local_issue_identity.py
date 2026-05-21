from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any

from agent_sandbox.models import LocalIssueRecord

_PROXY_IDENTIFIER_PATTERN = re.compile(
    r"^(?:(?:proxy:)?(azure_devops|ado)[:/])([0-9]+)$",
    flags=re.IGNORECASE,
)


def _remote_reference_fields(issue: LocalIssueRecord) -> tuple[str, str]:
    remote = issue.remote_reference
    if remote is not None:
        provider = str(remote.provider or "").strip().casefold()
        issue_id = str(remote.work_item_id or "").strip()
    else:
        metadata_remote = issue.metadata.get("remote")
        if not isinstance(metadata_remote, Mapping):
            return "", ""
        provider = str(metadata_remote.get("provider") or "").strip().casefold()
        issue_id = str(metadata_remote.get("issue_id") or "").strip()
    if provider == "ado":
        provider = "azure_devops"
    return provider, issue_id


@dataclass(frozen=True)
class LocalIssueIdentityResolver:
    local_issue_store: Any
    read_issue_number_from_yaml: Callable[[Path], str]

    def _read_all_issues(self) -> dict[str, LocalIssueRecord]:
        return dict(self.local_issue_store.read_all())

    def resolve_key(self, identifier: str) -> str | None:
        normalized = str(identifier or "").strip()
        if not normalized:
            return None
        issues = self._read_all_issues()
        if normalized in issues:
            return normalized
        matches = [
            issue_id
            for issue_id, issue in issues.items()
            if str(issue.issue_number or "").strip() == normalized
        ]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        sorted_matches = sorted(matches)
        raise ValueError(
            "Ambiguous local issue identifier: "
            f"{normalized}. Matches: {', '.join(sorted_matches)}. "
            "Use a scoped issue identifier."
        )

    def find_by_remote_identifier(self, issue_identifier: str) -> LocalIssueRecord | None:
        normalized = str(issue_identifier or "").strip()
        if not normalized:
            return None
        prefixed_match = _PROXY_IDENTIFIER_PATTERN.match(normalized)
        if not prefixed_match:
            return None
        provider = "azure_devops"
        remote_issue_id = prefixed_match.group(2)
        matches: list[LocalIssueRecord] = []
        for issue in self._read_all_issues().values():
            issue_provider, issue_remote_id = _remote_reference_fields(issue)
            if issue_provider != provider:
                continue
            if issue_remote_id == remote_issue_id:
                matches.append(issue)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        sorted_matches = sorted(issue.issue_id for issue in matches)
        raise ValueError(
            "Ambiguous proxy issue identifier: "
            f"{normalized}. Matches: {', '.join(sorted_matches)}. "
            "Use a canonical proxy issue identifier."
        )

    def resolve_record(self, issue_identifier: str) -> LocalIssueRecord:
        candidate = Path(issue_identifier)
        if candidate.is_file():
            candidate_path = candidate.resolve()
            for issue in self._read_all_issues().values():
                if issue.yaml_path and Path(issue.yaml_path).resolve() == candidate_path:
                    return issue
            issue_number = self.read_issue_number_from_yaml(candidate)
            raise ValueError(f"Unknown local issue: {issue_number}")

        issue_key = self.resolve_key(issue_identifier)
        if issue_key is not None:
            issue = self.local_issue_store.get(issue_key)
            if issue is not None:
                return issue

        by_remote_identifier = self.find_by_remote_identifier(issue_identifier)
        if by_remote_identifier is not None:
            return by_remote_identifier

        raise ValueError(f"Unknown local issue identifier: {issue_identifier}")
