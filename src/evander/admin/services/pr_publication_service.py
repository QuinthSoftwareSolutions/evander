from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from evander.admin.services.managed_git_common import _normalize_object_id, _normalize_ref
from evander.admin.services.pr_publication_policy import PrPublicationPolicy


@dataclass(frozen=True, slots=True)
class PrPublicationDependencies:
    require_scoped_record: Callable[[str, str], Mapping[str, Any]]
    normalize_branch: Callable[..., str]
    resolve_publication: Callable[..., Any]
    azure_repository_parts: Callable[[str], tuple[str, str, str]]
    resolve_pr_tracking_session: Callable[[str], tuple[Any, Any] | None]
    azure_work_item_id: Callable[[str, str], str]
    target_lock: Callable[[str, str], AbstractContextManager[Any]]
    push_branch: Callable[..., dict[str, Any]]
    create_or_reuse_github_pr: Callable[..., dict[str, Any]]
    create_or_reuse_azure_pr: Callable[..., dict[str, Any]]
    persist_pr_tracking: Callable[..., None]


class PrPublicationService:
    """Coordinates a verified Git push, provider publication and feedback binding."""

    def __init__(self, dependencies: PrPublicationDependencies) -> None:
        self._deps = dependencies

    def create(
        self,
        *,
        managed_repo_id: str,
        source_ref: str,
        expected_commit_sha: str,
        destination_branch: str,
        directory_path: str,
        base_branch: str | None = None,
        session_id: str | None = None,
        work_item_id: str | None = None,
        title: str | None = None,
        body: str | None = None,
        draft: bool | None = None,
        fill: bool = True,
        publication_context_id: str | None = None,
        publication_policy: str = "allow_bot_fallback",
    ) -> dict[str, Any]:
        policy = PrPublicationPolicy(publication_policy)
        record = self._deps.require_scoped_record(managed_repo_id, directory_path)
        destination = self._deps.normalize_branch(
            destination_branch, field_name="destination_branch"
        )
        base = self._deps.normalize_branch(
            base_branch or str(record.get("default_branch") or "main"),
            field_name="base_branch",
        )
        expected = _normalize_object_id(expected_commit_sha, field_name="expected_commit_sha")
        normalized_session_id = str(session_id or "").strip()
        normalized_work_item_id = str(work_item_id or "").strip()
        normalized_title = str(title or "").strip() or f"Merge {destination} into {base}"
        title_explicit = bool(str(title or "").strip())
        normalized_body = str(body or "")
        provider = str(record.get("provider") or "").strip()
        if provider not in {"github", "azure_devops"}:
            raise ValueError(
                f"Managed PR creation is unsupported for provider {provider or 'generic'}"
            )
        if publication_context_id or policy == PrPublicationPolicy.REQUIRE_PERSONAL:
            if provider != "azure_devops":
                raise ValueError("Personal PR attribution requires an Azure DevOps repository")
        if publication_context_id:
            self._deps.resolve_publication(
                publication_context_id,
                managed_repo_id=managed_repo_id,
                commit_sha=expected,
                directory_path=directory_path,
                organization=self._deps.azure_repository_parts(record["canonical_repository_url"])[
                    0
                ],
            )
        tracking_session = self._deps.resolve_pr_tracking_session(normalized_session_id)
        if provider == "azure_devops":
            normalized_work_item_id = self._deps.azure_work_item_id(
                normalized_work_item_id,
                normalized_session_id,
            )
        destination_ref = f"refs/heads/{destination}"
        with self._deps.target_lock(managed_repo_id, f"upstream:{destination_ref}"):
            if policy == PrPublicationPolicy.REQUIRE_PERSONAL:
                # Validate credentials and any existing PR's creator before pushing a ref.
                self._deps.create_or_reuse_azure_pr(
                    record,
                    destination_branch=destination,
                    base_branch=base,
                    expected_commit_sha=expected,
                    title=normalized_title,
                    body=normalized_body,
                    draft=bool(draft),
                    publication_context_id=publication_context_id,
                    publication_directory_path=directory_path,
                    publication_policy=policy,
                    preflight_only=True,
                )
            published = self._deps.push_branch(
                managed_repo_id,
                source_ref,
                expected,
                destination,
                directory_path,
                _target_lock_held=True,
            )
            if not published.get("success"):
                return dict(published)
            if provider == "github":
                pr = self._deps.create_or_reuse_github_pr(
                    record,
                    destination_branch=destination,
                    base_branch=base,
                    expected_commit_sha=expected,
                    title=normalized_title,
                    body=normalized_body,
                    draft=bool(draft),
                    fill=fill,
                )
            elif provider == "azure_devops":
                pr = self._deps.create_or_reuse_azure_pr(
                    record,
                    destination_branch=destination,
                    base_branch=base,
                    expected_commit_sha=expected,
                    title=normalized_title,
                    body=normalized_body,
                    draft=bool(draft),
                    title_explicit=title_explicit,
                    work_item_id=normalized_work_item_id,
                    **(
                        {
                            "publication_context_id": publication_context_id,
                            "publication_directory_path": directory_path,
                        }
                        if publication_context_id
                        else {}
                    ),
                    **(
                        {"publication_policy": policy}
                        if policy == PrPublicationPolicy.REQUIRE_PERSONAL
                        else {}
                    ),
                )
            self._deps.persist_pr_tracking(
                record,
                pr=pr,
                source_branch=destination,
                target_branch=base,
                session_id=normalized_session_id,
                directory_path=directory_path,
                tracking_session=tracking_session,
            )
        return {
            "success": True,
            "managed_repo_id": managed_repo_id,
            "source_ref": published.get("source_ref")
            or _normalize_ref(source_ref, field_name="source_ref"),
            "destination_branch": destination,
            "expected_commit_sha": expected,
            "base_branch": base,
            "session_id": normalized_session_id or None,
            "work_item_id": normalized_work_item_id or None,
            **pr,
        }
