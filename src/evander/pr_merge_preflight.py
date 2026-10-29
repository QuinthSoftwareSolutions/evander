from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MergePreflightMode = Literal["single", "pairwise", "ordered"]
MergePreflightStatus = Literal[
    "clean",
    "conflict",
    "missing_ref",
    "stale_target_branch",
    "repo_error",
    "provider_error",
]

_OID_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)
_FIXED_COMMIT_DATE = "1970-01-01T00:00:00+00:00"


class MergePreflightModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def _strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class MergePreflightPullRequest(MergePreflightModel):
    pr_id: str = Field(min_length=1)
    source_branch: str | None = None
    target_branch: str | None = None
    source_commit: str | None = None
    target_commit: str | None = None
    url: str | None = None
    status: str | None = None


class MergePreflightRequest(MergePreflightModel):
    repo_path: str = Field(min_length=1)
    mode: MergePreflightMode
    pull_requests: list[MergePreflightPullRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_pr_count(self) -> "MergePreflightRequest":
        count = len(self.pull_requests)
        if self.mode == "single" and count != 1:
            raise ValueError("single mode requires exactly one PR id")
        if self.mode in {"pairwise", "ordered"} and count < 2:
            raise ValueError(f"{self.mode} mode requires at least two PR ids")
        return self


class MergePreflightParticipant(MergePreflightModel):
    pr_id: str
    source_branch: str | None = None
    target_branch: str | None = None
    source_commit: str | None = None
    target_commit: str | None = None
    current_target_commit: str | None = None
    url: str | None = None
    status: MergePreflightStatus = "clean"
    explanation: str | None = None


class MergePreflightCheck(MergePreflightModel):
    index: int = Field(ge=1)
    kind: MergePreflightMode
    pr_ids: list[str] = Field(default_factory=list)
    source_commits: list[str] = Field(default_factory=list)
    left_commit: str | None = None
    right_commit: str | None = None
    target_commit: str | None = None
    current_target_commit: str | None = None
    tree_oid: str | None = None
    conflicted_paths: list[str] = Field(default_factory=list)
    status: MergePreflightStatus
    explanation: str


class MergePreflightReport(MergePreflightModel):
    repo_path: str
    mode: MergePreflightMode
    status: MergePreflightStatus
    target_branch: str | None = None
    target_commit: str | None = None
    current_target_commit: str | None = None
    participants: list[MergePreflightParticipant] = Field(default_factory=list)
    checks: list[MergePreflightCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    summary_text: str | None = None
    generated_at: datetime | None = None


class GitMergePreflightEngine:
    def run(
        self,
        repo_path: str | Path,
        *,
        mode: MergePreflightMode,
        pull_requests: Sequence[MergePreflightPullRequest | dict[str, Any]],
    ) -> MergePreflightReport:
        request = MergePreflightRequest.model_validate(
            {
                "repo_path": str(repo_path),
                "mode": mode,
                "pull_requests": list(pull_requests),
            }
        )
        return self.evaluate(request)

    def evaluate(self, request: MergePreflightRequest) -> MergePreflightReport:
        repo_path = Path(request.repo_path)
        participants = [
            MergePreflightParticipant(
                pr_id=pr.pr_id,
                source_branch=pr.source_branch,
                target_branch=pr.target_branch,
                source_commit=pr.source_commit,
                target_commit=pr.target_commit,
                current_target_commit=None,
                url=pr.url,
                status="clean",
                explanation=None,
            )
            for pr in request.pull_requests
        ]

        repo_status = self._git_status(repo_path)
        if repo_status.returncode != 0:
            error = self._git_text(repo_status) or f"{repo_path} is not a git repository"
            report = MergePreflightReport(
                repo_path=str(repo_path),
                mode=request.mode,
                status="repo_error",
                participants=participants,
                checks=[],
                warnings=[],
                errors=[error],
                summary_text=self._summary_text(
                    mode=request.mode,
                    status="repo_error",
                    participants=participants,
                    target_commit=None,
                    current_target_commit=None,
                    warnings=[],
                    errors=[error],
                ),
                generated_at=None,
            )
            return report

        target_branch = self._first_text(
            pr.target_branch for pr in request.pull_requests if pr.target_branch
        )
        expected_target_commit, current_target_commit = self._resolve_target_context(
            repo_path,
            target_branch=target_branch,
            target_commit=self._first_text(
                pr.target_commit for pr in request.pull_requests if pr.target_commit
            ),
        )

        warnings: list[str] = []
        if (
            expected_target_commit is not None
            and current_target_commit is not None
            and expected_target_commit != current_target_commit
        ):
            warnings.append(
                "stale_target_branch: ADO target commit "
                f"{expected_target_commit} differs from current target commit "
                f"{current_target_commit}"
            )

        resolved_requests: list[dict[str, Any]] = []
        for participant, pr in zip(participants, request.pull_requests, strict=True):
            source_commit = self._resolve_commit(
                repo_path,
                sha=pr.source_commit,
                ref_name=pr.source_branch,
            )
            target_commit = self._resolve_commit(
                repo_path,
                sha=pr.target_commit,
                ref_name=pr.target_branch,
            )
            participant.source_commit = source_commit
            participant.target_commit = target_commit
            participant.current_target_commit = current_target_commit
            resolved_requests.append(
                {
                    "participant": participant,
                    "request": pr,
                    "source_commit": source_commit,
                    "target_commit": target_commit,
                }
            )

        missing_ref_error = self._first_missing_ref_error(
            resolved_requests,
            mode=request.mode,
        )
        if missing_ref_error is not None:
            report = MergePreflightReport(
                repo_path=str(repo_path),
                mode=request.mode,
                status="missing_ref",
                target_branch=target_branch,
                target_commit=expected_target_commit,
                current_target_commit=current_target_commit,
                participants=participants,
                checks=[missing_ref_error],
                warnings=warnings,
                errors=[missing_ref_error.explanation],
                summary_text=self._summary_text(
                    mode=request.mode,
                    status="missing_ref",
                    participants=participants,
                    target_commit=expected_target_commit,
                    current_target_commit=current_target_commit,
                    warnings=warnings,
                    errors=[missing_ref_error.explanation],
                    checks=[missing_ref_error],
                ),
                generated_at=None,
            )
            self._apply_stale_warning(participants, warnings)
            return report

        if request.mode == "single":
            checks = self._run_single(
                repo_path,
                requests=resolved_requests,
                target_commit=expected_target_commit or current_target_commit,
                current_target_commit=current_target_commit,
            )
        elif request.mode == "pairwise":
            checks = self._run_pairwise(
                repo_path,
                requests=resolved_requests,
                target_commit=expected_target_commit,
                current_target_commit=current_target_commit,
            )
        else:
            checks = self._run_ordered(
                repo_path,
                requests=resolved_requests,
                target_commit=expected_target_commit or current_target_commit,
                current_target_commit=current_target_commit,
            )

        status = self._overall_status(checks=checks, warnings=warnings)
        self._apply_participant_outcomes(
            participants, checks=checks, warnings=warnings, status=status
        )
        summary_text = self._summary_text(
            mode=request.mode,
            status=status,
            participants=participants,
            target_commit=expected_target_commit,
            current_target_commit=current_target_commit,
            warnings=warnings,
            errors=[
                check.explanation
                for check in checks
                if check.status not in {"clean", "stale_target_branch"}
            ],
            checks=checks,
        )
        return MergePreflightReport(
            repo_path=str(repo_path),
            mode=request.mode,
            status=status,
            target_branch=target_branch,
            target_commit=expected_target_commit,
            current_target_commit=current_target_commit,
            participants=participants,
            checks=checks,
            warnings=warnings,
            errors=[
                check.explanation
                for check in checks
                if check.status not in {"clean", "stale_target_branch"}
            ],
            summary_text=summary_text,
            generated_at=None,
        )

    def _run_single(
        self,
        repo_path: Path,
        *,
        requests: list[dict[str, Any]],
        target_commit: str | None,
        current_target_commit: str | None,
    ) -> list[MergePreflightCheck]:
        item = requests[0]
        pr = item["request"]
        left_commit = target_commit or current_target_commit
        right_commit = item["source_commit"]
        check = self._run_merge_check(
            repo_path,
            index=1,
            kind="single",
            pr_ids=[pr.pr_id],
            source_commits=[right_commit] if right_commit else [],
            left_commit=left_commit,
            right_commit=right_commit,
            target_commit=target_commit,
            current_target_commit=current_target_commit,
        )
        return [check]

    def _run_pairwise(
        self,
        repo_path: Path,
        *,
        requests: list[dict[str, Any]],
        target_commit: str | None,
        current_target_commit: str | None,
    ) -> list[MergePreflightCheck]:
        checks: list[MergePreflightCheck] = []
        check_index = 1
        target_left = target_commit or current_target_commit
        for left_index in range(len(requests)):
            for right_index in range(left_index + 1, len(requests)):
                left = requests[left_index]
                right = requests[right_index]
                left_commit = left["source_commit"]
                right_commit = right["source_commit"]
                source_commits = [
                    commit for commit in (left_commit, right_commit) if commit is not None
                ]
                check = self._run_pairwise_merge_check(
                    repo_path,
                    index=check_index,
                    pr_ids=[left["request"].pr_id, right["request"].pr_id],
                    source_commits=source_commits,
                    target_left=target_left,
                    left_commit=left_commit,
                    right_commit=right_commit,
                    target_commit=target_commit,
                    current_target_commit=current_target_commit,
                )
                checks.append(check)
                check_index += 1
        return checks

    def _run_pairwise_merge_check(
        self,
        repo_path: Path,
        *,
        index: int,
        pr_ids: list[str],
        source_commits: list[str],
        target_left: str | None,
        left_commit: str | None,
        right_commit: str | None,
        target_commit: str | None,
        current_target_commit: str | None,
    ) -> MergePreflightCheck:
        target_left_check = self._run_merge_check(
            repo_path,
            index=index,
            kind="pairwise",
            pr_ids=pr_ids,
            source_commits=source_commits,
            left_commit=target_left,
            right_commit=left_commit,
            target_commit=target_commit,
            current_target_commit=current_target_commit,
        )
        if target_left_check.status != "clean":
            return target_left_check
        if target_left_check.tree_oid is None:
            return MergePreflightCheck(
                index=index,
                kind="pairwise",
                pr_ids=pr_ids,
                source_commits=source_commits,
                left_commit=target_left,
                right_commit=left_commit,
                target_commit=target_commit,
                current_target_commit=current_target_commit,
                tree_oid=None,
                conflicted_paths=[],
                status="repo_error",
                explanation="failed to resolve target merge tree",
            )

        synthetic_left = self._commit_tree(
            repo_path,
            tree_oid=target_left_check.tree_oid,
            left_commit=target_left,
            right_commit=left_commit,
        )
        if synthetic_left is None:
            return MergePreflightCheck(
                index=index,
                kind="pairwise",
                pr_ids=pr_ids,
                source_commits=source_commits,
                left_commit=target_left,
                right_commit=left_commit,
                target_commit=target_commit,
                current_target_commit=current_target_commit,
                tree_oid=target_left_check.tree_oid,
                conflicted_paths=[],
                status="repo_error",
                explanation="failed to create synthetic pairwise merge commit",
            )

        return self._run_merge_check(
            repo_path,
            index=index,
            kind="pairwise",
            pr_ids=pr_ids,
            source_commits=source_commits,
            left_commit=synthetic_left,
            right_commit=right_commit,
            target_commit=target_commit,
            current_target_commit=current_target_commit,
        )

    def _run_ordered(
        self,
        repo_path: Path,
        *,
        requests: list[dict[str, Any]],
        target_commit: str | None,
        current_target_commit: str | None,
    ) -> list[MergePreflightCheck]:
        checks: list[MergePreflightCheck] = []
        current_left = target_commit or current_target_commit
        cumulative_pr_ids: list[str] = []
        cumulative_source_commits: list[str] = []
        for index, item in enumerate(requests, start=1):
            pr = item["request"]
            right_commit = item["source_commit"]
            cumulative_pr_ids.append(pr.pr_id)
            if right_commit:
                cumulative_source_commits.append(right_commit)
            check = self._run_merge_check(
                repo_path,
                index=index,
                kind="ordered",
                pr_ids=list(cumulative_pr_ids),
                source_commits=list(cumulative_source_commits),
                left_commit=current_left,
                right_commit=right_commit,
                target_commit=target_commit,
                current_target_commit=current_target_commit,
            )
            checks.append(check)
            if check.status != "clean":
                break
            if check.tree_oid is None:
                break
            current_left = self._commit_tree(
                repo_path,
                tree_oid=check.tree_oid,
                left_commit=current_left,
                right_commit=right_commit,
            )
            if current_left is None:
                failure = MergePreflightCheck(
                    index=index,
                    kind="ordered",
                    pr_ids=list(cumulative_pr_ids),
                    source_commits=list(cumulative_source_commits),
                    left_commit=check.left_commit,
                    right_commit=check.right_commit,
                    target_commit=target_commit,
                    current_target_commit=current_target_commit,
                    tree_oid=check.tree_oid,
                    conflicted_paths=[],
                    status="repo_error",
                    explanation="failed to create synthetic merge commit",
                )
                checks[-1] = failure
                break
        return checks

    def _run_merge_check(
        self,
        repo_path: Path,
        *,
        index: int,
        kind: MergePreflightMode,
        pr_ids: list[str],
        source_commits: list[str],
        left_commit: str | None,
        right_commit: str | None,
        target_commit: str | None,
        current_target_commit: str | None,
    ) -> MergePreflightCheck:
        if left_commit is None or right_commit is None:
            return MergePreflightCheck(
                index=index,
                kind=kind,
                pr_ids=pr_ids,
                source_commits=source_commits,
                left_commit=left_commit,
                right_commit=right_commit,
                target_commit=target_commit,
                current_target_commit=current_target_commit,
                tree_oid=None,
                conflicted_paths=[],
                status="missing_ref",
                explanation="missing commit reference for merge check",
            )
        result = self._git(
            repo_path,
            "merge-tree",
            "--write-tree",
            "--name-only",
            "--no-messages",
            left_commit,
            right_commit,
        )
        if result.returncode not in {0, 1}:
            return MergePreflightCheck(
                index=index,
                kind=kind,
                pr_ids=pr_ids,
                source_commits=source_commits,
                left_commit=left_commit,
                right_commit=right_commit,
                target_commit=target_commit,
                current_target_commit=current_target_commit,
                tree_oid=None,
                conflicted_paths=[],
                status="repo_error",
                explanation=self._git_text(result) or "git merge-tree failed",
            )
        tree_oid, conflicted_paths = self._parse_merge_tree_output(result.stdout)
        if result.returncode == 1:
            explanation = self._conflict_explanation(conflicted_paths)
            return MergePreflightCheck(
                index=index,
                kind=kind,
                pr_ids=pr_ids,
                source_commits=source_commits,
                left_commit=left_commit,
                right_commit=right_commit,
                target_commit=target_commit,
                current_target_commit=current_target_commit,
                tree_oid=tree_oid,
                conflicted_paths=conflicted_paths,
                status="conflict",
                explanation=explanation,
            )
        explanation = "merged cleanly"
        return MergePreflightCheck(
            index=index,
            kind=kind,
            pr_ids=pr_ids,
            source_commits=source_commits,
            left_commit=left_commit,
            right_commit=right_commit,
            target_commit=target_commit,
            current_target_commit=current_target_commit,
            tree_oid=tree_oid,
            conflicted_paths=[],
            status="clean",
            explanation=explanation,
        )

    def _first_missing_ref_error(
        self,
        requests: list[dict[str, Any]],
        *,
        mode: MergePreflightMode,
    ) -> MergePreflightCheck | None:
        for index, item in enumerate(requests, start=1):
            if item["source_commit"] is not None and (item["target_commit"] is not None):
                continue
            missing = []
            if item["request"].source_commit is None and item["request"].source_branch is None:
                missing.append("source commit/ref")
            elif item["source_commit"] is None:
                missing.append(
                    f"source commit/ref ({item['request'].source_commit or item['request'].source_branch})"
                )
            if item["request"].target_commit is None and item["request"].target_branch is None:
                missing.append("target commit/ref")
            elif item["target_commit"] is None:
                missing.append(
                    f"target commit/ref ({item['request'].target_commit or item['request'].target_branch})"
                )
            if not missing:
                continue
            explanation = ", ".join(missing)
            return MergePreflightCheck(
                index=index,
                kind=mode,
                pr_ids=[item["request"].pr_id],
                source_commits=[item["source_commit"]] if item["source_commit"] else [],
                left_commit=item["target_commit"],
                right_commit=item["source_commit"],
                target_commit=item["target_commit"],
                current_target_commit=item["participant"].current_target_commit,
                tree_oid=None,
                conflicted_paths=[],
                status="missing_ref",
                explanation=f"missing ref for PR {item['request'].pr_id}: {explanation}",
            )
        return None

    def _apply_participant_outcomes(
        self,
        participants: list[MergePreflightParticipant],
        *,
        checks: list[MergePreflightCheck],
        warnings: list[str],
        status: MergePreflightStatus,
    ) -> None:
        if status == "stale_target_branch":
            self._apply_stale_warning(participants, warnings)
        for check in checks:
            if check.status == "clean":
                continue
            if check.kind == "single":
                self._mark_participants(participants, check.pr_ids, check.status, check.explanation)
            elif check.kind == "pairwise":
                self._mark_participants(participants, check.pr_ids, check.status, check.explanation)
            else:
                self._mark_participants(
                    participants,
                    check.pr_ids[-1:],
                    check.status,
                    check.explanation,
                )

    def _apply_stale_warning(
        self,
        participants: list[MergePreflightParticipant],
        warnings: list[str],
    ) -> None:
        if not warnings:
            return
        explanation = warnings[0]
        for participant in participants:
            if participant.status == "clean":
                participant.status = "stale_target_branch"
                participant.explanation = explanation

    def _mark_participants(
        self,
        participants: list[MergePreflightParticipant],
        pr_ids: list[str],
        status: MergePreflightStatus,
        explanation: str,
    ) -> None:
        wanted = set(pr_ids)
        for participant in participants:
            if participant.pr_id in wanted:
                participant.status = status
                participant.explanation = explanation

    def _overall_status(
        self,
        *,
        checks: list[MergePreflightCheck],
        warnings: list[str],
    ) -> MergePreflightStatus:
        for status in ("repo_error", "provider_error", "missing_ref", "conflict"):
            if any(check.status == status for check in checks):
                return status
        if warnings:
            return "stale_target_branch"
        return "clean"

    def _resolve_target_context(
        self,
        repo_path: Path,
        *,
        target_branch: str | None,
        target_commit: str | None,
    ) -> tuple[str | None, str | None]:
        current_target_commit = (
            self._resolve_commit(repo_path, sha=None, ref_name=target_branch)
            if target_branch
            else None
        )
        expected_target_commit = self._resolve_commit(
            repo_path,
            sha=target_commit,
            ref_name=target_branch,
        )
        if expected_target_commit is None:
            expected_target_commit = current_target_commit
        return expected_target_commit, current_target_commit

    def _resolve_commit(
        self,
        repo_path: Path,
        *,
        sha: str | None,
        ref_name: str | None,
    ) -> str | None:
        for candidate in (sha, ref_name):
            text = self._resolve_commit_candidate(repo_path, candidate)
            if text is not None:
                return text
        return None

    def _resolve_commit_candidate(
        self,
        repo_path: Path,
        candidate: str | None,
    ) -> str | None:
        text = self._normalize_optional_text(candidate)
        if not text:
            return None
        result = self._git(
            repo_path,
            "rev-parse",
            "--verify",
            "--quiet",
            f"{text}^{{commit}}",
        )
        if result.returncode != 0:
            return None
        resolved = result.stdout.strip()
        return resolved or None

    def _commit_tree(
        self,
        repo_path: Path,
        *,
        tree_oid: str,
        left_commit: str | None,
        right_commit: str | None,
    ) -> str | None:
        if not tree_oid or left_commit is None or right_commit is None:
            return None
        env = {
            "GIT_AUTHOR_NAME": "Evander Preflight",
            "GIT_AUTHOR_EMAIL": "preflight@example.invalid",
            "GIT_COMMITTER_NAME": "Evander Preflight",
            "GIT_COMMITTER_EMAIL": "preflight@example.invalid",
            "GIT_AUTHOR_DATE": _FIXED_COMMIT_DATE,
            "GIT_COMMITTER_DATE": _FIXED_COMMIT_DATE,
        }
        result = self._git(
            repo_path,
            "commit-tree",
            tree_oid,
            "-p",
            left_commit,
            "-p",
            right_commit,
            "-m",
            "preflight synthetic merge",
            env=env,
        )
        if result.returncode != 0:
            return None
        commit = result.stdout.strip()
        return commit or None

    def _parse_merge_tree_output(self, output: str) -> tuple[str | None, list[str]]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return None, []
        tree_oid = lines[0] if _OID_RE.match(lines[0]) else None
        paths = lines[1:] if tree_oid else lines
        return tree_oid, self._unique_paths(paths)

    def _conflict_explanation(self, paths: list[str]) -> str:
        if not paths:
            return "merge conflict"
        if len(paths) == 1:
            return f"merge conflict on {paths[0]}"
        sample = ", ".join(paths[:3])
        suffix = "" if len(paths) <= 3 else f" and {len(paths) - 3} more"
        return f"merge conflict on {len(paths)} paths: {sample}{suffix}"

    def _summary_text(
        self,
        *,
        mode: MergePreflightMode,
        status: MergePreflightStatus,
        participants: list[MergePreflightParticipant],
        target_commit: str | None,
        current_target_commit: str | None,
        warnings: list[str],
        errors: list[str],
        checks: list[MergePreflightCheck] | None = None,
    ) -> str:
        pr_ids = ", ".join(participant.pr_id for participant in participants) or "no PRs"
        if status == "clean":
            return f"{mode} preflight clean for PRs {pr_ids}."
        if status == "stale_target_branch" and warnings:
            return (
                f"{mode} preflight is stale for PRs {pr_ids}: "
                f"target moved from {target_commit} to {current_target_commit}."
            )
        if status == "missing_ref" and errors:
            return f"{mode} preflight missing ref for PRs {pr_ids}: {errors[0]}"
        if status == "repo_error" and errors:
            return f"{mode} preflight repository error for PRs {pr_ids}: {errors[0]}"
        if status == "conflict" and checks:
            path_count = sum(len(check.conflicted_paths) for check in checks)
            return (
                f"{mode} preflight found conflict for PRs {pr_ids}: {path_count} conflicted paths."
            )
        if errors:
            return f"{mode} preflight reported {status} for PRs {pr_ids}: {errors[0]}"
        return f"{mode} preflight reported {status} for PRs {pr_ids}."

    def _unique_paths(self, paths: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            unique.append(path)
        return unique

    def _first_text(self, values: Sequence[str | None]) -> str | None:
        for value in values:
            text = self._normalize_optional_text(value)
            if text:
                return text
        return None

    def _normalize_optional_text(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _git_status(self, repo_path: Path) -> subprocess.CompletedProcess[str]:
        return self._git(repo_path, "rev-parse", "--git-dir")

    def _git(
        self,
        repo_path: Path,
        *args: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, **env} if env is not None else None,
            )
        except OSError as exc:
            return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))

    def _git_text(self, proc: subprocess.CompletedProcess[str]) -> str:
        return f"{proc.stdout or ''}{proc.stderr or ''}".strip()


__all__ = [
    "GitMergePreflightEngine",
    "MergePreflightCheck",
    "MergePreflightMode",
    "MergePreflightParticipant",
    "MergePreflightPullRequest",
    "MergePreflightReport",
    "MergePreflightRequest",
    "MergePreflightStatus",
]
