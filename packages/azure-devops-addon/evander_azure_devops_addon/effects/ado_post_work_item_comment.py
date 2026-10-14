from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import html
import json
from pathlib import Path
import re
from typing import Any

from evander.ado_mentions import normalize_ado_mention_refs
from evander.issues.service import IssueService
from evander.storage import JsonObjectStore

from evander.effects.providers.base import EffectProviderBase


class AdoPostWorkItemCommentEffectProvider(EffectProviderBase):
    def initialize(self, effect_definition, runtime_context):
        from evander.optional_integrations import require_azure_devops_module
        resolver = runtime_context.provider_config_resolver
        config = getattr(resolver, "config", None) or runtime_context.resolved_config
        require_azure_devops_module(config=config)
        super().initialize(effect_definition, runtime_context)

    """Post an ADO work-item comment with optional remote-reconciled idempotency."""

    NODE_TYPE = "effect:ado:post-work-item-comment"
    _IDEMPOTENCY_STORE_FILENAME = "ado-work-item-comment-idempotency.json"
    _MARKER_PATTERN = re.compile(
        r"<!--\s*evander:ado-work-item-comment:v1:key=([0-9a-f]{64});"
        r"payload=([0-9a-f]{64})\s*-->"
    )

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = self._require_non_empty_string(
            payload.get("directory_path"), field_name="directory_path"
        )
        project = self._require_non_empty_string(payload.get("project"), field_name="project")
        work_item_id = self._require_non_empty_string(
            payload.get("work_item_id"), field_name="work_item_id"
        )
        comment = self._require_non_empty_string(payload.get("comment"), field_name="comment")
        mentions = normalize_ado_mention_refs(payload.get("mentions"))
        idempotency_key = self._normalize_optional_string(payload.get("idempotency_key"))

        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError(
                f"{self.NODE_TYPE} provider_config_resolver is not available"
            )
        issue_service = IssueService(context.provider_config_resolver)
        if idempotency_key is not None:
            target_scope = self._target_scope(
                directory_path=directory_path,
                project=project,
                work_item_id=work_item_id,
            )
            return self._execute_idempotent(
                issue_service=issue_service,
                target_scope=target_scope,
                directory_path=directory_path,
                project=project,
                work_item_id=work_item_id,
                comment=comment,
                mentions=mentions,
                idempotency_key=idempotency_key,
            )

        result = issue_service.post_remote_comment(
            provider="azure_devops",
            remote_issue_id=work_item_id,
            directory_path=directory_path,
            project=project,
            comment=comment,
            mentions=mentions or None,
        )
        return self._output_from_result(
            result=result,
            work_item_id=work_item_id,
            status="posted",
            idempotency_key=None,
            correlation_marker=None,
        )

    def _execute_idempotent(
        self,
        *,
        issue_service: IssueService,
        target_scope: list[str],
        directory_path: str,
        project: str,
        work_item_id: str,
        comment: str,
        mentions: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        target_key = self._digest([*target_scope, idempotency_key])
        request_digest = self._digest(
            {
                "comment": comment,
                "mentions": [mention.casefold() for mention in mentions],
            }
        )
        correlation_marker = self._correlation_marker(
            target_key=target_key,
            request_digest=request_digest,
        )
        store = JsonObjectStore(
            self._runtime_dir() / self._IDEMPOTENCY_STORE_FILENAME,
            {"version": 1, "entries": {}},
        )

        def post_or_reuse(state: dict[str, Any]) -> dict[str, Any]:
            entries = state.get("entries")
            if state.get("version") != 1 or not isinstance(entries, dict):
                raise ValueError(
                    f"{self.NODE_TYPE} idempotency store has an unsupported format"
                )

            existing = entries.get(target_key)
            if target_key in entries:
                if not isinstance(existing, Mapping):
                    raise ValueError(
                        f"{self.NODE_TYPE} idempotency record has an unsupported format"
                    )
                self._require_matching_request_digest(
                    existing.get("request_digest"),
                    request_digest=request_digest,
                    idempotency_key=idempotency_key,
                )
                stored_output = existing.get("output")
                if not isinstance(stored_output, Mapping):
                    raise ValueError(
                        f"{self.NODE_TYPE} idempotency record has no reusable output"
                    )
                return self._deduplicated_output(dict(stored_output), work_item_id=work_item_id)

            remote_issue = issue_service.read_remote_issue(
                provider="azure_devops",
                remote_issue_id=work_item_id,
                directory_path=directory_path,
                project=project,
                include_comments=True,
            )
            matched_comment = self._find_correlated_comment(
                remote_issue,
                target_key=target_key,
                request_digest=request_digest,
                idempotency_key=idempotency_key,
            )
            if matched_comment is not None:
                resolved_mentions = (
                    issue_service.resolve_ado_comment_mentions(
                        directory_path=directory_path,
                        mentions=mentions,
                    )
                    if mentions
                    else []
                )
                result = {
                    "provider": "azure_devops",
                    "remote_issue_id": work_item_id,
                    "comment": matched_comment,
                    "resolved_mentions": resolved_mentions,
                }
                output = self._output_from_result(
                    result=result,
                    work_item_id=work_item_id,
                    status="deduplicated",
                    idempotency_key=idempotency_key,
                    correlation_marker=correlation_marker,
                )
            else:
                result = issue_service.post_remote_comment(
                    provider="azure_devops",
                    remote_issue_id=work_item_id,
                    directory_path=directory_path,
                    project=project,
                    comment=comment,
                    mentions=mentions or None,
                    correlation_marker=correlation_marker,
                )
                output = self._output_from_result(
                    result=result,
                    work_item_id=work_item_id,
                    status="posted",
                    idempotency_key=idempotency_key,
                    correlation_marker=correlation_marker,
                )

            entries[target_key] = {
                "request_digest": request_digest,
                "correlation_marker": correlation_marker,
                "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "output": output,
            }
            return output

        output = store.update_object(post_or_reuse)
        if not isinstance(output, dict):
            raise RuntimeError(f"{self.NODE_TYPE} did not produce an output")
        return output

    @classmethod
    def _find_correlated_comment(
        cls,
        remote_issue: Mapping[str, Any],
        *,
        target_key: str,
        request_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        comments = remote_issue.get("comments")
        if not isinstance(comments, list):
            return None
        matched_comment: dict[str, Any] | None = None
        for raw_comment in comments:
            if not isinstance(raw_comment, Mapping):
                continue
            values = [
                html.unescape(str(raw_comment.get(field_name) or ""))
                for field_name in ("text", "html", "raw_text")
            ]
            markers = {
                match.groups()
                for value in values
                for match in cls._MARKER_PATTERN.finditer(value)
            }
            for marker_target_key, marker_request_digest in markers:
                if marker_target_key != target_key:
                    continue
                if marker_request_digest != request_digest:
                    raise ValueError(
                        f"{cls.NODE_TYPE} idempotency key {idempotency_key!r} was already "
                        "used with a different comment or mention set"
                    )
                matched_comment = dict(raw_comment)
        return matched_comment

    @classmethod
    def _require_matching_request_digest(
        cls,
        stored_digest: Any,
        *,
        request_digest: str,
        idempotency_key: str,
    ) -> None:
        if stored_digest != request_digest:
            raise ValueError(
                f"{cls.NODE_TYPE} idempotency key {idempotency_key!r} was already used "
                "with a different comment or mention set"
            )

    @staticmethod
    def _deduplicated_output(
        stored_output: dict[str, Any], *, work_item_id: str
    ) -> dict[str, Any]:
        stored_output["status"] = "deduplicated"
        stored_output["message"] = f"Reused comment on Azure DevOps work item {work_item_id}"
        return stored_output

    @classmethod
    def _correlation_marker(cls, *, target_key: str, request_digest: str) -> str:
        return (
            "<!-- evander:ado-work-item-comment:v1:"
            f"key={target_key};payload={request_digest} -->"
        )

    @staticmethod
    def _digest(value: Any) -> str:
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _runtime_dir(self) -> Path:
        return Path(self._context().resolved_config.runtime_dir)

    def _target_scope(
        self, *, directory_path: str, project: str, work_item_id: str
    ) -> list[str]:
        resolver = self._context().provider_config_resolver
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if not callable(resolve_for_directory):
            raise RuntimeError(f"{self.NODE_TYPE} cannot resolve its Azure DevOps target")
        provider_config = resolve_for_directory(directory_path)
        organization_url = self._require_non_empty_string(
            getattr(provider_config, "azure_devops_organization_url", None),
            field_name="azure_devops_organization_url",
        )
        return [
            "azure_devops",
            organization_url.rstrip("/").casefold(),
            project.casefold(),
            work_item_id,
        ]

    def _output_from_result(
        self,
        *,
        result: Mapping[str, Any],
        work_item_id: str,
        status: str,
        idempotency_key: str | None,
        correlation_marker: str | None,
    ) -> dict[str, Any]:
        comment_payload = (
            result.get("comment") if isinstance(result.get("comment"), Mapping) else {}
        )
        return {
            "success": True,
            "status": status,
            "provider": str(result.get("provider") or "azure_devops"),
            "work_item_id": str(result.get("remote_issue_id") or work_item_id),
            "comment_id": self._normalize_optional_string(comment_payload.get("id")),
            "comment_url": self._normalize_optional_string(comment_payload.get("url")),
            "comment": dict(comment_payload),
            "resolved_mentions": list(result.get("resolved_mentions") or []),
            "idempotency_key": idempotency_key,
            "correlation_marker": correlation_marker,
            "message": (
                f"Posted comment to Azure DevOps work item {work_item_id}"
                if status == "posted"
                else f"Reused comment on Azure DevOps work item {work_item_id}"
            ),
        }

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "directory_path",
            "project",
            "work_item_id",
            "comment",
            "mentions",
            "idempotency_key",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AdoPostWorkItemCommentEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _require_non_empty_string(value: Any, *, field_name: str) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"{AdoPostWorkItemCommentEffectProvider.NODE_TYPE} requires {field_name}")
        return text

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
