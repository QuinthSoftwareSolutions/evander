from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

    from evander.admin.payloads import (
        WorkflowEditorCatalogPayload,
        WorkflowEditorGeneratedDraftPayload,
        WorkflowEditorGraphMetadata,
    )

_WORKFLOW_GENERATION_COOLDOWN_SECONDS = 10.0
_SENSITIVE_PAYLOAD_MARKERS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "pat",
    "authorization",
    "cookie",
    "private_key",
    "ssh_key",
)


@dataclass(slots=True)
class WorkflowGenerationAdminDependencies:
    config: Any
    runtime: Any
    workflow_config_admin: Any
    normalize_directory_reference: Callable[[str | None], str]
    read_assistant_settings: Callable[[], dict[str, object]]
    workflow_generation_transport: "httpx.AsyncBaseTransport | None" = None
    workflow_generation_transport_provider: (
        Callable[[], "httpx.AsyncBaseTransport | None"] | None
    ) = None


class WorkflowGenerationAdminService:
    """Workflow draft generation operations."""

    def __init__(self, dependencies: WorkflowGenerationAdminDependencies) -> None:
        self.config = dependencies.config
        self.runtime = dependencies.runtime
        self.workflow_generation_transport = dependencies.workflow_generation_transport
        self._workflow_config_admin = dependencies.workflow_config_admin
        self._normalize_directory_reference = dependencies.normalize_directory_reference
        self._read_assistant_settings = dependencies.read_assistant_settings
        self._workflow_generation_transport_provider = (
            dependencies.workflow_generation_transport_provider
        )
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
        normalized_description = str(description or "").strip()
        if not normalized_description:
            raise ValueError("description is required")
        normalized_directory = self._normalize_directory_reference(directory_path)
        settings = self._read_assistant_settings()
        model = str(
            settings.get("workflow_generation_model") or settings.get("default_model") or ""
        ).strip()
        if not model:
            raise ValueError(
                "Workflow generation requires assistant_settings.workflow_generation_model "
                "or assistant_settings.default_model."
            )

        from evander.provider_config import ProviderConfigResolver
        from evander.repositories.session_manifests import as_session_manifest_repository

        provider_config = ProviderConfigResolver(
            self.config,
            directory_store=self.runtime.directories,
            session_store=as_session_manifest_repository(self.runtime.session_repository),
        ).resolve_for_directory(normalized_directory)
        api_key = os.environ.get(provider_config.openai_api_key_env)
        if not api_key:
            raise ValueError(
                f"Host environment variable {provider_config.openai_api_key_env} is not set."
            )

        catalog = self._workflow_config_admin._read_workflow_editor_catalog(
            normalized_directory
        )
        request_payload = self._workflow_generation_request_payload(
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
                transport=self._current_workflow_generation_transport(),
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
        generated_text = self._workflow_generation_response_text(response_payload)
        try:
            generated_payload = json.loads(generated_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"OpenAI workflow generation returned invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(generated_payload, dict):
            raise ValueError("OpenAI workflow generation response must be a JSON object.")

        config, layout, graphs, warnings = self._workflow_generation_materialize_payload(
            generated_payload,
            directory_path=normalized_directory,
            catalog=catalog,
        )
        validation = self._workflow_config_admin._validate_workflow_editor_document(
            config=config,
            layout=layout,
            graphs=[graph.model_dump(mode="json") for graph in graphs],
            base_config={},
        )
        from evander.admin.payloads import WorkflowEditorGeneratedDraftPayload

        return WorkflowEditorGeneratedDraftPayload(
            model=model,
            config=validation.config,
            layout=validation.layout,
            graphs=validation.graphs,
            output_schemas=validation.output_schemas,
            validation_diagnostics=validation.validation_diagnostics,
            warnings=warnings,
        )

    def _current_workflow_generation_transport(self):
        if self._workflow_generation_transport_provider is not None:
            return self._workflow_generation_transport_provider()
        return self.workflow_generation_transport

    def _workflow_generation_request_payload(
        self,
        *,
        description: str,
        directory_path: str,
        catalog: "WorkflowEditorCatalogPayload",
        include_current_packs: bool,
        model: str,
    ) -> dict[str, object]:
        context = {
            "directory_path": directory_path,
            "node_catalog": self._workflow_generation_catalog_context(catalog),
            "examples": self._workflow_generation_examples() if include_current_packs else [],
            "rules": [
                "Return one connected workflow graph unless the user explicitly asks for multiple.",
                "Every node id must be stable, lowercase, unique, and easy to read.",
                "Use only node types listed in node_catalog.",
                "Trigger nodes must not have inbound edges.",
                "Avoid cycles unless a listed output port explicitly models a loop body.",
                "Use binding expressions like ${{node_id.output.path}} only for upstream nodes.",
                "Do not invent secrets, tokens, passwords, phone numbers, or private endpoints.",
                "Prefer placeholders for operator-specific values that the user can edit later.",
            ],
        }
        user_payload = {
            "workflow_description": description,
            "generation_context": context,
        }
        return {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You generate safe draft workflow graphs for the Evander "
                                "Admin Web workflow editor. The draft is preview-only and will be "
                                "validated before it can be saved. Return only data that conforms "
                                "to the supplied schema."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(user_payload, sort_keys=True),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "workflow_graph_draft",
                    "strict": True,
                    "schema": self._workflow_generation_response_schema(catalog),
                }
            },
            "temperature": 0.2,
            "max_output_tokens": 12000,
        }

    def _workflow_generation_response_schema(
        self, catalog: "WorkflowEditorCatalogPayload"
    ) -> dict[str, object]:
        node_types = sorted({entry.type for entry in catalog.nodes})
        node_type_schema: dict[str, object] = {"type": "string"}
        if node_types:
            node_type_schema["enum"] = node_types
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["graph", "nodes", "edges", "warnings"],
            "properties": {
                "graph": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "name",
                        "description",
                        "directory_path",
                        "directory_paths",
                        "user_ids",
                    ],
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": ["string", "null"]},
                        "directory_path": {"type": "string"},
                        "directory_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Legacy compatibility field; one directory only.",
                        },
                        "user_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "id",
                            "kind",
                            "type",
                            "display_name",
                            "config_json",
                        ],
                        "properties": {
                            "id": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "enum": ["trigger", "gate", "effect", "query"],
                            },
                            "type": node_type_schema,
                            "display_name": {"type": "string"},
                            "config_json": {
                                "type": "string",
                                "description": (
                                    "JSON object string containing node fields other than "
                                    "id, display_name, and type."
                                ),
                            },
                        },
                    },
                },
                "edges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "source_id",
                            "source_port",
                            "target_id",
                            "allow_cycle",
                        ],
                        "properties": {
                            "source_id": {"type": "string"},
                            "source_port": {"type": ["string", "null"]},
                            "target_id": {"type": "string"},
                            "allow_cycle": {"type": "boolean"},
                        },
                    },
                },
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
        }

    def _workflow_generation_catalog_context(
        self, catalog: "WorkflowEditorCatalogPayload"
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for entry in catalog.nodes:
            fields: list[dict[str, object]] = []
            for field in entry.fields:
                field_payload: dict[str, object] = {
                    "key": field.key,
                    "kind": field.kind,
                    "required": field.required,
                    "bindable": field.bindable,
                    "read_only": field.read_only,
                }
                if field.default_value is not None:
                    field_payload["default_value"] = field.default_value
                if field.options:
                    field_payload["options"] = [option.value for option in field.options[:50]]
                fields.append(field_payload)
            result.append(
                {
                    "kind": entry.kind,
                    "type": entry.type,
                    "label": entry.label,
                    "description": entry.description,
                    "fields": fields,
                    "input_ports": [port.key for port in entry.input_ports],
                    "output_ports": [port.key for port in entry.output_ports],
                }
            )
        return result

    def _workflow_generation_examples(self) -> list[dict[str, object]]:
        try:
            document = self._workflow_config_admin._read_composed_workflow_editor_document()
        except ValueError:
            return []
        nodes_by_id = self._workflow_config_admin._workflow_editor_nodes_by_id(document.config)
        examples: list[dict[str, object]] = []
        for graph in document.graphs[:6]:
            node_ids = set(graph.node_ids)
            nodes = [
                self._sanitize_workflow_generation_value(nodes_by_id[node_id])
                for node_id in graph.node_ids
                if node_id in nodes_by_id
            ]
            edges = [
                self._sanitize_workflow_generation_value(edge)
                for edge in document.config.get("edges", [])
                if isinstance(edge, dict)
                and edge.get("source_id") in node_ids
                and edge.get("target_id") in node_ids
            ]
            examples.append(
                {
                    "graph": self._sanitize_workflow_generation_value(
                        graph.model_dump(mode="json")
                    ),
                    "nodes": nodes,
                    "edges": edges,
                }
            )
        encoded = json.dumps(examples, sort_keys=True)
        if len(encoded) <= 24000:
            return examples
        trimmed: list[dict[str, object]] = []
        total = 0
        for example in examples:
            candidate_size = len(json.dumps(example, sort_keys=True))
            if trimmed and total + candidate_size > 24000:
                break
            trimmed.append(example)
            total += candidate_size
        return trimmed

    def _sanitize_workflow_generation_value(self, value: object) -> object:
        if isinstance(value, dict):
            result: dict[str, object] = {}
            for key, entry in value.items():
                key_text = str(key)
                if any(marker in key_text.lower() for marker in _SENSITIVE_PAYLOAD_MARKERS):
                    result[key_text] = "[redacted]"
                else:
                    result[key_text] = self._sanitize_workflow_generation_value(entry)
            return result
        if isinstance(value, list):
            return [self._sanitize_workflow_generation_value(entry) for entry in value]
        if isinstance(value, str) and len(value) > 1000:
            return f"{value[:1000]}..."
        return value

    def _workflow_generation_response_text(self, payload: object) -> str:
        if not isinstance(payload, dict):
            raise ValueError("OpenAI workflow generation response must be a JSON object.")
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text
        output = payload.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for content_item in content:
                    if not isinstance(content_item, dict):
                        continue
                    content_type = str(content_item.get("type") or "")
                    if content_type == "refusal":
                        refusal = str(content_item.get("refusal") or "").strip()
                        raise ValueError(
                            f"OpenAI workflow generation was refused{': ' + refusal if refusal else ''}."
                        )
                    text = content_item.get("text")
                    if content_type in {"output_text", "text"} and isinstance(text, str):
                        parts.append(text)
            joined = "".join(parts).strip()
            if joined:
                return joined
        raise ValueError("OpenAI workflow generation response did not include output text.")

    def _workflow_generation_materialize_payload(
        self,
        payload: dict[str, object],
        *,
        directory_path: str,
        catalog: "WorkflowEditorCatalogPayload",
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        list["WorkflowEditorGraphMetadata"],
        list[str],
    ]:
        from evander.admin.payloads import WorkflowEditorGraphMetadata

        catalog_by_type = {entry.type: entry for entry in catalog.nodes}
        nodes_payload = payload.get("nodes")
        if not isinstance(nodes_payload, list) or not nodes_payload:
            raise ValueError("Generated workflow must include at least one node.")
        config: dict[str, object] = {
            "triggers": [],
            "gates": [],
            "effects": [],
            "queries": [],
            "edges": [],
        }
        seen_node_ids: set[str] = set()
        ordered_generated_node_ids: list[str] = []
        for raw_node in nodes_payload:
            if not isinstance(raw_node, dict):
                raise ValueError("Generated workflow nodes must be JSON objects.")
            node_id = str(raw_node.get("id") or "").strip()
            display_name = str(raw_node.get("display_name") or "").strip()
            node_type = str(raw_node.get("type") or "").strip()
            if not node_id or not display_name or not node_type:
                raise ValueError("Generated workflow nodes require id, display_name, and type.")
            if node_id in seen_node_ids:
                raise ValueError(f"Generated workflow contains duplicate node id: {node_id}")
            seen_node_ids.add(node_id)
            ordered_generated_node_ids.append(node_id)
            catalog_entry = catalog_by_type.get(node_type)
            if catalog_entry is None:
                raise ValueError(f"Generated workflow used unknown node type: {node_type}")
            config_json = raw_node.get("config_json")
            if not isinstance(config_json, str):
                raise ValueError(f"Generated workflow node {node_id} must include config_json.")
            try:
                node_config = json.loads(config_json or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Generated workflow node {node_id} has invalid config_json: {exc.msg}"
                ) from exc
            if not isinstance(node_config, dict):
                raise ValueError(
                    f"Generated workflow node {node_id} config_json must decode to an object."
                )
            for envelope_key in ("id", "display_name", "type"):
                node_config.pop(envelope_key, None)
            materialized_node = {
                "id": node_id,
                "display_name": display_name,
                "type": node_type,
                **node_config,
            }
            collection = {
                "trigger": "triggers",
                "gate": "gates",
                "effect": "effects",
                "query": "queries",
            }[catalog_entry.kind]
            config[collection].append(materialized_node)  # type: ignore[index]

        edges_payload = payload.get("edges")
        if not isinstance(edges_payload, list):
            raise ValueError("Generated workflow edges must be a JSON array.")
        seen_edges: set[tuple[str, str | None, str]] = set()
        for raw_edge in edges_payload:
            if not isinstance(raw_edge, dict):
                raise ValueError("Generated workflow edges must be JSON objects.")
            source_id = str(raw_edge.get("source_id") or "").strip()
            target_id = str(raw_edge.get("target_id") or "").strip()
            source_port_raw = raw_edge.get("source_port")
            source_port = (
                str(source_port_raw).strip()
                if isinstance(source_port_raw, str) and source_port_raw.strip()
                else None
            )
            if not source_id or not target_id:
                raise ValueError("Generated workflow edges require source_id and target_id.")
            if source_id not in seen_node_ids or target_id not in seen_node_ids:
                raise ValueError(
                    f"Generated workflow edge references unknown node: {source_id} -> {target_id}"
                )
            edge_key = (source_id, source_port, target_id)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edge: dict[str, object] = {
                "source_id": source_id,
                "target_id": target_id,
            }
            if source_port is not None:
                edge["source_port"] = source_port
            if bool(raw_edge.get("allow_cycle")):
                edge["allow_cycle"] = True
            config["edges"].append(edge)  # type: ignore[index]

        graph_payload = payload.get("graph")
        if not isinstance(graph_payload, dict):
            raise ValueError("Generated workflow graph metadata is required.")
        graph_id = str(graph_payload.get("id") or "").strip() or "generated-workflow"
        graph_name = str(graph_payload.get("name") or "").strip() or "Generated workflow"
        graph_input = dict(graph_payload)
        if "directory_path" not in graph_input and "directory_paths" not in graph_input:
            graph_input["directory_path"] = directory_path
        graph_input["node_ids"] = ordered_generated_node_ids
        graph = WorkflowEditorGraphMetadata.model_validate(
            {
                "id": graph_id,
                "name": graph_name,
                "description": str(graph_payload.get("description") or "").strip() or None,
                **graph_input,
            }
        )
        warnings_payload = payload.get("warnings")
        warnings = (
            [str(warning).strip() for warning in warnings_payload if str(warning).strip()]
            if isinstance(warnings_payload, list)
            else []
        )
        layout = self._workflow_generation_layout(config)
        return config, layout, [graph], warnings

    def _workflow_generation_layout(self, config: dict[str, object]) -> dict[str, object]:
        nodes_by_id = self._workflow_config_admin._workflow_editor_nodes_by_id(config)
        ordered_node_ids = list(nodes_by_id)
        inbound_count = {node_id: 0 for node_id in ordered_node_ids}
        outbound: dict[str, list[str]] = {node_id: [] for node_id in ordered_node_ids}
        edges = config.get("edges")
        if isinstance(edges, list):
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                source_id = str(edge.get("source_id") or "").strip()
                target_id = str(edge.get("target_id") or "").strip()
                if source_id not in nodes_by_id or target_id not in nodes_by_id:
                    continue
                outbound[source_id].append(target_id)
                inbound_count[target_id] += 1
        initial_queue = [
            node_id for node_id in ordered_node_ids if inbound_count[node_id] == 0
        ] or ordered_node_ids[:1]
        queue = deque(initial_queue)
        queued_node_ids = set(initial_queue)
        processed_node_ids: set[str] = set()
        levels: dict[str, int] = {}
        while len(processed_node_ids) < len(ordered_node_ids):
            if not queue:
                next_node_id = next(
                    (node_id for node_id in ordered_node_ids if node_id not in processed_node_ids),
                    None,
                )
                if next_node_id is None:
                    break
                queue.append(next_node_id)
                queued_node_ids.add(next_node_id)
            node_id = queue.popleft()
            queued_node_ids.discard(node_id)
            if node_id in processed_node_ids:
                continue
            processed_node_ids.add(node_id)
            level = levels.get(node_id, 0)
            for target_id in outbound.get(node_id, []):
                if target_id in processed_node_ids:
                    continue
                levels[target_id] = max(levels.get(target_id, 0), level + 1)
                inbound_count[target_id] -= 1
                if inbound_count[target_id] <= 0 and target_id not in queued_node_ids:
                    queue.append(target_id)
                    queued_node_ids.add(target_id)
        for node_id in ordered_node_ids:
            levels.setdefault(node_id, 0)
        rows_by_level: dict[int, int] = {}
        layout_nodes: dict[str, dict[str, float]] = {}
        for node_id in ordered_node_ids:
            level = levels[node_id]
            row = rows_by_level.get(level, 0)
            rows_by_level[level] = row + 1
            layout_nodes[node_id] = {
                "x": float(120 + level * 300),
                "y": float(100 + row * 180),
            }
        return {"nodes": layout_nodes}
