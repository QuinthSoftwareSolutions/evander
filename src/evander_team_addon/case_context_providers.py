"""Workflow contracts/adapters for case context and source capture."""

from collections.abc import Mapping
from datetime import datetime, UTC
import json
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from evander.effects.providers.base import EffectProviderBase, scoped_effect_directory_path
from evander.queries.providers.base import QueryProviderBase, scoped_query_directory_path
from evander.events.models import (
    ConfigValue,
    EffectDefinition,
    QueryDefinition,
    WorkflowNodeEnvelope,
)

from .repository import TeamStateRepository
from .services.case_context_service import CaseContextAccess, CaseContextService, digest


class CaseContextQueryDefinition(QueryDefinition):
    type: Literal["query:team:case-context"]
    case_id: ConfigValue | None = None
    case_ids: ConfigValue | None = None
    task_id: ConfigValue | None = None
    release_package_id: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    view_id: ConfigValue = "case-overview"
    view_version: ConfigValue | None = None
    cursor: ConfigValue = 0


class CaseContextSnapshotEffectDefinition(EffectDefinition):
    type: Literal["effect:team:snapshot-case-context"]
    request_id: ConfigValue
    case_id: ConfigValue | None = None
    case_ids: ConfigValue | None = None
    task_id: ConfigValue | None = None
    release_package_id: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    view_id: ConfigValue = "case-overview"
    view_version: ConfigValue | None = None
    recipient_session_id: ConfigValue | None = None
    candidate_commit_sha: ConfigValue | None = None
    analysis_base_commit_sha: ConfigValue | None = None


class CaseContextSnapshotQueryDefinition(QueryDefinition):
    type: Literal["query:team:case-context-snapshot"]
    snapshot_id: ConfigValue
    directory_path: ConfigValue | None = None
    section: ConfigValue | None = None
    cursor: ConfigValue = 0
    limit: ConfigValue = 20


class RecordSourceEvidenceDefinition(EffectDefinition):
    type: Literal["effect:team:record-source-evidence"]
    team_id: ConfigValue
    project_id: ConfigValue | None = None
    case_ids: ConfigValue | None = None
    task_ids: ConfigValue | None = None
    evidence: ConfigValue
    directory_path: ConfigValue | None = None


class CaptureAdoCaseSourceDefinition(EffectDefinition):
    type: Literal["effect:team:capture-ado-case-source"]
    case_id: ConfigValue
    ado_project: ConfigValue
    work_item_id: ConfigValue
    directory_path: ConfigValue | None = None


class RefreshCaseSourcesDefinition(EffectDefinition):
    type: Literal["effect:team:refresh-case-sources"]
    case_ids: ConfigValue
    directory_path: ConfigValue | None = None


def materialize(model, node):
    if not isinstance(node, WorkflowNodeEnvelope):
        return node
    return model.model_validate(
        {
            "id": node.id,
            "type": node.type,
            "display_name": node.display_name,
            "outputs": node.outputs,
            **node.config,
        }
    )


def _values(definition, resolved):
    base_fields = set(QueryDefinition.model_fields) | set(EffectDefinition.model_fields)
    result = {k: v for k, v in definition.model_dump().items() if k not in base_fields}
    result.update(resolved or {})
    return {k: v for k, v in result.items() if v is not None}


def _list(value):
    if isinstance(value, str):
        value = json.loads(value)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError("expected a list of nonempty identifiers")
    return sorted(set(value))


def _service(context):
    runtime_dir = getattr(getattr(context, "resolved_config", None), "runtime_dir", None)
    if runtime_dir is None:
        raise RuntimeError("case-context provider requires a runtime directory")
    return CaseContextService(TeamStateRepository(Path(runtime_dir)))


def _selection(values):
    result = dict(values)
    case_ids = _list(result.pop("case_ids", None))
    case_id = result.pop("case_id", None)
    if case_id:
        case_ids.append(str(case_id))
    result["case_ids"] = sorted(set(case_ids))
    for key in ("view_version", "cursor", "limit"):
        if key in result:
            result[key] = int(result[key])
    return result


class CaseContextQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(CaseContextQueryDefinition, definition)

    def execute(self, *, resolved_config=None):
        values = _selection(_values(self.query_definition, resolved_config))
        values["directory_path"] = scoped_query_directory_path(
            self.runtime_context,
            values.get("directory_path"),
        )
        return {"success": True, **_service(self.runtime_context).assemble(**values)}


class CaseContextSnapshotEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(CaseContextSnapshotEffectDefinition, definition)

    def execute(self, *, resolved_config=None):
        values = _selection(_values(self.effect_definition, resolved_config))
        values["directory_path"] = scoped_effect_directory_path(
            self.runtime_context,
            values.get("directory_path"),
        )
        snapshot = _service(self.runtime_context).create_snapshot(**values)
        return {
            "success": True,
            "snapshot_id": snapshot.id,
            "content_digest": snapshot.content_digest,
            "context": snapshot.context,
            "text": snapshot.context["text"],
            "case_ids": snapshot.case_ids,
        }


class CaseContextSnapshotQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(CaseContextSnapshotQueryDefinition, definition)

    def execute(self, *, resolved_config=None):
        values = _values(self.query_definition, resolved_config)
        values["directory_path"] = scoped_query_directory_path(
            self.runtime_context,
            values.get("directory_path"),
        )
        for key in ("cursor", "limit"):
            if key in values:
                values[key] = int(values[key])
        return {"success": True, **_service(self.runtime_context).get_snapshot(**values)}


def record_source(service, *, directory_path, team_id, project_id, case_ids, task_ids, evidence):
    from .services.admin_service import TeamAdminService

    state = service.repository.read()
    access = CaseContextAccess(state, directory_path=directory_path)
    if not case_ids and not task_ids:
        raise ValueError("source capture requires case or task links")
    if any(not access.case_visible(c) for c in case_ids) or any(
        not access.task_visible(t) for t in task_ids
    ):
        raise PermissionError("source links are outside workflow scope")
    if any(
        state.cases[c].team_id != team_id or state.cases[c].project_id != project_id
        for c in case_ids
    ) or any(state.tasks[t].project_id != project_id for t in task_ids):
        raise ValueError("source ownership must match its cases/tasks")
    if not isinstance(evidence, Mapping):
        raise ValueError("evidence must be a normalized source object")
    allowed = {
        "provider_type",
        "source_type",
        "external_identifier",
        "external_version",
        "fetched_at",
        "expires_at",
        "freshness_status",
        "access_classification",
        "title",
        "body",
        "acceptance_criteria",
        "discussion",
        "links",
        "metadata",
    }
    if set(evidence) - allowed:
        raise ValueError("unknown source evidence fields")
    admin = TeamAdminService(service.repository)
    values = dict(evidence)
    required = {"provider_type", "source_type", "external_identifier", "external_version"}
    if not required.issubset(values):
        raise ValueError("source evidence requires provider, source type, identifier and version")
    values.setdefault("fetched_at", datetime.now(UTC))
    values.setdefault("expires_at", None)
    values.setdefault("access_classification", "internal")
    values.setdefault("freshness_status", "unknown")
    identity = (
        team_id,
        project_id,
        values["provider_type"],
        values["source_type"],
        values["external_identifier"],
        values["external_version"],
    )
    for previous in state.external_source_evidence_snapshots.values():
        if identity == (
            previous.team_id,
            previous.project_id,
            previous.provider_type,
            previous.source_type,
            previous.external_identifier,
            previous.external_version,
        ):
            # Reuse capture metadata on retry; the domain service still checks content identity.
            values.update(
                fetched_at=previous.fetched_at,
                expires_at=previous.expires_at,
                freshness_status=previous.freshness_status,
            )
            break
    _, snapshot = admin.create_external_source_evidence_snapshot(
        **values,
        snapshot_id=None,
        team_id=team_id,
        project_id=project_id,
        case_ids=case_ids,
        task_ids=task_ids,
        created_by_actor="workflow:case-source-capture",
    )
    return {
        "success": True,
        "source_evidence_snapshot_id": snapshot.id,
        "content_digest": snapshot.content_digest,
    }


class RecordSourceEvidenceProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(RecordSourceEvidenceDefinition, definition)

    def execute(self, *, resolved_config=None):
        values = _values(self.effect_definition, resolved_config)
        values["directory_path"] = scoped_effect_directory_path(
            self.runtime_context,
            values.get("directory_path"),
        )
        values.setdefault("project_id", None)
        for key in ("case_ids", "task_ids"):
            values[key] = _list(values.get(key))
        if isinstance(values.get("evidence"), str):
            values["evidence"] = json.loads(values["evidence"])
        return record_source(_service(self.runtime_context), **values)


def ado_evidence(item, *, external_identifier):
    fields = item.get("fields") or {}
    description = item.get("description") or fields.get("System.Description")
    criteria = item.get("acceptance_criteria") or fields.get(
        "Microsoft.VSTS.Common.AcceptanceCriteria"
    )
    comments = []
    for comment in item.get("comments") or []:
        body = comment.get("text") or comment.get("body")
        if not body:
            continue
        author = comment.get("author") or comment.get("created_by")
        if isinstance(author, dict):
            author = author.get("display_name") or author.get("displayName") or author.get("id")
        comments.append(
            {
                "external_id": str(comment["id"]) if comment.get("id") else None,
                "body": str(body),
                "author": str(author) if author else None,
                "occurred_at": comment.get("created_at")
                or comment.get("createdDate")
                or comment.get("created_date"),
                "metadata": {
                    "version": comment.get("version"),
                    "modified_at": comment.get("modified_at"),
                },
            }
        )
    source_url = item.get("web_url") or item.get("url")
    content = {
        "title": item.get("title") or fields.get("System.Title"),
        "body": {"content": str(description), "format": "html"} if description else None,
        "acceptance_criteria": [{"text": str(criteria)}] if criteria else [],
        "discussion": comments,
        "links": [{"url": source_url, "relation": "source"}] if source_url else [],
        "metadata": {
            "discussion_capture": item.get("comments_paging")
            or {
                "complete": False,
                "reason": "pagination_not_reported",
            }
        },
    }
    return {
        "provider_type": "azure_devops",
        "source_type": "work_item",
        "external_identifier": external_identifier,
        "external_version": f"{item.get('rev') or fields.get('System.Rev') or 'capture'}:{digest(content)}",
        "freshness_status": "current",
        "access_classification": "internal",
        **content,
    }


def ado_case_binding(source_ref):
    """Only structured identities, never a trailing numeric ID alone."""
    parts = str(source_ref).split(":")
    if len(parts) == 4 and parts[0] == "ado" and all(parts[1:]):
        org, project, item_id = parts[1:]
    else:
        url = urlparse(str(source_ref))
        path = [unquote(p) for p in url.path.strip("/").split("/")]
        if (
            url.scheme != "https"
            or url.hostname != "dev.azure.com"
            or len(path) != 5
            or path[2:4] != ["_workitems", "edit"]
        ):
            raise ValueError(
                "case source must be ado:organization:project:id or an ADO work-item URL"
            )
        org, project, item_id = path[0], path[1], path[-1]
    if not item_id.isdigit():
        raise ValueError("ADO work item ID must be numeric")
    return org, project, item_id


class CaptureAdoCaseSourceProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(CaptureAdoCaseSourceDefinition, definition)

    def execute(self, *, resolved_config=None):
        from evander.issues.service import IssueService

        values = _values(self.effect_definition, resolved_config)
        path = scoped_effect_directory_path(self.runtime_context, values.get("directory_path"))
        service = _service(self.runtime_context)
        state = service.repository.read()
        access = CaseContextAccess(state, directory_path=path)
        if not access.case_visible(values["case_id"]):
            raise PermissionError("case is outside workflow scope")
        case = state.cases[values["case_id"]]
        if case.source != "azure_devops" or not case.source_ref:
            raise ValueError("ADO capture requires an ADO-attributed case")
        org, project, item_id = ado_case_binding(case.source_ref)
        if (
            item_id != str(values["work_item_id"])
            or project.casefold() != str(values["ado_project"]).casefold()
        ):
            raise ValueError("ADO work item does not match the case source")
        try:
            item = IssueService(
                self.runtime_context.provider_config_resolver
            ).get_remote_backlog_item_by_id(
                provider="azure_devops",
                directory_path=path,
                backlog_item_id=str(values["work_item_id"]),
                project=str(values["ado_project"]),
                include_comments=True,
                include_relations=True,
                include_updates=False,
                comment_page_limit=10,
            )
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="json")
            actual_url = urlparse(item.get("organization_url") or "")
            actual_org = (
                actual_url.path.strip("/")
                if actual_url.hostname == "dev.azure.com"
                else (
                    (actual_url.hostname or "").removesuffix(".visualstudio.com")
                    if (actual_url.hostname or "").endswith(".visualstudio.com")
                    else ""
                )
            )
            if (
                actual_org.casefold() != org.casefold()
                or str(item.get("id")) != item_id
                or str(item.get("project")).casefold() != project.casefold()
            ):
                raise ValueError("captured ADO identity does not match the case source")
        except Exception as exc:
            # Keep errors useful without exposing connector credentials or HTTP bodies.
            error_type = type(exc).__name__

            def failed(current):
                current.cases[case.id].metadata["case_source_capture"] = {
                    "status": "unavailable",
                    "error_type": error_type,
                    "checked_at": datetime.now(UTC).isoformat(),
                }

            service.repository.update(failed)
            return {
                "success": True,
                "capture_status": "unavailable",
                "warning": f"ADO capture failed ({error_type}); prior evidence retained",
                "source_evidence_snapshot_ids": list(case.source_evidence_snapshot_ids),
            }
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        result = record_source(
            service,
            directory_path=path,
            team_id=case.team_id,
            project_id=case.project_id,
            case_ids=[case.id],
            task_ids=[],
            evidence=ado_evidence(item, external_identifier=case.source_ref),
        )

        def captured(current):
            current.cases[case.id].metadata["case_source_capture"] = {
                "status": "captured",
                "source_id": result["source_evidence_snapshot_id"],
            }

        service.repository.update(captured)
        return {**result, "capture_status": "captured"}


class RefreshCaseSourcesProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(RefreshCaseSourcesDefinition, definition)

    def execute(self, *, resolved_config=None):
        values = _values(self.effect_definition, resolved_config)
        path = scoped_effect_directory_path(self.runtime_context, values.get("directory_path"))
        state = _service(self.runtime_context).repository.read()
        case_ids = _list(values["case_ids"])
        if len(case_ids) > 100:
            raise ValueError("source refresh exceeds 100 cases")
        access = CaseContextAccess(state, directory_path=path)
        if any(not access.case_visible(cid) for cid in case_ids):
            raise PermissionError("case is outside workflow scope")
        results = []
        for cid in case_ids:
            case = state.cases[cid]
            if case.source != "azure_devops":
                results.append({"case_id": cid, "capture_status": "unsupported"})
                continue
            _, project, item_id = ado_case_binding(case.source_ref)
            definition = CaptureAdoCaseSourceDefinition(
                id="capture-case-source",
                type="effect:team:capture-ado-case-source",
                display_name="Capture case source",
                case_id=cid,
                ado_project=project,
                work_item_id=item_id,
                directory_path=path,
            )
            provider = CaptureAdoCaseSourceProvider()
            provider.initialize(definition, self.runtime_context)
            result = provider.execute()
            results.append({"case_id": cid, **result})
        return {"success": True, "case_ids": case_ids, "captures": results}


CONTEXT_DEFINITIONS = {
    "query:team:case-context": CaseContextQueryDefinition,
    "query:team:case-context-snapshot": CaseContextSnapshotQueryDefinition,
    "effect:team:snapshot-case-context": CaseContextSnapshotEffectDefinition,
    "effect:team:record-source-evidence": RecordSourceEvidenceDefinition,
    "effect:team:capture-ado-case-source": CaptureAdoCaseSourceDefinition,
    "effect:team:refresh-case-sources": RefreshCaseSourcesDefinition,
}
CONTEXT_QUERY_PROVIDERS = {
    "query:team:case-context": CaseContextQueryProvider,
    "query:team:case-context-snapshot": CaseContextSnapshotQueryProvider,
}
CONTEXT_EFFECT_PROVIDERS = {
    "effect:team:snapshot-case-context": CaseContextSnapshotEffectProvider,
    "effect:team:record-source-evidence": RecordSourceEvidenceProvider,
    "effect:team:capture-ado-case-source": CaptureAdoCaseSourceProvider,
    "effect:team:refresh-case-sources": RefreshCaseSourcesProvider,
}
CONTEXT_OUTPUTS = {
    "success": {"type": "boolean"},
    "text": {"type": "string"},
    "context": {"type": "object"},
    "sections": {"type": "array"},
    "omissions": {"type": "array"},
    "complete": {"type": "boolean"},
    "snapshot_id": {"type": "string"},
    "content_digest": {"type": "string"},
    "case_ids": {"type": "array"},
    "revision": {"type": "integer"},
    "evidence": {"type": "array"},
    "next_cursor": {"type": "integer"},
    "source_evidence_snapshot_id": {"type": "string"},
    "warning": {"type": "string"},
    "capture_status": {"type": "string"},
    "captures": {"type": "array"},
    "source_evidence_snapshot_ids": {"type": "array"},
}


def context_catalog(entry, field):
    rows = []
    for node_type, model in CONTEXT_DEFINITIONS.items():
        base_fields = set(QueryDefinition.model_fields) | set(EffectDefinition.model_fields)
        fields = []
        for name, definition in model.model_fields.items():
            if name in base_fields:
                continue
            array = name.endswith("_ids")
            kind = (
                "json"
                if array or name == "evidence"
                else ("number" if name in {"view_version", "cursor", "limit"} else "string")
            )
            kwargs = {
                "required": definition.is_required(),
                "description": name.replace("_", " ").capitalize() + ".",
            }
            if kind == "json":
                kwargs["json_value_type"] = "array" if array else "object"
            if not definition.is_required() and definition.default is not None:
                kwargs["default_value"] = definition.default
            fields.append(field(name, name.replace("_", " ").capitalize(), kind, **kwargs))
        rows.append(
            entry(
                node_type.split(":")[0],
                node_type,
                node_type.split(":")[-1].replace("-", " ").capitalize(),
                "Scoped case context and durable source evidence.",
                fields,
            )
        )
    return tuple(rows)
