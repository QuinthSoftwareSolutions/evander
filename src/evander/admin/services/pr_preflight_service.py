from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Sequence

from pydantic import ValidationError

from evander.pr_merge_preflight import (
    GitMergePreflightEngine,
    MergePreflightCheck,
    MergePreflightMode,
    MergePreflightParticipant,
    MergePreflightPullRequest,
    MergePreflightReport,
    MergePreflightStatus,
)
from evander.pull_request_automation import build_pull_request_provider
from evander.provider_runtime.contracts import ProviderRuntimeBase
from evander.services import normalize_pr_automation_provider


@dataclass(frozen=True)
class PrIntegrationPreflightAdminDependencies:
    runtime: Any
    managed_git_admin: Any


class PrIntegrationPreflightAdminService:
    NODE_TYPE = "admin:pr-integration-preflight"

    def __init__(self, dependencies: PrIntegrationPreflightAdminDependencies) -> None:
        self._deps = dependencies
        self._engine = GitMergePreflightEngine()

    def run_pr_integration_preflight(
        self,
        *,
        provider: str,
        repo: str,
        pr_ids: Sequence[str],
        mode: MergePreflightMode,
        directory_path: str,
        managed_repo_id: str,
    ) -> MergePreflightReport:
        provider_name = normalize_pr_automation_provider(provider)
        if provider_name != "azure_devops":
            raise ValueError(f"{self.NODE_TYPE} provider {provider!r} is not supported")

        normalized_repo = self._normalize_required_text(repo, field_name="repo")
        normalized_directory_path = self._normalize_directory_path(directory_path)
        normalized_managed_repo_id = self._normalize_required_text(
            managed_repo_id,
            field_name="managed_repo_id",
        )
        normalized_pr_ids = self._normalize_pr_ids(pr_ids)
        self._validate_pr_count(mode=mode, pr_ids=normalized_pr_ids)

        try:
            managed_record = self._deps.managed_git_admin.get_repository(
                normalized_managed_repo_id,
                normalized_directory_path,
            )
            self._deps.managed_git_admin.resolve_internal_repository(
                normalized_managed_repo_id,
                normalized_directory_path,
            )
            self._deps.managed_git_admin.refresh_repository(normalized_managed_repo_id)
        except Exception as exc:
            return self._error_report(
                status="repo_error",
                mode=mode,
                repo_path=normalized_managed_repo_id,
                pr_ids=normalized_pr_ids,
                explanation=self._error_text(exc),
            )

        try:
            provider_client = self._build_provider(
                provider=provider_name,
                directory_path=normalized_directory_path,
            )
        except Exception as exc:
            return self._error_report(
                status="provider_error",
                mode=mode,
                repo_path=normalized_managed_repo_id,
                pr_ids=normalized_pr_ids,
                explanation=self._error_text(exc),
            )

        pull_requests: list[MergePreflightPullRequest] = []
        participants: list[MergePreflightParticipant] = []
        for index, pr_id in enumerate(normalized_pr_ids):
            try:
                response = provider_client.get_pull_request(
                    repo=normalized_repo,
                    pr_id=pr_id,
                    include_commits=False,
                )
            except Exception as exc:
                explanation = self._error_text(exc) or f"Failed to fetch pull request {pr_id}"
                participants.extend(
                    self._placeholder_participants(
                        pr_ids=normalized_pr_ids[index:],
                        status="provider_error",
                        explanation=explanation,
                    )
                )
                return self._error_report(
                    status="provider_error",
                    mode=mode,
                    repo_path=normalized_managed_repo_id,
                    pr_ids=normalized_pr_ids,
                    explanation=explanation,
                    participants=participants,
                )

            pr = MergePreflightPullRequest(
                pr_id=self._normalize_required_text(response.pr_id or pr_id, field_name="pr_id"),
                source_branch=self._normalize_optional_text(response.source_branch),
                target_branch=self._normalize_optional_text(response.target_branch),
                source_commit=self._normalize_optional_text(response.source_commit),
                target_commit=self._normalize_optional_text(response.target_commit),
                url=self._normalize_optional_text(response.url),
                status=self._normalize_optional_text(response.status),
            )
            pull_requests.append(pr)
            participants.append(
                MergePreflightParticipant(
                    pr_id=pr.pr_id,
                    source_branch=pr.source_branch,
                    target_branch=pr.target_branch,
                    source_commit=pr.source_commit,
                    target_commit=pr.target_commit,
                    url=pr.url,
                    status="clean",
                )
            )

        target_commit = next((pr.target_commit for pr in pull_requests if pr.target_commit), None)
        target_branch = next((pr.target_branch for pr in pull_requests if pr.target_branch), None)
        source_ref = self._managed_branch_ref(
            target_branch or str(managed_record.get("default_branch") or "main")
        )
        try:
            worktree_args: dict[str, Any] = {
                "operation_id": f"pr-preflight-{os.urandom(12).hex()}",
                "purpose": "pr-integration-preflight",
            }
            if target_commit:
                worktree_args["commit_sha"] = target_commit
            else:
                worktree_args["source_ref"] = source_ref
            with self._deps.managed_git_admin.temporary_worktree(
                normalized_managed_repo_id,
                **worktree_args,
            ) as repo_path:
                report = self._engine.run(
                    repo_path,
                    mode=mode,
                    pull_requests=pull_requests,
                )
                report = self._sanitize_managed_worktree_report(report, repo_path)
        except ValidationError as exc:
            raise ValueError(self._validation_error_text(exc)) from exc
        except Exception as exc:
            return self._error_report(
                status="repo_error",
                mode=mode,
                repo_path=normalized_managed_repo_id,
                pr_ids=normalized_pr_ids,
                explanation=self._error_text(exc),
                participants=participants,
            )

        return report.model_copy(update={"repo_path": normalized_managed_repo_id})

    @staticmethod
    def _sanitize_managed_worktree_report(
        report: MergePreflightReport,
        repo_path: Any,
    ) -> MergePreflightReport:
        checkout_path = str(repo_path)
        operation_path = str(repo_path.parent)

        def scrub(value: Any) -> Any:
            if isinstance(value, str):
                return value.replace(checkout_path, "<managed-worktree>").replace(
                    operation_path, "<managed-worktree>"
                )
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, tuple):
                return tuple(scrub(item) for item in value)
            if isinstance(value, dict):
                return {key: scrub(item) for key, item in value.items()}
            return value

        return MergePreflightReport.model_validate(scrub(report.model_dump(mode="python")))

    def _build_provider(
        self,
        *,
        provider: str,
        directory_path: str,
    ) -> Any:
        context = ProviderRuntimeBase(
            provider_config_resolver=self._deps.runtime.provider_config,
            env=dict(os.environ),
        )
        _, _, provider_client = build_pull_request_provider(
            context=context,
            provider=provider,
            directory_path=directory_path,
            # Admin preflight credentials may only be sent to the directory's
            # trusted provider endpoint, never to request-supplied targets.
            ado_organization=None,
            project=None,
            node_type=self.NODE_TYPE,
        )
        return provider_client

    @staticmethod
    def _managed_branch_ref(value: str) -> str:
        branch = str(value or "").strip()
        if branch.startswith("refs/heads/"):
            return branch
        return f"refs/heads/{branch}"

    def _error_report(
        self,
        *,
        status: MergePreflightStatus,
        mode: MergePreflightMode,
        repo_path: str,
        pr_ids: list[str],
        explanation: str,
        participants: list[MergePreflightParticipant] | None = None,
    ) -> MergePreflightReport:
        resolved_participants = participants or self._placeholder_participants(
            pr_ids=pr_ids, status=status, explanation=explanation
        )
        check = self._error_check(
            index=1,
            kind=mode,
            pr_ids=pr_ids,
            status=status,
            explanation=explanation,
        )
        return MergePreflightReport(
            repo_path=repo_path,
            mode=mode,
            status=status,
            participants=resolved_participants,
            checks=[check],
            warnings=[],
            errors=[explanation],
            summary_text=self._engine._summary_text(
                mode=mode,
                status=status,
                participants=resolved_participants,
                target_commit=None,
                current_target_commit=None,
                warnings=[],
                errors=[explanation],
                checks=[check],
            ),
            generated_at=None,
        )

    def _error_check(
        self,
        *,
        index: int,
        kind: MergePreflightMode,
        pr_ids: list[str],
        status: MergePreflightStatus,
        explanation: str,
    ) -> MergePreflightCheck:
        return MergePreflightCheck(
            index=index,
            kind=kind,
            pr_ids=list(pr_ids),
            source_commits=[],
            left_commit=None,
            right_commit=None,
            target_commit=None,
            current_target_commit=None,
            tree_oid=None,
            conflicted_paths=[],
            status=status,
            explanation=explanation,
        )

    def _placeholder_participants(
        self,
        *,
        pr_ids: Sequence[str],
        status: MergePreflightStatus,
        explanation: str,
    ) -> list[MergePreflightParticipant]:
        return [
            MergePreflightParticipant(pr_id=pr_id, status=status, explanation=explanation)
            for pr_id in pr_ids
        ]

    def _validate_pr_count(self, *, mode: MergePreflightMode, pr_ids: list[str]) -> None:
        count = len(pr_ids)
        if mode == "single" and count != 1:
            raise ValueError("single mode requires exactly one PR id")
        if mode in {"pairwise", "ordered"} and count < 2:
            raise ValueError(f"{mode} mode requires at least two PR ids")

    def _normalize_pr_ids(self, pr_ids: Sequence[str]) -> list[str]:
        normalized: list[str] = []
        for pr_id in pr_ids:
            text = self._normalize_required_text(pr_id, field_name="pr_id")
            normalized.append(text)
        if not normalized:
            raise ValueError(f"{self.NODE_TYPE} requires at least one PR id")
        return normalized

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        return self._normalize_optional_text(directory_path) or "root"

    def _normalize_required_text(self, value: object, *, field_name: str) -> str:
        text = self._normalize_optional_text(value)
        if not text:
            raise ValueError(f"{self.NODE_TYPE} requires {field_name}")
        return text

    @staticmethod
    def _normalize_optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _error_text(exc: Exception) -> str:
        return str(exc).strip() or exc.__class__.__name__

    @staticmethod
    def _validation_error_text(exc: ValidationError) -> str:
        messages = [str(error.get("msg", "")).strip() for error in exc.errors()]
        messages = [message for message in messages if message]
        return "; ".join(messages) or "Invalid merge preflight request"

    @staticmethod
    def _directory_config_error_text(result: Any) -> str | None:
        for field_name in ("errors", "validation_diagnostics"):
            entries = getattr(result, field_name, None)
            if not entries:
                continue
            messages: list[str] = []
            for entry in entries:
                message = getattr(entry, "message", None)
                if message is None:
                    message = getattr(entry, "detail", None)
                if message is None:
                    message = str(entry)
                text = str(message).strip()
                if text:
                    messages.append(text)
            if messages:
                return "; ".join(messages)
        return None


__all__ = [
    "PrIntegrationPreflightAdminDependencies",
    "PrIntegrationPreflightAdminService",
]
