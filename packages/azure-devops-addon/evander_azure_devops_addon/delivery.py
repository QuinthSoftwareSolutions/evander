"""Small workflow operations for CI, review, exact-payload approval and verified merge.

The graph owns sequencing and retries. These providers do not run a hidden scheduler.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote
import re
import fcntl
import hashlib

import httpx
from pydantic import BaseModel, ConfigDict, model_validator

from evander.addons import addon_runtime_dir
from evander.azure_devops import azure_devops_organization_url
from evander.effects.providers.base import EffectProviderBase
from evander.events.models import ConfigValue, WorkflowNodeEnvelope
from evander.models import ApprovalGrantSettings, ApprovalStatus, DirectoryPath
from evander.optional_integrations import require_azure_devops_module
from evander.queries.providers.base import QueryProviderBase
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration
from evander.workflow.state_store import WorkflowStateStore

from .builds import positive_id


class DeliveryDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    type: Literal[
        "query:ado:delivery-status",
        "effect:ado:merge-approval",
        "effect:ado:merge-approved",
        "effect:ado:claim-repair",
        "effect:ado:claim-start",
        "effect:ado:record-publication",
    ]
    publication: ConfigValue | None = None
    session_id: ConfigValue
    min_reviewers: ConfigValue = 1
    max_repairs: ConfigValue = 3
    outputs: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_publication(self):
        if self.type != "effect:ado:claim-start" and self.publication is None:
            raise ValueError("This delivery operation requires publication")
        return self


def materialize(node):
    return DeliveryDefinition.model_validate(
        {
            "id": node.id,
            "display_name": node.display_name,
            "type": node.type,
            "outputs": node.outputs,
            **node.config,
        }
    )


def scope(context):
    if context is None:
        raise ValueError("Delivery operations require runtime context")
    directory = DirectoryPath(
        segments=str(context.metadata.get("directory_path") or "root")
    ).as_posix()
    graph = str(context.metadata.get("graph_id") or "").strip()
    if not graph:
        raise ValueError("Delivery operations require a workflow graph")
    return directory, graph


def runtime_services(context):
    admin = context.admin_service
    runtime = getattr(admin, "runtime", admin)
    managed = getattr(admin, "managed_git_admin", None)
    policy = getattr(runtime, "policy", None)
    if managed is None or policy is None:
        raise ValueError("Delivery requires the composed admin runtime")
    return managed, policy


def publication_payload(context, payload):
    directory, _ = scope(context)
    publication = payload.get("publication")
    if not isinstance(publication, Mapping):
        raise ValueError("No published pull request is available yet")
    result = dict(publication)
    for key in (
        "managed_repo_id",
        "pr_id",
        "commit_sha",
        "source_branch",
        "target_branch",
        "build_id",
        "pipeline_id",
    ):
        if not isinstance(result.get(key), (str, int)) or not str(result[key]).strip():
            raise ValueError(f"Publication requires {key}")
        result[key] = str(result[key]).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", result["commit_sha"]):
        raise ValueError("Publication requires an exact commit SHA")
    result["commit_sha"] = result["commit_sha"].lower()
    for key in ("pr_id", "build_id", "pipeline_id"):
        positive_id(result[key], key)
    managed, policy = runtime_services(context)
    session_id = str(payload.get("session_id") or "").strip()
    session = policy.get_session(session_id)
    if session.directory_path != directory:
        raise ValueError("Delivery agent is outside the workflow directory")
    record = managed.get_repository(result["managed_repo_id"], directory_path=directory)
    if record.get("provider") not in {"azure_devops", "ado"}:
        raise ValueError("Delivery requires an Azure DevOps managed repository")
    organization, project, repository = managed._azure_repository_parts(
        record["canonical_repository_url"]
    )
    config = context.provider_config_resolver.resolve_for_directory(directory)
    require_azure_devops_module(config=config)
    if str(config.ado_organization or "").casefold() != organization.casefold():
        raise ValueError(
            "Managed repository must belong to the configured Azure DevOps organization"
        )
    result.update(
        organization=organization, project=project, repository=repository, session_id=session_id
    )
    result["min_reviewers"] = positive_id(payload.get("min_reviewers", 1), "min_reviewers")
    return result, config, policy


class DeliveryClient:
    def __init__(self, config, publication, context):
        self.publication = publication
        token = context.resolve_secret(config.azure_devops_pr_pat_env)
        if not token:
            raise ValueError("Azure DevOps pull request credential is unavailable")
        self._build_token = context.resolve_secret(config.azure_devops_pat_env)
        if not self._build_token:
            raise ValueError("Azure DevOps build credential is unavailable")
        self.client = httpx.Client(
            base_url=f"{azure_devops_organization_url(publication['organization'])}/{quote(publication['project'], safe='')}/_apis/",
            auth=("", token),
            timeout=30.0,
            follow_redirects=False,
        )
        self.pr_path = f"git/repositories/{quote(publication['repository'], safe='')}/pullRequests/{publication['pr_id']}"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def request(self, method, path, **kwargs):
        params = {"api-version": "7.1", **kwargs.pop("params", {})}
        if path.startswith("build/"):
            kwargs["auth"] = ("", self._build_token)
        try:
            response = self.client.request(method, path, params=params, **kwargs)
            response.raise_for_status()
            value = response.json()
        except httpx.HTTPError as exc:
            code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else "unavailable"
            )
            raise RuntimeError(
                f"Azure DevOps delivery request failed ({code}); inspect the run before retrying a write"
            ) from None
        except ValueError:
            raise RuntimeError("Azure DevOps returned invalid delivery data") from None
        if not isinstance(value, dict):
            raise RuntimeError("Azure DevOps returned invalid delivery data")
        return value

    def inspect(self):
        p = self.publication
        pr = self.request("GET", self.pr_path)
        blockers = []
        source = str((pr.get("lastMergeSourceCommit") or {}).get("commitId") or "").lower()
        target = str((pr.get("lastMergeTargetCommit") or {}).get("commitId") or "").lower()
        if source != p["commit_sha"]:
            blockers.append("source_commit_changed")
        if (
            pr.get("sourceRefName") != "refs/heads/" + p["source_branch"]
            or pr.get("targetRefName") != "refs/heads/" + p["target_branch"]
        ):
            blockers.append("pull_request_branches_changed")
        if not re.fullmatch(r"[0-9a-f]{40}", target):
            blockers.append("target_commit_unavailable")
        merge = str((pr.get("lastMergeCommit") or {}).get("commitId") or "").lower()
        merged = pr.get("status") == "completed" and not blockers
        if merged:
            refs = self.request(
                "GET",
                f"git/repositories/{quote(p['repository'], safe='')}/refs",
                params={"filter": "heads/" + p["target_branch"]},
            )
            heads = {
                ref.get("name"): str(ref.get("objectId") or "").lower()
                for ref in refs.get("value", [])
            }
            merged = (
                bool(re.fullmatch(r"[0-9a-f]{40}", merge))
                and heads.get("refs/heads/" + p["target_branch"]) == merge
            )
            if not merged:
                blockers.append("merge_target_needs_verification")
            return {
                "ready": False,
                "merged": merged,
                "repairable": False,
                "blockers": blockers,
                "merge_commit": merge,
                "target_commit": target,
                "feedback": "",
            }
        if pr.get("status") != "active":
            blockers.append("pull_request_not_active")
        if pr.get("isDraft"):
            blockers.append("pull_request_is_draft")
        build = self.request("GET", f"build/builds/{p['build_id']}")
        build_matches = (
            str(build.get("sourceVersion") or "").lower() == p["commit_sha"]
            and str((build.get("definition") or {}).get("id")) == p["pipeline_id"]
            and str((build.get("repository") or {}).get("id"))
            == str((pr.get("repository") or {}).get("id"))
            and build.get("sourceBranch") == "refs/heads/" + p["source_branch"]
        )
        if not build_matches:
            blockers.append("build_does_not_match_commit")
        if build.get("status") != "completed":
            blockers.append("ci_pending")
        elif build.get("result") != "succeeded":
            blockers.append("ci_failed")
        reviewers = pr.get("reviewers")
        if not isinstance(reviewers, list):
            reviewers = []
        creator = str((pr.get("createdBy") or {}).get("id") or "").casefold()
        approvals = {
            str(r.get("id"))
            for r in reviewers
            if r.get("vote") == 10
            and not r.get("isContainer")
            and r.get("id")
            and str(r["id"]).casefold() != creator
        }
        if len(approvals) < p["min_reviewers"]:
            blockers.append("review_pending")
        if any(r.get("vote") in {-10, -5} for r in reviewers):
            blockers.append("review_changes_requested")
        project_id = str(((pr.get("repository") or {}).get("project") or {}).get("id") or "")
        if not project_id:
            blockers.append("policy_scope_unavailable")
        else:
            policies = self.request(
                "GET",
                "policy/evaluations",
                params={
                    "api-version": "7.1-preview.1",
                    "artifactId": f"vstfs:///CodeReview/CodeReviewId/{project_id}/{p['pr_id']}",
                    "$top": 200,
                },
            ).get("value")
            if not isinstance(policies, list) or len(policies) >= 200:
                blockers.append("policy_evaluation_incomplete")
            elif any(
                e.get("configuration", {}).get("isEnabled", True)
                and e.get("configuration", {}).get("isBlocking", True)
                and e.get("status") not in {"approved", "notApplicable"}
                for e in policies
            ):
                blockers.append("branch_policy_not_satisfied")
        threads = self.request("GET", self.pr_path + "/threads").get("value")
        if not isinstance(threads, list):
            blockers.append("review_threads_unavailable")
            threads = []
        active = [t for t in threads if t.get("status") in {"active", 1} and not t.get("isDeleted")]
        if active:
            blockers.append("review_threads_unresolved")
        feedback = "\n".join(
            str(c.get("content") or "")[:2000]
            for t in active
            for c in t.get("comments", [])
            if not c.get("isDeleted") and c.get("commentType", "text") in {"text", 1}
        )[:8000]
        unsafe = {
            "source_commit_changed",
            "pull_request_branches_changed",
            "pull_request_not_active",
            "build_does_not_match_commit",
            "target_commit_unavailable",
        }
        repairable = bool(
            set(blockers) & {"ci_failed", "review_changes_requested", "review_threads_unresolved"}
        ) and not bool(set(blockers) & unsafe)
        return {
            "ready": not blockers,
            "merged": False,
            "repairable": repairable,
            "blockers": blockers,
            "target_commit": target,
            "merge_commit": merge,
            "feedback": feedback,
        }

    def merge(self):
        p = self.publication
        self.request(
            "PATCH",
            self.pr_path,
            json={
                "status": "completed",
                "lastMergeSourceCommit": {"commitId": p["commit_sha"]},
                "completionOptions": {
                    "bypassPolicy": False,
                    "deleteSourceBranch": False,
                    "mergeStrategy": "noFastForward",
                },
            },
        )
        return self.inspect()


class Operation:
    @classmethod
    def validate_definition(cls, definition):
        return (
            materialize(definition) if isinstance(definition, WorkflowNodeEnvelope) else definition
        )

    def arguments(self, resolved_config):
        definition = getattr(self, "effect_definition", None) or getattr(
            self, "query_definition", None
        )
        value = definition.model_dump()
        value.update(resolved_config or {})
        return value


class RecordPublicationEffect(Operation, EffectProviderBase):
    def execute(self, *, resolved_config=None):
        publication, config, _ = publication_payload(
            self.runtime_context, self.arguments(resolved_config)
        )
        store, directory, graph = state_store(self.runtime_context)
        lock_dir = addon_runtime_dir(store.runtime_dir, "azure-devops") / "delivery-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256((directory + "\0" + graph).encode()).hexdigest()
        with (lock_dir / key).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with DeliveryClient(config, publication, self.runtime_context) as client:
                    pr = client.request("GET", client.pr_path)
                if (
                    str((pr.get("lastMergeSourceCommit") or {}).get("commitId") or "").lower()
                    != publication["commit_sha"]
                    or pr.get("sourceRefName") != "refs/heads/" + publication["source_branch"]
                    or pr.get("targetRefName") != "refs/heads/" + publication["target_branch"]
                ):
                    return {"success": False, "blockers": ["publication_superseded"]}
                store.set(
                    directory,
                    graph,
                    "ado-delivery",
                    "publication",
                    value={**publication, "done": False},
                )
                return {"success": True, "publication": publication}
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


class DeliveryStatusQuery(Operation, QueryProviderBase):
    def execute(self, *, resolved_config=None):
        payload = self.arguments(resolved_config)
        publication, config, _ = publication_payload(self.runtime_context, payload)
        with DeliveryClient(config, publication, self.runtime_context) as client:
            status = client.inspect()
        return {**status, "publication": publication, "commit_sha": publication["commit_sha"]}


def approval_payload(publication):
    return {
        key: publication[key]
        for key in (
            "managed_repo_id",
            "organization",
            "project",
            "repository",
            "pr_id",
            "commit_sha",
            "source_branch",
            "target_branch",
            "build_id",
            "pipeline_id",
            "session_id",
            "min_reviewers",
        )
    } | {"action": "merge_pull_request"}


def state_store(context):
    directory, graph = scope(context)
    return WorkflowStateStore(context.config_path("runtime_dir")), directory, graph


class MergeApprovalEffect(Operation, EffectProviderBase):
    def execute(self, *, resolved_config=None):
        publication, config, policy = publication_payload(
            self.runtime_context, self.arguments(resolved_config)
        )
        with DeliveryClient(config, publication, self.runtime_context) as client:
            status = client.inspect()
        if not status["ready"]:
            return {"approved": False, **status}
        approval = approval_payload(publication)
        from evander.policy import digest_payload

        key = "approval:" + digest_payload(approval)
        store, directory, graph = state_store(self.runtime_context)
        record = store.get(directory, graph, "ado-delivery", key).record
        if record is not None:
            request = policy.get_approval(record.value["request_id"])
        else:
            request = policy.create_approval(
                publication["session_id"],
                "ado_merge",
                approval,
                f"Merge PR {publication['pr_id']} at {publication['commit_sha']} into {publication['target_branch']} after successful CI and review.",
                intended_effect="Complete the selected Azure DevOps pull request without bypassing branch policies.",
                approval_settings=ApprovalGrantSettings(ttl_seconds=86400, max_uses=1),
            )
            store.set(
                directory, graph, "ado-delivery", key, value={"request_id": request.request_id}
            )
        return {
            "approved": request.status == ApprovalStatus.APPROVED,
            "request_id": request.request_id,
            "approval_status": request.status.value,
            "approval_payload": approval,
            "blockers": []
            if request.status == ApprovalStatus.APPROVED
            else ["merge_approval_" + request.status.value],
        }


class ApprovedMergeEffect(Operation, EffectProviderBase):
    def execute(self, *, resolved_config=None):
        publication, config, policy = publication_payload(
            self.runtime_context, self.arguments(resolved_config)
        )
        with DeliveryClient(config, publication, self.runtime_context) as client:
            status = client.inspect()
            if status["merged"]:
                return status
            if not status["ready"]:
                return status
            approval = approval_payload(publication)
            from evander.policy import digest_payload

            store, directory, graph = state_store(self.runtime_context)
            record = store.get(
                directory, graph, "ado-delivery", "approval:" + digest_payload(approval)
            ).record
            if record is None:
                return {"merged": False, "blockers": ["merge_approval_missing"]}
            request = policy.get_approval(record.value["request_id"])
            if (
                request.session_id != publication["session_id"]
                or request.action_type != "ado_merge"
            ):
                raise ValueError("Merge approval belongs to a different operation")
            policy.require_approved(request.request_id, approval, consume=True)
            return client.merge()


def event_identity(execution_context):
    identity = str((execution_context or {}).get("idempotency_key") or "").strip()
    if not identity:
        raise ValueError("Delivery claims require a durable workflow execution identity")
    return identity


class ClaimStartEffect(Operation, EffectProviderBase):
    def execute(self, *, resolved_config=None, execution_context=None):
        payload = self.arguments(resolved_config)
        identity = event_identity(execution_context)
        _, policy = runtime_services(self.runtime_context)
        store, directory, graph = state_store(self.runtime_context)
        session = policy.get_session(str(payload["session_id"]))
        if session.directory_path != directory:
            raise ValueError("Delivery agent is outside the workflow directory")
        result = store.set(
            directory,
            graph,
            "ado-delivery",
            "started",
            value={"owner": identity},
            expected_version=0,
        )
        if result.conflict:
            record = store.get(directory, graph, "ado-delivery", "started").record
            return {"allowed": record.value.get("owner") == identity, "replayed": True}
        return {"allowed": True, "replayed": False}


class ClaimRepairEffect(Operation, EffectProviderBase):
    def execute(self, *, resolved_config=None, execution_context=None):
        payload = self.arguments(resolved_config)
        identity = event_identity(execution_context)
        publication, _, _ = publication_payload(self.runtime_context, payload)
        maximum = positive_id(payload.get("max_repairs", 3), "max_repairs")
        if maximum > 10:
            raise ValueError("max_repairs must be at most ten")
        store, directory, graph = state_store(self.runtime_context)
        for _ in range(8):
            record = store.get(directory, graph, "ado-delivery", "repair-attempts").record
            attempts = list(record.value.get("attempts", [])) if record else []
            existing = next(
                (item for item in attempts if item["commit"] == publication["commit_sha"]), None
            )
            if existing:
                same = existing["owner"] == identity
                return {
                    "allowed": same,
                    "attempt": attempts.index(existing) + 1,
                    "replayed": True,
                    "blockers": [] if same else ["repair_already_requested"],
                }
            if len(attempts) >= maximum:
                return {
                    "allowed": False,
                    "attempt": len(attempts),
                    "blockers": ["repair_budget_exhausted"],
                }
            attempts.append({"commit": publication["commit_sha"], "owner": identity})
            result = store.set(
                directory,
                graph,
                "ado-delivery",
                "repair-attempts",
                value={"attempts": attempts},
                expected_version=record.version if record else 0,
            )
            if not result.conflict:
                return {"allowed": True, "attempt": len(attempts), "replayed": False}
        raise RuntimeError("Repair budget changed concurrently; retry this workflow step")


def register_providers():
    providers = {
        "query:ado:delivery-status": (DeliveryStatusQuery, "Check delivery status"),
        "effect:ado:merge-approval": (MergeApprovalEffect, "Request approval for this exact merge"),
        "effect:ado:merge-approved": (ApprovedMergeEffect, "Merge the approved commit and verify"),
        "effect:ado:claim-repair": (ClaimRepairEffect, "Claim a bounded repair attempt"),
        "effect:ado:claim-start": (ClaimStartEffect, "Start this issue once"),
        "effect:ado:record-publication": (
            RecordPublicationEffect,
            "Track the current published commit",
        ),
    }
    entries = []
    for node_type, (_, label) in providers.items():
        register_node_output_schema(
            node_type=node_type,
            outputs={
                "success": {"type": "boolean"},
                "ready": {"type": "boolean"},
                "merged": {"type": "boolean"},
                "repairable": {"type": "boolean"},
                "approved": {"type": "boolean"},
                "allowed": {"type": "boolean"},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "publication": {"type": "object"},
                "commit_sha": {"type": "string"},
                "feedback": {"type": "string"},
                "merge_commit": {"type": "string"},
            },
        )
        entries.append(
            WorkflowEditorNodeCatalogEntry(
                kind=node_type.split(":")[0],
                type=node_type,
                label=label,
                description="Uses the workflow's directory, exact commit, existing approval store and managed repository.",
                fields=[
                    WorkflowEditorFieldDefinition(
                        key=key,
                        label=key.replace("_", " ").title(),
                        kind="json" if key == "publication" else "text",
                        required=key in {"publication", "session_id"},
                        bindable=True,
                    )
                    for key in ("publication", "session_id", "min_reviewers", "max_repairs")
                ],
            )
        )
    return ProviderPluginRegistration(
        query_providers={k: v[0] for k, v in providers.items() if k.startswith("query:")},
        effect_providers={k: v[0] for k, v in providers.items() if k.startswith("effect:")},
        node_definition_resolvers={k: materialize for k in providers},
        workflow_editor_catalog_entries=tuple(entries),
    )
