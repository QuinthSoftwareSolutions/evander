"""Semantic tool registration for the MCP gateway."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field as PydanticField

from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import ToolName
from evander.policy import PolicyError

SemanticPointIdArgument = Annotated[
    int | str,
    PydanticField(description="unsigned 64-bit integer or UUID string point ID."),
]
SemanticPointIdsArgument = Annotated[
    int | str | list[int | str],
    PydanticField(
        description=(
            "One or more point IDs. Each ID must be an unsigned 64-bit integer or UUID string."
        )
    ),
]
SemanticPayloadArgument = Annotated[
    dict[str, Any] | None,
    PydanticField(description="Optional JSON object payload."),
]
SemanticFilterArgument = Annotated[
    dict[str, Any] | None,
    PydanticField(description="Optional Qdrant filter JSON object."),
]


def register_semantic_tools(
    server: Any,
    context: Any,
    deps: Any,
    *,
    transport_session_map: dict[str, str] | None = None,
    audit_success: Any,
    audit_failure: Any,
) -> None:
    """Register semantic tools with the MCP gateway server."""

    semantic_adapter = deps.semantic_adapter

    @server.tool()
    def semantic_upsert_text(
        collection_name: Annotated[
            str,
            PydanticField(description="Semantic collection name."),
        ],
        point_id: SemanticPointIdArgument,
        text: Annotated[
            str,
            PydanticField(description="Text to embed and store in the point payload."),
        ],
        payload: SemanticPayloadArgument = None,
        wait: Annotated[
            bool | None,
            PydanticField(description="Whether Qdrant should wait for the write operation."),
        ] = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Embed and upsert text into the caller session's semantic store."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "collection_name": collection_name,
            "point_id": point_id,
            "text": text,
            "payload": payload,
            "wait": wait,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.SEMANTIC_UPSERT_TEXT
            )
            result = semantic_adapter.upsert_text(
                session=session,
                collection_name=collection_name,
                point_id=point_id,
                text=text,
                payload=payload,
                wait=wait,
            )
            audit_success(
                resolved_session_id,
                ToolName.SEMANTIC_UPSERT_TEXT,
                args,
                {
                    "collection_name": result.get("collection_name"),
                    "point_id": result.get("point_id"),
                    "created_collection": result.get("created_collection"),
                    "operation_status": result.get("operation_status"),
                    "wait": result.get("wait"),
                },
            )
            return result
        except (PolicyError, ToolError, ValueError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_UPSERT_TEXT,
                args,
                str(exc),
            )
            raise ToolError(str(exc)) from exc
        except RuntimeError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_UPSERT_TEXT,
                args,
                str(exc),
                decision="error",
                error_class=type(exc).__name__,
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_UPSERT_TEXT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def semantic_search(
        collection_name: Annotated[
            str,
            PydanticField(description="Semantic collection name."),
        ],
        query_text: Annotated[
            str,
            PydanticField(description="Text to embed and search with."),
        ],
        filter: SemanticFilterArgument = None,
        limit: Annotated[
            int,
            PydanticField(description="Maximum number of points to return.", ge=1),
        ] = 10,
        score_threshold: Annotated[
            float | None,
            PydanticField(description="Optional minimum Qdrant score threshold."),
        ] = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Search the caller session's semantic store with embedded query text."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "collection_name": collection_name,
            "query_text": query_text,
            "filter": filter,
            "limit": limit,
            "score_threshold": score_threshold,
        }
        try:
            session = context.policy.require_tool(resolved_session_id, ToolName.SEMANTIC_SEARCH)
            result = semantic_adapter.search(
                session=session,
                collection_name=collection_name,
                query_text=query_text,
                filter=filter,
                limit=limit,
                score_threshold=score_threshold,
            )
            audit_success(
                resolved_session_id,
                ToolName.SEMANTIC_SEARCH,
                args,
                {
                    "collection_name": result.get("collection_name"),
                    "count": result.get("count"),
                    "limit": result.get("limit"),
                },
            )
            return result
        except (PolicyError, ToolError, ValueError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_SEARCH,
                args,
                str(exc),
            )
            raise ToolError(str(exc)) from exc
        except RuntimeError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_SEARCH,
                args,
                str(exc),
                decision="error",
                error_class=type(exc).__name__,
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_SEARCH,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def semantic_get_points(
        collection_name: Annotated[
            str,
            PydanticField(description="Semantic collection name."),
        ],
        point_ids: SemanticPointIdsArgument,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Retrieve semantic points by explicit unsigned 64-bit integer or UUID IDs."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "collection_name": collection_name,
            "point_ids": point_ids,
        }
        try:
            session = context.policy.require_tool(resolved_session_id, ToolName.SEMANTIC_GET_POINTS)
            result = semantic_adapter.get_points(
                session=session,
                collection_name=collection_name,
                point_ids=point_ids,
            )
            audit_success(
                resolved_session_id,
                ToolName.SEMANTIC_GET_POINTS,
                args,
                {
                    "collection_name": result.get("collection_name"),
                    "count": result.get("count"),
                    "requested_count": result.get("requested_count"),
                    "missing_count": len(result.get("missing_ids") or []),
                },
            )
            return result
        except (PolicyError, ToolError, ValueError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_GET_POINTS,
                args,
                str(exc),
            )
            raise ToolError(str(exc)) from exc
        except RuntimeError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_GET_POINTS,
                args,
                str(exc),
                decision="error",
                error_class=type(exc).__name__,
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_GET_POINTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def semantic_delete_points(
        collection_name: Annotated[
            str,
            PydanticField(description="Semantic collection name."),
        ],
        point_ids: SemanticPointIdsArgument,
        wait: Annotated[
            bool | None,
            PydanticField(description="Whether Qdrant should wait for the delete operation."),
        ] = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete semantic points by explicit unsigned 64-bit integer or UUID IDs."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "collection_name": collection_name,
            "point_ids": point_ids,
            "wait": wait,
        }
        try:
            session = context.policy.require_tool(
                resolved_session_id, ToolName.SEMANTIC_DELETE_POINTS
            )
            result = semantic_adapter.delete_points(
                session=session,
                collection_name=collection_name,
                point_ids=point_ids,
                wait=wait,
            )
            audit_success(
                resolved_session_id,
                ToolName.SEMANTIC_DELETE_POINTS,
                args,
                {
                    "collection_name": result.get("collection_name"),
                    "deleted_count": result.get("deleted_count"),
                    "operation_status": result.get("operation_status"),
                    "wait": result.get("wait"),
                },
            )
            return result
        except (PolicyError, ToolError, ValueError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_DELETE_POINTS,
                args,
                str(exc),
            )
            raise ToolError(str(exc)) from exc
        except RuntimeError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_DELETE_POINTS,
                args,
                str(exc),
                decision="error",
                error_class=type(exc).__name__,
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SEMANTIC_DELETE_POINTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise
