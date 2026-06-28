from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from agent_sandbox.admin.service import (
        WorkflowEditorCatalogPayload,
        WorkflowEditorGeneratedDraftPayload,
    )

_WORKFLOW_GENERATION_COOLDOWN_SECONDS = 10.0


class _WorkflowGenerationAdminServiceHost(Protocol):
    config: Any
    runtime: Any
    workflow_generation_transport: Any

    def _normalize_directory_reference(self, directory_path: str | None) -> str: ...
    def read_assistant_settings(self) -> dict[str, object]: ...
    def _read_workflow_editor_catalog(
        self, directory_path: str
    ) -> "WorkflowEditorCatalogPayload": ...
    def _workflow_generation_request_payload(
        self,
        *,
        description: str,
        directory_path: str,
        catalog: "WorkflowEditorCatalogPayload",
        include_current_packs: bool,
        model: str,
    ) -> dict[str, object]: ...
    def _workflow_generation_response_text(self, payload: object) -> str: ...
    def _workflow_generation_materialize_payload(
        self,
        payload: dict[str, object],
        *,
        directory_path: str,
        catalog: "WorkflowEditorCatalogPayload",
    ) -> tuple[dict[str, object], dict[str, object], list[Any], list[str]]: ...
    def _validate_workflow_editor_document(self, **kwargs: Any) -> Any: ...


class WorkflowGenerationAdminService:
    """Workflow draft generation operations extracted from AdminService."""

    def __init__(self, host: _WorkflowGenerationAdminServiceHost) -> None:
        self._host = host
        self._workflow_generation_lock = threading.Lock()
        self._workflow_generation_in_flight = False
        self._workflow_generation_last_finished_at_monotonic: float | None = None

    async def generate_workflow_editor_draft(
        self,
        *,
        description: str,
        directory_path: str | None = None,
        include_current_packs: bool = True,
    ) -> "WorkflowEditorGeneratedDraftPayload":
        return await self._generate_workflow_editor_draft(
            description=description,
            directory_path=directory_path,
            include_current_packs=include_current_packs,
        )

    def _begin_workflow_generation_request(self) -> None:
        now = time.monotonic()
        with self._workflow_generation_lock:
            if self._workflow_generation_in_flight:
                raise ValueError(
                    "Workflow generation is already in progress. Wait for it to finish "
                    "before generating another draft."
                )
            last_finished = self._workflow_generation_last_finished_at_monotonic
            if last_finished is not None:
                remaining_seconds = _WORKFLOW_GENERATION_COOLDOWN_SECONDS - (now - last_finished)
                if remaining_seconds > 0:
                    raise ValueError(
                        "Workflow generation is cooling down. Try again in "
                        f"{math.ceil(remaining_seconds)} seconds."
                    )
            self._workflow_generation_in_flight = True

    def _finish_workflow_generation_request(self) -> None:
        with self._workflow_generation_lock:
            self._workflow_generation_in_flight = False
            self._workflow_generation_last_finished_at_monotonic = time.monotonic()

    async def _generate_workflow_editor_draft(
        self,
        *,
        description: str,
        directory_path: str | None = None,
        include_current_packs: bool = True,
    ) -> "WorkflowEditorGeneratedDraftPayload":
        host = self._host
        normalized_description = str(description or "").strip()
        if not normalized_description:
            raise ValueError("description is required")
        normalized_directory = host._normalize_directory_reference(directory_path)
        settings = host.read_assistant_settings()
        model = str(
            settings.get("workflow_generation_model") or settings.get("default_model") or ""
        ).strip()
        if not model:
            raise ValueError(
                "Workflow generation requires assistant_settings.workflow_generation_model "
                "or assistant_settings.default_model."
            )

        from agent_sandbox.provider_config import ProviderConfigResolver
        from agent_sandbox.repositories.session_manifests import as_session_manifest_repository

        provider_config = ProviderConfigResolver(
            host.config,
            directory_store=host.runtime.directories,
            session_store=as_session_manifest_repository(host.runtime.session_repository),
        ).resolve_for_directory(normalized_directory)
        api_key = os.environ.get(provider_config.openai_api_key_env)
        if not api_key:
            raise ValueError(
                f"Host environment variable {provider_config.openai_api_key_env} is not set."
            )

        catalog = host._read_workflow_editor_catalog(normalized_directory)
        request_payload = host._workflow_generation_request_payload(
            description=normalized_description,
            directory_path=normalized_directory,
            catalog=catalog,
            include_current_packs=include_current_packs,
            model=model,
        )
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = f"{provider_config.openai_api_base_url.rstrip('/')}/responses"
        import httpx

        self._begin_workflow_generation_request()
        try:
            async with httpx.AsyncClient(
                timeout=provider_config.openai_relay_timeout_seconds,
                transport=host.workflow_generation_transport,
            ) as client:
                try:
                    response = await client.post(url, headers=headers, json=request_payload)
                except httpx.HTTPError as exc:
                    raise ValueError(f"OpenAI workflow generation request failed: {exc}") from exc
        finally:
            self._finish_workflow_generation_request()
        if response.status_code >= 400:
            detail = response.text.strip()
            raise ValueError(
                f"OpenAI workflow generation failed with HTTP {response.status_code}"
                f"{': ' + detail[:500] if detail else ''}"
            )
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ValueError("OpenAI workflow generation returned non-JSON response.") from exc
        generated_text = host._workflow_generation_response_text(response_payload)
        try:
            generated_payload = json.loads(generated_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"OpenAI workflow generation returned invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(generated_payload, dict):
            raise ValueError("OpenAI workflow generation response must be a JSON object.")

        config, layout, graphs, warnings = host._workflow_generation_materialize_payload(
            generated_payload,
            directory_path=normalized_directory,
            catalog=catalog,
        )
        validation = host._validate_workflow_editor_document(
            config=config,
            layout=layout,
            graphs=[graph.model_dump(mode="json") for graph in graphs],
            base_config={},
        )
        from agent_sandbox.admin.service import WorkflowEditorGeneratedDraftPayload

        return WorkflowEditorGeneratedDraftPayload(
            model=model,
            config=validation.config,
            layout=validation.layout,
            graphs=validation.graphs,
            output_schemas=validation.output_schemas,
            validation_diagnostics=validation.validation_diagnostics,
            warnings=warnings,
        )
