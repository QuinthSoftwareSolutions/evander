from __future__ import annotations
from collections.abc import Mapping
import base64
import json
import os
import time
from typing import Any
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from evander.admin.services.pr_publication_policy import (
    PrPublicationPolicy,
    require_personal_connection,
    require_pr_creator,
)
from evander.admin.services.managed_git_common import (
    ManagedGitConflictError,
)

from evander.admin.services.managed_git_service import _AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS, _AZURE_PR_SOURCE_CONFIRMATION_INTERVAL_SECONDS, AzurePrRequestError, AzurePrCreationUncertain

def _create_or_reuse_azure_pr(
    self,
    record: Mapping[str, Any],
    *,
    destination_branch: str,
    base_branch: str,
    expected_commit_sha: str,
    title: str,
    body: str,
    draft: bool,
    title_explicit: bool = True,
    work_item_id: str = "",
    publication_context_id: str | None = None,
    publication_directory_path: str | None = None,
    publication_policy: str = "allow_bot_fallback",
    preflight_only: bool = False,
) -> dict[str, Any]:
    policy = PrPublicationPolicy(publication_policy)
    organization, _, _ = self._azure_repository_parts(str(record["canonical_repository_url"]))
    config = self._resolved_config_for_record(record)
    pat_env = str(getattr(config, "azure_devops_pr_pat_env", "") or "AZURE_DEVOPS_PR_PAT")
    bot_pat = os.environ.get(pat_env, "").strip()
    bot_auth = "Basic " + base64.b64encode(f":{bot_pat}".encode()).decode() if bot_pat else None
    binding = dict(
        managed_repo_id=record["managed_repo_id"], commit_sha=expected_commit_sha,
        directory_path=publication_directory_path or record["directory_path"],
        organization=organization,
    ) if publication_context_id else {}
    context: dict[str, Any] = {}
    excluded: set[str] = set()
    reasons: list[str] = []
    arguments = dict(destination_branch=destination_branch, base_branch=base_branch,
                     expected_commit_sha=expected_commit_sha, title=title, body=body,
                     draft=draft, title_explicit=title_explicit, work_item_id=work_item_id)
    while True:
        personal_auth = None
        if publication_context_id:
            context, personal_auth = self.ado_user_connections.resolve_publication(
                publication_context_id, **binding, excluded_methods=frozenset(excluded))
            if context.get("work_item_ids"):
                arguments["work_item_ids"] = context["work_item_ids"]
        reason = context.get("fallback_reason") if publication_context_id else "publication_context_missing"
        if reason and reason not in reasons:
            reasons.append(reason)
        if policy == PrPublicationPolicy.REQUIRE_PERSONAL:
            require_personal_connection(
                {**context, "fallback_reason": ";".join(reasons)} if context else context,
                personal_auth,
            )
            arguments["required_creator_id"] = context["identity_id"]
        if preflight_only:
            arguments["preflight_only"] = True
        auth = personal_auth or bot_auth
        if not auth:
            raise RuntimeError(f"Azure DevOps PR credentials are unavailable in {pat_env}")
        credential_source = context.get("credential_source", "personal_pat") if personal_auth else "bot"
        try:
            result = self._create_or_reuse_azure_pr_using_auth(record, auth=auth, **arguments)
            break
        except AzurePrRequestError as exc:
            if not personal_auth or exc.status_code not in {401, 403}:
                raise
            reasons.append(credential_source + ("_rejected" if exc.status_code == 401 else "_permission_denied"))
            excluded.add(credential_source)
            # Each personal method is tried at most once. A definitive authorization
            # failure permits fallback; an uncertain POST remains guarded below.
        except AzurePrCreationUncertain:
            reconciliation_auth = auth
            if policy == PrPublicationPolicy.ALLOW_BOT_FALLBACK and bot_auth:
                if personal_auth:
                    reasons.append("personal_pr_creation_uncertain")
                credential_source = "bot"
                reconciliation_auth = bot_auth
            result = self._create_or_reuse_azure_pr_using_auth(record, auth=reconciliation_auth, **arguments)
            break
    if preflight_only:
        return result
    actual_creator_id = str((result.get("actual_creator") or {}).get("id") or "").lower()
    if context.get("identity_id") and actual_creator_id != context["identity_id"].lower():
        reasons.append("existing_pr_creator_mismatch" if not result.get("created") else "created_pr_creator_mismatch")
    result.update(intended_creator_id=context.get("identity_id"),
                  credential_source=credential_source, fallback_reason=";".join(reasons) or None)
    self.ado_user_connections.record_publication(publication_context_id, result)
    return result


def _create_or_reuse_azure_pr_using_auth(
    self,
    record: Mapping[str, Any],
    *,
    auth: str,
    destination_branch: str,
    base_branch: str,
    expected_commit_sha: str,
    title: str,
    body: str,
    draft: bool,
    title_explicit: bool = True,
    work_item_id: str = "",
    work_item_ids: list[str] | None = None,
    required_creator_id: str | None = None,
    preflight_only: bool = False,
) -> dict[str, Any]:
    organization, project, repository = self._azure_repository_parts(
        str(record["canonical_repository_url"])
    )
    api_root = (
        "https://dev.azure.com/"
        f"{quote(organization, safe='')}/{quote(project, safe='')}/_apis/git/"
        f"repositories/{quote(repository, safe='')}/pullrequests"
    )
    query = (
        "?searchCriteria.status=active"
        f"&searchCriteria.sourceRefName={quote(f'refs/heads/{destination_branch}', safe='')}"
        f"&searchCriteria.targetRefName={quote(f'refs/heads/{base_branch}', safe='')}"
        "&api-version=7.1"
    )
    attempt_key = json.dumps([record.get("managed_repo_id") or record["canonical_repository_url"], destination_branch, base_branch])
    bounded_body, truncation = self._bounded_azure_pr_description(body)
    existing = self._azure_request_json(api_root + query, auth=auth)
    values = existing.get("value") if isinstance(existing, dict) else None
    if isinstance(values, list) and values:
        item = values[0]
        if not isinstance(item, Mapping):
            raise RuntimeError("Azure DevOps PR API returned an invalid response")
        if required_creator_id:
            require_pr_creator(item, required_creator_id, existing=True)
        if preflight_only:
            return {"provider": "azure_devops", "pr_id": str(item.get("pullRequestId") or "")}
        self.ado_user_connections.mark_attempt(attempt_key, confirmed=True)
        self._confirm_existing_azure_pr_source_commit(
            api_root=api_root,
            pr=item,
            expected_commit_sha=expected_commit_sha,
            auth=auth,
        )
        pr_id = str(item.get("pullRequestId") or "").strip()
        if not pr_id:
            raise RuntimeError("Azure DevOps PR API returned no pull request ID")
        update: dict[str, Any] = {"description": bounded_body}
        if title_explicit:
            update["title"] = title
        updated_item = self._azure_request_json(
            f"{api_root}/{quote(pr_id, safe='')}?api-version=7.1",
            auth=auth,
            payload=update,
            method="PATCH",
        )
        item = {**dict(item), **updated_item}
        link_result = self._ensure_azure_work_item_links(
            item,
            organization=organization,
            project=project,
            work_item_id=work_item_id,
            work_item_ids=work_item_ids,
            auth=auth,
        )
        return {
            "provider": "azure_devops",
            "pr_id": pr_id,
            "pr_url": self._azure_pr_web_url(item),
            "created": False,
            "actual_creator": self._azure_pr_creator(item),
            **truncation,
            **link_result,
        }
    if self.ado_user_connections.has_unconfirmed_attempt(attempt_key):
        raise AzurePrCreationUncertain("A previous PR creation has an uncertain result; awaiting ADO reconciliation. No duplicate PR was created.")
    if preflight_only:
        return {"provider": "azure_devops"}
    self.ado_user_connections.mark_attempt(attempt_key)
    try:
        created = self._azure_request_json(
            api_root + "?api-version=7.1",
            auth=auth,
            payload={
                "sourceRefName": f"refs/heads/{destination_branch}",
                "targetRefName": f"refs/heads/{base_branch}",
                "title": title,
                "description": bounded_body,
                "isDraft": draft,
            },
        )
    except AzurePrRequestError as exc:
        if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code not in {408, 409, 429}:
            self.ado_user_connections.mark_attempt(attempt_key, confirmed=True)
            raise
        raise AzurePrCreationUncertain("ADO PR creation requires reconciliation") from exc
    except (TimeoutError, ValueError) as exc:
        raise AzurePrCreationUncertain("ADO PR creation requires reconciliation") from exc
    if not created.get("pullRequestId"):
        raise AzurePrCreationUncertain("ADO returned no PR identity; reconciliation required")
    self.ado_user_connections.mark_attempt(attempt_key, confirmed=True)
    source_commit = created.get("lastMergeSourceCommit")
    try:
        self._verify_provider_pr_source_commit(
            provider="Azure DevOps",
            expected_commit_sha=expected_commit_sha,
            actual_commit_sha=(
                source_commit.get("commitId") if isinstance(source_commit, Mapping) else None
            ),
        )
    except ManagedGitConflictError as mismatch:
        created_id = str(created.get("pullRequestId") or "").strip()
        try:
            if not created_id:
                raise RuntimeError("Azure DevOps did not return a pull request ID")
            self._azure_request_json(
                f"{api_root}/{quote(created_id, safe='')}?api-version=7.1",
                auth=auth,
                payload={"status": "abandoned"},
                method="PATCH",
            )
        except Exception as cleanup_error:
            raise ManagedGitConflictError(
                "Azure DevOps pull request source moved and the newly created pull "
                "request could not be abandoned",
                details={**mismatch.details, "cleanup_failed": True},
            ) from cleanup_error
        raise
    if required_creator_id:
        require_pr_creator(created, required_creator_id, existing=False)
    link_result = self._ensure_azure_work_item_links(
        created,
        organization=organization,
        project=project,
        work_item_id=work_item_id,
        work_item_ids=work_item_ids,
        auth=auth,
    )
    return {
        "provider": "azure_devops",
        "pr_id": str(created.get("pullRequestId") or ""),
        "pr_url": self._azure_pr_web_url(created),
        "created": True,
        "actual_creator": self._azure_pr_creator(created),
        **truncation,
        **link_result,
    }


def _confirm_existing_azure_pr_source_commit(
    self,
    *,
    api_root: str,
    pr: Mapping[str, Any],
    expected_commit_sha: str,
    auth: str,
) -> None:
    source_commit = pr.get("lastMergeSourceCommit")
    try:
        self._verify_provider_pr_source_commit(
            provider="Azure DevOps",
            expected_commit_sha=expected_commit_sha,
            actual_commit_sha=(
                source_commit.get("commitId") if isinstance(source_commit, Mapping) else None
            ),
        )
        return
    except ManagedGitConflictError as mismatch:
        latest_mismatch = mismatch

    pr_id = str(pr.get("pullRequestId") or "").strip()
    if not pr_id:
        raise RuntimeError("Azure DevOps PR API returned no pull request ID")
    iterations_url = f"{api_root}/{quote(pr_id, safe='')}/iterations?api-version=7.1"
    deadline = time.monotonic() + _AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS
    while True:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise latest_mismatch
        iterations = self._azure_request_json(
            iterations_url,
            auth=auth,
            timeout_seconds=remaining_seconds,
        )
        current_commit_sha = self._latest_azure_pr_iteration_source_commit(iterations)
        try:
            self._verify_provider_pr_source_commit(
                provider="Azure DevOps",
                expected_commit_sha=expected_commit_sha,
                actual_commit_sha=current_commit_sha,
            )
            return
        except ManagedGitConflictError as mismatch:
            latest_mismatch = mismatch
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise latest_mismatch
        time.sleep(
            min(
                _AZURE_PR_SOURCE_CONFIRMATION_INTERVAL_SECONDS,
                remaining_seconds,
            )
        )


def _latest_azure_pr_iteration_source_commit(
    iterations: Mapping[str, Any],
) -> str | None:
    values = iterations.get("value")
    if not isinstance(values, list):
        return None
    latest: tuple[int, Mapping[str, Any]] | None = None
    for item in values:
        if not isinstance(item, Mapping):
            continue
        try:
            iteration_id = int(str(item.get("id") or "").strip())
        except ValueError:
            continue
        if latest is None or iteration_id > latest[0]:
            latest = (iteration_id, item)
    if latest is None:
        return None
    source_commit = latest[1].get("sourceRefCommit")
    if not isinstance(source_commit, Mapping):
        return None
    return str(source_commit.get("commitId") or "").strip() or None


def _ensure_azure_work_item_links(
    self,
    pr: Mapping[str, Any],
    *,
    organization: str,
    project: str,
    work_item_id: str,
    work_item_ids: list[str] | None,
    auth: str,
) -> dict[str, Any]:
    item_ids = sorted(set(work_item_ids or []) | ({work_item_id} if work_item_id else set()))
    linked = []
    warnings = []
    for item_id in item_ids:
        result = self._ensure_azure_work_item_link(
            pr, organization=organization, project=project, work_item_id=item_id, auth=auth,
        )
        if result["work_item_linked"]:
            linked.append(item_id)
        else:
            warnings.append(f"Work item {item_id}: {result.get('work_item_link_warning', 'Link failed')}")
    return {
        "work_item_linked": bool(item_ids) and len(linked) == len(item_ids),
        "work_item_ids": item_ids,
        "linked_work_item_ids": linked,
        **({"work_item_link_warning": "; ".join(warnings)} if warnings else {}),
    }


def _ensure_azure_work_item_link(
    self,
    pr: Mapping[str, Any],
    *,
    organization: str,
    project: str,
    work_item_id: str,
    auth: str,
) -> dict[str, Any]:
    if not work_item_id:
        return {"work_item_linked": False}
    repository = pr.get("repository")
    repository_id = (
        str(repository.get("id") or "").strip() if isinstance(repository, Mapping) else ""
    )
    project_info = repository.get("project") if isinstance(repository, Mapping) else None
    project_id = (
        str(project_info.get("id") or "").strip() if isinstance(project_info, Mapping) else ""
    )
    pr_id = str(pr.get("pullRequestId") or "").strip()
    if not repository_id or not project_id or not pr_id:
        return {
            "work_item_linked": False,
            "work_item_link_warning": (
                "Azure DevOps PR response omitted repository identity required for "
                "work-item association"
            ),
        }
    artifact_url = f"vstfs:///Git/PullRequestId/{project_id}%2F{repository_id}%2F{pr_id}"
    work_item_url = (
        "https://dev.azure.com/"
        f"{quote(organization, safe='')}/{quote(project, safe='')}/_apis/wit/"
        f"workitems/{quote(work_item_id, safe='')}"
    )
    try:
        item = self._azure_request_json(
            f"{work_item_url}?$expand=relations&api-version=7.1",
            auth=auth,
        )
        relations = item.get("relations")
        if isinstance(relations, list) and any(
            isinstance(relation, Mapping)
            and relation.get("rel") == "ArtifactLink"
            and relation.get("url") == artifact_url
            for relation in relations
        ):
            return {"work_item_linked": True}
        self._azure_request_json(
            f"{work_item_url}?api-version=7.1",
            auth=auth,
            payload=[
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "ArtifactLink",
                        "url": artifact_url,
                        "attributes": {"name": "Pull Request"},
                    },
                }
            ],
            method="PATCH",
            content_type="application/json-patch+json",
        )
    except Exception as exc:
        return {
            "work_item_linked": False,
            "work_item_link_warning": str(exc),
        }
    return {"work_item_linked": True}


def _azure_request_json(
    url: str,
    *,
    auth: str,
    payload: Mapping[str, Any] | list[Mapping[str, Any]] | None = None,
    method: str | None = None,
    content_type: str = "application/json",
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        url,
        data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={
            "Accept": "application/json",
            "Authorization": auth,
            "Content-Type": content_type,
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise AzurePrRequestError(exc.code) from exc
    except URLError as exc:
        raise AzurePrRequestError() from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Azure DevOps PR API returned an invalid response")
    return parsed
