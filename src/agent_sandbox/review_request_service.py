from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel

from .models import RequestPrReviewResponse, SessionManifest
from .review_request_store import ReviewRequestRecord, ReviewRequestStore
from .services import normalize_pr_feedback_provider
from .storage import JsonStore
from .config import GatewayConfig


class ReviewRequestMetadata(BaseModel):
    canonical_url: str | None = None
    title: str | None = None
    body: str | None = None
    base_branch: str | None = None
    head_branch: str | None = None
    base_commit: str | None = None
    head_commit: str | None = None


class ReviewRequestProvider(Protocol):
    def fetch_metadata(self, *, repo: str, pr_id: str) -> ReviewRequestMetadata: ...


class GitHubReviewRequestProvider:
    def __init__(
        self,
        *,
        token: str,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_metadata(self, *, repo: str, pr_id: str) -> ReviewRequestMetadata:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.api_base_url}/repos/{repo}/pulls/{pr_id}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            payload = response.json()
        base = payload.get("base") if isinstance(payload.get("base"), dict) else {}
        head = payload.get("head") if isinstance(payload.get("head"), dict) else {}
        return ReviewRequestMetadata(
            canonical_url=payload.get("html_url") or payload.get("url"),
            title=payload.get("title"),
            body=payload.get("body"),
            base_branch=base.get("ref"),
            head_branch=head.get("ref"),
            base_commit=base.get("sha"),
            head_commit=head.get("sha"),
        )


class AzureDevOpsReviewRequestProvider:
    def __init__(
        self,
        *,
        organization_url: str,
        project: str,
        pat: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self.pat = pat
        self.timeout_seconds = timeout_seconds

    def fetch_metadata(self, *, repo: str, pr_id: str) -> ReviewRequestMetadata:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                (
                    f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo}/"
                    f"pullRequests/{pr_id}"
                ),
                params={"api-version": "7.1"},
                auth=("", self.pat),
            )
            response.raise_for_status()
            payload = response.json()
        return ReviewRequestMetadata(
            canonical_url=payload.get("url"),
            title=payload.get("title"),
            body=payload.get("description"),
            base_branch=_strip_ref_prefix(payload.get("targetRefName")),
            head_branch=_strip_ref_prefix(payload.get("sourceRefName")),
            base_commit=payload.get("lastMergeTargetCommit", {}).get("commitId"),
            head_commit=payload.get("lastMergeSourceCommit", {}).get("commitId"),
        )


class ReviewRequestService:
    def __init__(
        self,
        *,
        providers: dict[str, ReviewRequestProvider],
        store: ReviewRequestStore,
        session_store: JsonStore[SessionManifest],
    ) -> None:
        self.providers = providers
        self.store = store
        self.session_store = session_store

    def enqueue_request(
        self,
        *,
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        requested_by: str | None = None,
        request_channel: str | None = None,
        review_type: str | None = None,
        instructions: str | None = None,
        directory_path: str | None = None,
        model: str | None = None,
        labels: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RequestPrReviewResponse:
        provider_name = normalize_pr_feedback_provider(provider)
        normalized_metadata = dict(metadata or {})
        session = self.session_store.get(session_id)
        resolved_directory = (
            directory_path.strip()
            if isinstance(directory_path, str) and directory_path.strip()
            else (session.directory_path if session is not None else "root")
        )
        requested_at = datetime.now(UTC)
        request_id = f"rvr_{uuid4().hex[:16]}"
        provider_metadata = self._resolve_metadata(
            provider_name=provider_name,
            repo=repo,
            pr_id=pr_id,
            metadata=normalized_metadata,
        )
        record = ReviewRequestRecord(
            request_id=request_id,
            requested_at=requested_at,
            requested_by=(requested_by or "").strip(),
            request_channel=(request_channel or "").strip(),
            provider=provider_name,
            repo=repo,
            pr_id=pr_id,
            canonical_url=provider_metadata.canonical_url or "",
            title=provider_metadata.title or "",
            body=provider_metadata.body or "",
            base_branch=provider_metadata.base_branch or "",
            head_branch=provider_metadata.head_branch or "",
            base_commit=provider_metadata.base_commit,
            head_commit=provider_metadata.head_commit,
            review_type=(review_type or "review").strip() or "review",
            instructions=(instructions or "").strip(),
            directory_path=resolved_directory,
            model=(model or "").strip() or None,
            labels=labels or {},
            metadata=normalized_metadata,
            status="pending",
            updated_at=requested_at,
        )
        self.store.add_request_if_new(record)
        return RequestPrReviewResponse(
            status=record.status,
            request_id=record.request_id,
            provider=record.provider,
            repo=record.repo,
            pr_id=record.pr_id,
            canonical_url=record.canonical_url or None,
            title=record.title or None,
            body=record.body or None,
            base_branch=record.base_branch or None,
            head_branch=record.head_branch or None,
            base_commit=record.base_commit,
            head_commit=record.head_commit,
            requested_at=record.requested_at,
            requested_by=record.requested_by or None,
            request_channel=record.request_channel or None,
            review_type=record.review_type,
            instructions=record.instructions or None,
            directory_path=record.directory_path,
            model=record.model,
            labels=record.labels,
            metadata=record.metadata,
        )

    def _resolve_metadata(
        self,
        *,
        provider_name: str,
        repo: str,
        pr_id: str,
        metadata: dict[str, Any],
    ) -> ReviewRequestMetadata:
        if provider_name in self.providers:
            return self.providers[provider_name].fetch_metadata(repo=repo, pr_id=pr_id)
        fallback = ReviewRequestMetadata(
            canonical_url=_metadata_string(metadata, "canonical_url"),
            title=_metadata_string(metadata, "title"),
            body=_metadata_string(metadata, "body"),
            base_branch=_metadata_string(metadata, "base_branch"),
            head_branch=_metadata_string(metadata, "head_branch"),
            base_commit=_metadata_string(metadata, "base_commit"),
            head_commit=_metadata_string(metadata, "head_commit"),
        )
        if fallback.base_branch and fallback.head_branch:
            return fallback
        raise ValueError(f"PR review request provider {provider_name!r} is not configured")


def _metadata_string(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _strip_ref_prefix(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("refs/heads/"):
        return text.removeprefix("refs/heads/") or None
    return text


def _build_review_request_service(
    config: GatewayConfig,
    session_store: JsonStore[SessionManifest],
) -> ReviewRequestService:
    github_token = os.environ.get(config.github_token_env, "").strip()
    azure_pat = os.environ.get(config.azure_devops_pr_pat_env, "").strip()

    providers: dict[str, ReviewRequestProvider] = {}
    if github_token:
        providers["github"] = GitHubReviewRequestProvider(
            token=github_token,
            timeout_seconds=config.email_provider_timeout_seconds,
        )
    if azure_pat:
        providers["azure_devops"] = AzureDevOpsReviewRequestProvider(
            organization_url=config.azure_devops_organization_url,
            project=config.azure_devops_project,
            pat=azure_pat,
            timeout_seconds=config.email_provider_timeout_seconds,
        )
    return ReviewRequestService(
        providers=providers,
        store=ReviewRequestStore(config.runtime_dir / "review-request-store.json"),
        session_store=session_store,
    )
