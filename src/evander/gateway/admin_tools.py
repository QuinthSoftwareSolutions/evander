"""Admin/operator tool registration helpers for the MCP gateway."""

from __future__ import annotations

from typing import Any, Callable, Literal

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from evander.admin.composition import AdminRuntimeComponents
from evander.bootstrap import load_host_bootstrap_config
from evander.directory_config import DIRECTORY_CONFIG_DOMAIN_FILENAMES
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import McpToolName, ToolName
from evander.policy import PolicyError


def _admin_result_payload_or_raise(
    result: Any,
    action: str,
    payload_from_result: Callable[[Any], dict[str, Any] | None],
) -> dict[str, Any]:
    payload = payload_from_result(result) or {}
    if payload.get("ok", True):
        return payload
    errors = payload.get("errors")
    messages: list[str] = []
    if isinstance(errors, list):
        for error in errors:
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
                if message:
                    messages.append(message)
    detail = "; ".join(messages) or f"{action} failed"
    raise ValueError(detail)


def _assistant_require_writable_scope(session: Any, requested_scope: str) -> None:
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    mutation_policy = (
        metadata.get("assistant_mutation_policy") if isinstance(metadata, dict) else None
    )
    if not isinstance(mutation_policy, dict):
        raise PolicyError(
            f"Session {getattr(session, 'session_id', '<unknown>')} is missing metadata.assistant_mutation_policy"
        )
    raw_scopes = mutation_policy.get("writable_scopes")
    if not isinstance(raw_scopes, list):
        raise PolicyError(
            f"Session {getattr(session, 'session_id', '<unknown>')} has no assistant writable scopes"
        )
    normalized_scope = str(requested_scope or "").strip()
    allowed_scopes = [str(scope or "").strip() for scope in raw_scopes if str(scope or "").strip()]
    if normalized_scope not in allowed_scopes:
        raise PolicyError(
            f"Assistant mutation scope {normalized_scope!r} is not writable for session {getattr(session, 'session_id', '<unknown>')}"
        )


def register_admin_tools(
    server: Any,
    context: Any,
    deps: Any,
    *,
    transport_session_map: dict[str, str],
    audit_success: Callable[[str, McpToolName, dict[str, Any], dict[str, Any]], None],
    audit_failure: Callable[..., None],
    payload_from_result: Callable[[Any], dict[str, Any] | None],
) -> None:
    """Register admin tools with the MCP gateway server."""

    def _admin_runtime() -> AdminRuntimeComponents:
        return deps.admin_runtime_factory()

    @server.tool()
    def assistant_read_status(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id}
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_READ_STATUS
            )
            runtime = _admin_runtime()
            settings = runtime.operations_admin.read_assistant_settings()
            workflow_runs = runtime.workflow_config_admin.read_workflow_run_snapshot()
            result = {
                "assistant": {
                    "enabled": bool(settings.get("enabled")),
                    "read_only": bool(settings.get("read_only", True)),
                    "default_model": settings.get("default_model"),
                    "capability_profile": session.metadata.get("capability_profile"),
                },
                "session_id": session.session_id,
                "directory_path": session.directory_path,
                "active_workflow_runs": len(workflow_runs.runs),
            }
            audit_success(resolved_session_id, ToolName.ASSISTANT_READ_STATUS, args, result)
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_READ_STATUS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_READ_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_list_agents(
        backend: str | None = None,
        include_inactive: bool = True,
        directory_path: str | None = None,
        all_directories: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "backend": backend,
            "include_inactive": include_inactive,
            "directory_path": directory_path,
            "all_directories": all_directories,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_LIST_AGENTS
            )
            list_all_directories = bool(all_directories and not directory_path)
            target_directory = directory_path or session.directory_path
            records = _admin_runtime().vm_agent_admin.list_agents(
                directory_path=target_directory,
                direct_only=False,
                include_inactive=include_inactive,
                backend=backend,
                all_directories=list_all_directories,
                include_prompt_queue=False,
            )
            agents = [
                record.model_dump(mode="json", exclude={"prompt_queue"})
                if hasattr(record, "model_dump")
                else {key: value for key, value in dict(record).items() if key != "prompt_queue"}
                for record in records
            ]
            result = {
                "agents": agents,
                "count": len(agents),
                "directory_path": None if list_all_directories else target_directory,
                "all_directories": list_all_directories,
            }
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_LIST_AGENTS,
                args,
                {
                    "count": len(agents),
                    "directory_path": result["directory_path"],
                    "all_directories": list_all_directories,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_LIST_AGENTS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_LIST_AGENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_workflow_snapshot(
        include_runs: bool = False,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return the current workflow configuration snapshot."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id, "include_runs": include_runs}
        try:
            context.policy.require_tool(resolved_session_id, ToolName.GET_WORKFLOW_SNAPSHOT)
            runtime = _admin_runtime()
            document = runtime.workflow_config_admin.read_workflow_editor_document()
            result = document.model_dump(mode="json")
            if include_runs:
                runs = runtime.workflow_config_admin.read_workflow_run_snapshot()
                result["runs"] = runs.model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.GET_WORKFLOW_SNAPSHOT,
                args,
                {
                    "revision": document.revision,
                    "include_runs": include_runs,
                    "graph_count": len(document.graphs),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.GET_WORKFLOW_SNAPSHOT, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_WORKFLOW_SNAPSHOT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_inspect_workflows(
        include_runs: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id, "include_runs": include_runs}
        try:
            context.policy.require_tool(resolved_session_id, ToolName.ASSISTANT_INSPECT_WORKFLOWS)
            runtime = _admin_runtime()
            document = runtime.workflow_config_admin.read_workflow_editor_document()
            result = {
                "config_path": document.config_path,
                "revision": document.revision,
                "config": document.config,
            }
            if include_runs:
                runs = runtime.workflow_config_admin.read_workflow_run_snapshot()
                result["runs"] = runs.model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_INSPECT_WORKFLOWS,
                args,
                {"revision": document.revision, "include_runs": include_runs},
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_INSPECT_WORKFLOWS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_INSPECT_WORKFLOWS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_read_config(
        scope: Literal["directory", "global", "worker_global"] = "directory",
        domain: str | None = None,
        directory_path: str | None = None,
        worker_id: str | None = None,
        include_effective: bool = True,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read directory-scoped, assistant/global, or worker-global config."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "scope": scope,
            "domain": domain,
            "directory_path": directory_path,
            "worker_id": worker_id,
            "include_effective": include_effective,
        }

        def directory_snapshot(
            runtime: AdminRuntimeComponents,
            target_directory: str,
            target_domain: str,
        ) -> dict[str, Any]:
            operations = runtime.operations_admin
            snapshot: dict[str, Any] = {
                "scope": "directory",
                "domain": target_domain,
                "directory_path": target_directory,
            }
            if target_domain == "gateway":
                snapshot["raw"] = None
            else:
                raw_result = operations.read_directory_domain_config(
                    target_directory, target_domain
                )
                raw_payload = _admin_result_payload_or_raise(
                    raw_result, f"read directory config {target_domain!r}", payload_from_result
                )
                raw_data = raw_payload.get("data") if isinstance(raw_payload, dict) else None
                snapshot["raw"] = {
                    "exists": bool(raw_data.get("exists")) if isinstance(raw_data, dict) else False,
                    "config": raw_data.get("config") if isinstance(raw_data, dict) else None,
                    "result": raw_payload,
                }

            if include_effective or target_domain == "gateway":
                effective_result = operations.compute_directory_effective_config(
                    target_directory, target_domain
                )
                effective_payload = _admin_result_payload_or_raise(
                    effective_result,
                    f"compute effective directory config {target_domain!r}",
                    payload_from_result,
                )
                effective_data = (
                    effective_payload.get("data") if isinstance(effective_payload, dict) else None
                )
                resolution = (
                    effective_data.get("resolution") if isinstance(effective_data, dict) else None
                )
                effective_config = (
                    resolution.get("effective_config") if isinstance(resolution, dict) else None
                )
                snapshot["effective"] = {
                    "config": effective_config,
                    "resolution": resolution,
                    "result": effective_payload,
                }
                snapshot["validation_diagnostics"] = effective_payload.get(
                    "validation_diagnostics", []
                )
            return snapshot

        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_READ_CONFIG
            )
            runtime = _admin_runtime()
            operations = runtime.operations_admin
            workflow_config = runtime.workflow_config_admin
            normalized_scope = str(scope or "directory").strip() or "directory"
            normalized_domain = str(domain or "").strip()

            if normalized_scope == "global":
                target_domain = normalized_domain or "assistant_settings"
                if target_domain == "assistant_settings":
                    path = operations.assistant_settings_path()
                    result = {
                        "scope": "global",
                        "domain": target_domain,
                        "path": str(path),
                        "exists": path.exists(),
                        "config": operations.read_assistant_settings(),
                    }
                elif target_domain == "host":
                    host_config = load_host_bootstrap_config(runtime_dir=runtime.config.runtime_dir)
                    result = {
                        "scope": "global",
                        "domain": target_domain,
                        "path": str(host_config.runtime_dir / "host-config.json"),
                        "config": host_config.model_dump(mode="json"),
                    }
                elif target_domain == "gateway":
                    result = {
                        "scope": "global",
                        "domain": target_domain,
                        "config_kind": "effective_root_gateway",
                        "config": runtime.config.model_dump(mode="json"),
                    }
                else:
                    raise ValueError(
                        "Unsupported global config domain. Use one of: "
                        "assistant_settings, host, gateway."
                    )
                audit_success(
                    resolved_session_id,
                    ToolName.ASSISTANT_READ_CONFIG,
                    args,
                    {"scope": "global", "domain": target_domain},
                )
                return result

            if normalized_scope == "worker_global":
                target_worker_id = str(worker_id or normalized_domain or "").strip()
                if not target_worker_id:
                    raise ValueError("worker_id is required for worker_global config reads")
                payload = workflow_config.read_worker_global_config(target_worker_id).model_dump(
                    mode="json"
                )
                result = {"scope": "worker_global", **payload}
                audit_success(
                    resolved_session_id,
                    ToolName.ASSISTANT_READ_CONFIG,
                    args,
                    {
                        "scope": "worker_global",
                        "worker_id": target_worker_id,
                        "exists": payload.get("exists"),
                    },
                )
                return result

            if normalized_scope != "directory":
                raise ValueError(
                    "Unsupported config scope. Use one of: directory, global, worker_global."
                )

            target_directory = directory_path or session.directory_path
            if normalized_domain:
                result = directory_snapshot(runtime, target_directory, normalized_domain)
                audit_result = {
                    "scope": "directory",
                    "directory_path": target_directory,
                    "domain": normalized_domain,
                    "include_effective": include_effective,
                }
            else:
                snapshots = {
                    target_domain: directory_snapshot(
                        runtime,
                        target_directory,
                        target_domain,
                    )
                    for target_domain in sorted(DIRECTORY_CONFIG_DOMAIN_FILENAMES)
                }
                result = {
                    "scope": "directory",
                    "directory_path": target_directory,
                    "domains": list(snapshots),
                    "snapshots": snapshots,
                }
                audit_result = {
                    "scope": "directory",
                    "directory_path": target_directory,
                    "domain_count": len(snapshots),
                    "include_effective": include_effective,
                }
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_READ_CONFIG,
                args,
                audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_READ_CONFIG, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_READ_CONFIG,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_upsert_config(
        domain: str,
        payload: dict[str, Any],
        directory_path: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "domain": domain,
            "directory_path": directory_path,
            "after": payload,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_UPSERT_CONFIG
            )
            runtime = _admin_runtime()
            operations = runtime.operations_admin
            normalized_domain = str(domain or "").strip()
            if normalized_domain == "assistant_settings":
                _assistant_require_writable_scope(session, "config/assistant")
                before = operations.read_assistant_settings()
                after = operations.write_assistant_settings(payload)
                result = {
                    "status": "success",
                    "domain": normalized_domain,
                    "config": after,
                    "before_after_summary": {"before": before, "after": after},
                }
            else:
                _assistant_require_writable_scope(session, "config/directory")
                target_directory = directory_path or session.directory_path
                before_payload = operations.read_directory_domain_config(
                    target_directory, normalized_domain
                )
                write_result = operations.write_directory_domain_config(
                    target_directory, normalized_domain, payload
                )
                after_payload = (
                    write_result.model_dump(mode="json")
                    if hasattr(write_result, "model_dump")
                    else payload_from_result(write_result)
                )
                result = {
                    "status": "success",
                    "domain": normalized_domain,
                    "directory_path": target_directory,
                    "config": after_payload,
                    "before_after_summary": {
                        "before": payload_from_result(before_payload),
                        "after": after_payload,
                    },
                }
            args["before"] = result["before_after_summary"]["before"]
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_UPSERT_CONFIG,
                args,
                result,
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_UPSERT_CONFIG, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_UPSERT_CONFIG,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_create_workflow(
        config: dict[str, Any],
        layout: dict[str, Any] | None = None,
        workflow_pack: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "workflow_pack": workflow_pack,
            "after": config,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_CREATE_WORKFLOW
            )
            _assistant_require_writable_scope(session, "config/workflow")
            workflow_config = _admin_runtime().workflow_config_admin
            before = workflow_config.read_workflow_editor_document(
                workflow_pack=workflow_pack
            ).config
            saved = workflow_config.save_workflow_editor_document(
                config=config,
                layout=layout,
                workflow_pack=workflow_pack,
            )
            result = {
                "status": "success",
                "config_path": saved.config_path,
                "revision": saved.revision,
                "config": saved.config,
                "before_after_summary": {"before": before, "after": saved.config},
            }
            args["before"] = before
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_CREATE_WORKFLOW,
                args,
                result,
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.ASSISTANT_CREATE_WORKFLOW, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_CREATE_WORKFLOW,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def assistant_run_safe_operation(
        operation: str,
        parameters: dict[str, Any] | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "operation": operation,
            "parameters": parameters or {},
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.ASSISTANT_RUN_SAFE_OPERATION
            )
            _assistant_require_writable_scope(session, "runtime/agents")
            workflow_config = _admin_runtime().workflow_config_admin
            params = parameters or {}
            normalized_operation = str(operation or "").strip()
            if normalized_operation == "clear_workflow_run":
                result_payload = workflow_config.clear_workflow_run(
                    str(params.get("event_id") or "")
                )
            elif normalized_operation == "resume_workflow_run":
                result_payload = workflow_config.resume_workflow_run(
                    str(params.get("event_id") or "")
                )
            else:
                raise ToolError(f"Unsupported safe operation: {normalized_operation}")
            result = (
                result_payload.model_dump(mode="json")
                if hasattr(result_payload, "model_dump")
                else payload_from_result(result_payload) or {}
            )
            result.setdefault("status", "success")
            audit_success(
                resolved_session_id,
                ToolName.ASSISTANT_RUN_SAFE_OPERATION,
                args,
                result,
            )
            return result
        except PolicyError as exc:
            audit_failure(
                resolved_session_id, ToolName.ASSISTANT_RUN_SAFE_OPERATION, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.ASSISTANT_RUN_SAFE_OPERATION,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise
