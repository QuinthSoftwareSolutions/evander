from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.admin.service import AdminService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import create_server
from agent_sandbox.models import ApprovalStatus, SessionManifest, ToolName
from agent_sandbox.runtime import build_runtime
from agent_sandbox.semantic.qdrant import (
    QdrantCollectionInfo,
    QdrantOperationResult,
    QdrantPoint,
)

POINT_ID_1 = 123
POINT_ID_2 = "550e8400-e29b-41d4-a716-446655440000"


def call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


class FakeEmbeddingClient:
    instances: list["FakeEmbeddingClient"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.texts: list[str] = []
        self.instances.append(self)

    def __enter__(self) -> "FakeEmbeddingClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def embed_text(self, text: str) -> list[float]:
        self.texts.append(text)
        return [0.11, 0.22]


class FakeQdrantClient:
    instances: list["FakeQdrantClient"] = []
    collection_exists = True

    def __init__(self, base_url: str, *, api_key: str | None = None) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.ensure_calls: list[dict[str, object]] = []
        self.upsert_calls: list[dict[str, object]] = []
        self.retrieve_calls: list[dict[str, object]] = []
        self.query_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.instances.append(self)

    def __enter__(self) -> "FakeQdrantClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get_collection(self, collection_name: str) -> QdrantCollectionInfo:
        exists = type(self).collection_exists
        return QdrantCollectionInfo(
            name=collection_name,
            exists=exists,
            status="green" if exists else None,
            vector_size=1536 if exists else None,
            distance="Cosine" if exists else None,
        )

    def ensure_collection(
        self, collection_name: str, *, vector_size: int, distance: str
    ) -> SimpleNamespace:
        self.ensure_calls.append(
            {
                "collection_name": collection_name,
                "vector_size": vector_size,
                "distance": distance,
            }
        )
        return SimpleNamespace(
            collection=QdrantCollectionInfo(
                name=collection_name,
                exists=True,
                status="green",
                vector_size=vector_size,
                distance=distance,
            ),
            created=False,
        )

    def upsert_point(self, collection_name: str, **kwargs: object) -> QdrantOperationResult:
        self.upsert_calls.append({"collection_name": collection_name, **kwargs})
        return QdrantOperationResult(status="completed", operation_id=10)

    def retrieve_points(self, collection_name: str, *, point_ids: list[int | str]):
        self.retrieve_calls.append({"collection_name": collection_name, "point_ids": point_ids})
        return [
            QdrantPoint(id=point_ids[0], payload={"source": "test", "_semantic_text": "stored"})
        ]

    def query_points(self, collection_name: str, **kwargs: object):
        self.query_calls.append({"collection_name": collection_name, **kwargs})
        return [
            QdrantPoint(
                id=POINT_ID_1,
                score=0.91,
                payload={"source": "test", "_semantic_text": "stored"},
            )
        ]

    def delete_points(
        self,
        collection_name: str,
        *,
        point_ids: list[int | str],
        wait: bool | None = None,
    ) -> QdrantOperationResult:
        self.delete_calls.append(
            {"collection_name": collection_name, "point_ids": point_ids, "wait": wait}
        )
        return QdrantOperationResult(status="completed", operation_id=11)


@pytest.fixture(autouse=True)
def reset_fakes() -> None:
    FakeEmbeddingClient.instances = []
    FakeQdrantClient.instances = []
    FakeQdrantClient.collection_exists = True


def install_semantic_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_sandbox.semantic.workflow_helpers.OpenAIEmbeddingClient",
        FakeEmbeddingClient,
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.semantic_upsert.QdrantClient",
        FakeQdrantClient,
    )
    monkeypatch.setattr(
        "agent_sandbox.queries.providers.semantic_search.QdrantClient",
        FakeQdrantClient,
    )
    monkeypatch.setattr(
        "agent_sandbox.queries.providers.semantic_get.QdrantClient",
        FakeQdrantClient,
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.semantic_delete.QdrantClient",
        FakeQdrantClient,
    )


def build_gateway_context(
    tmp_path: Path,
    *,
    allowed_tools: list[ToolName],
    directory_path: str = "root",
):
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=allowed_tools,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path=directory_path,
        )
    )
    return context


def test_semantic_tools_register_with_schema_guidance(tmp_path: Path) -> None:
    context = build_gateway_context(tmp_path, allowed_tools=[])
    server = create_server(context)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert {
        "semantic_upsert_text",
        "semantic_search",
        "semantic_get_points",
        "semantic_delete_points",
    }.issubset(tools)
    assert "semantic_store_status" not in tools
    assert "semantic_ensure_collection" not in tools

    upsert = tools["semantic_upsert_text"]
    get_points = tools["semantic_get_points"]
    assert "Embed and upsert text" in upsert.description
    assert "Retrieve semantic points" in get_points.description
    assert "directory_path" not in upsert.parameters["properties"]
    assert "directory_path" not in get_points.parameters["properties"]
    assert "unsigned 64-bit integer or UUID string" in json.dumps(upsert.parameters)
    assert "unsigned 64-bit integer or UUID string" in json.dumps(get_points.parameters)


@pytest.mark.parametrize(
    ("tool_name", "required_tool", "arguments"),
    [
        (
            "semantic_upsert_text",
            ToolName.SEMANTIC_UPSERT_TEXT,
            {"collection_name": "memory", "point_id": POINT_ID_1, "text": "remember"},
        ),
        (
            "semantic_search",
            ToolName.SEMANTIC_SEARCH,
            {"collection_name": "memory", "query_text": "remember"},
        ),
        (
            "semantic_get_points",
            ToolName.SEMANTIC_GET_POINTS,
            {"collection_name": "memory", "point_ids": [POINT_ID_1]},
        ),
        (
            "semantic_delete_points",
            ToolName.SEMANTIC_DELETE_POINTS,
            {"collection_name": "memory", "point_ids": [POINT_ID_1]},
        ),
    ],
)
def test_semantic_tools_require_matching_allowed_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    required_tool: ToolName,
    arguments: dict[str, object],
) -> None:
    install_semantic_fakes(monkeypatch)
    semantic_tools = [
        ToolName.SEMANTIC_UPSERT_TEXT,
        ToolName.SEMANTIC_SEARCH,
        ToolName.SEMANTIC_GET_POINTS,
        ToolName.SEMANTIC_DELETE_POINTS,
    ]
    wrong_tool = next(tool for tool in semantic_tools if tool != required_tool)
    context = build_gateway_context(tmp_path, allowed_tools=[wrong_tool])
    server = create_server(context)

    with pytest.raises(ToolError, match=f"Tool {tool_name} is not allowed"):
        call_tool(server, tool_name, {"session_id": "session-1", **arguments})

    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[required_tool],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    result = call_tool(server, tool_name, {"session_id": "session-1", **arguments})

    assert result["success"] is True


def test_semantic_upsert_uses_session_directory_config_and_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_semantic_fakes(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("QDRANT_API_KEY", "qdrant-key")
    service = AdminService(tmp_path)
    service.create_directory_child("root", "team")
    service.write_directory_domain_config(
        "root/team",
        "providers",
        {"qdrant_base_url": "http://qdrant.team"},
    )
    context = build_gateway_context(
        tmp_path,
        allowed_tools=[ToolName.SEMANTIC_UPSERT_TEXT],
        directory_path="root/team",
    )
    server = create_server(context)

    result = call_tool(
        server,
        "semantic_upsert_text",
        {
            "session_id": "session-1",
            "collection_name": "memory",
            "point_id": POINT_ID_1,
            "text": "remember this",
            "payload": {"source": "gateway"},
            "wait": True,
        },
    )

    qdrant = FakeQdrantClient.instances[0]
    assert qdrant.base_url == "http://qdrant.team"
    assert qdrant.api_key == "qdrant-key"
    assert qdrant.ensure_calls == [
        {"collection_name": "memory", "vector_size": 1536, "distance": "Cosine"}
    ]
    assert qdrant.upsert_calls == [
        {
            "collection_name": "memory",
            "point_id": POINT_ID_1,
            "vector": [0.11, 0.22],
            "payload": {"source": "gateway", "_semantic_text": "remember this"},
            "wait": True,
        }
    ]
    assert FakeEmbeddingClient.instances[0].kwargs["api_key"] == "openai-key"
    assert FakeEmbeddingClient.instances[0].texts == ["remember this"]
    assert result["payload"] == {"source": "gateway", "_semantic_text": "remember this"}
    assert result["operation_status"] == "completed"


def test_semantic_upsert_rejects_reserved_payload_key(tmp_path: Path) -> None:
    context = build_gateway_context(tmp_path, allowed_tools=[ToolName.SEMANTIC_UPSERT_TEXT])
    server = create_server(context)

    with pytest.raises(ToolError, match="reserved key"):
        call_tool(
            server,
            "semantic_upsert_text",
            {
                "session_id": "session-1",
                "collection_name": "memory",
                "point_id": POINT_ID_1,
                "text": "remember",
                "payload": {"_semantic_text": "caller value"},
            },
        )

    assert FakeQdrantClient.instances == []


def test_semantic_search_passes_limit_filter_and_score_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_semantic_fakes(monkeypatch)
    context = build_gateway_context(tmp_path, allowed_tools=[ToolName.SEMANTIC_SEARCH])
    server = create_server(context)

    result = call_tool(
        server,
        "semantic_search",
        {
            "session_id": "session-1",
            "collection_name": "memory",
            "query_text": "find it",
            "filter": {"must": [{"key": "source", "match": {"value": "test"}}]},
            "limit": 3,
            "score_threshold": 0.8,
        },
    )

    qdrant = FakeQdrantClient.instances[0]
    assert qdrant.query_calls == [
        {
            "collection_name": "memory",
            "query_vector": [0.11, 0.22],
            "filter": {"must": [{"key": "source", "match": {"value": "test"}}]},
            "limit": 3,
            "score_threshold": 0.8,
        }
    ]
    assert FakeEmbeddingClient.instances[0].texts == ["find it"]
    assert result["points"] == [
        {
            "id": POINT_ID_1,
            "payload": {"source": "test", "_semantic_text": "stored"},
            "score": 0.91,
        }
    ]


def test_semantic_get_accepts_one_id_and_multiple_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_semantic_fakes(monkeypatch)
    context = build_gateway_context(tmp_path, allowed_tools=[ToolName.SEMANTIC_GET_POINTS])
    server = create_server(context)

    single = call_tool(
        server,
        "semantic_get_points",
        {
            "session_id": "session-1",
            "collection_name": "memory",
            "point_ids": POINT_ID_1,
        },
    )
    multiple = call_tool(
        server,
        "semantic_get_points",
        {
            "session_id": "session-1",
            "collection_name": "memory",
            "point_ids": [POINT_ID_1, POINT_ID_2],
        },
    )

    assert FakeQdrantClient.instances[0].retrieve_calls == [
        {"collection_name": "memory", "point_ids": [POINT_ID_1]}
    ]
    assert FakeQdrantClient.instances[1].retrieve_calls == [
        {"collection_name": "memory", "point_ids": [POINT_ID_1, POINT_ID_2]}
    ]
    assert single["point_ids"] == [POINT_ID_1]
    assert multiple["missing_ids"] == [POINT_ID_2]


def test_semantic_delete_uses_explicit_point_ids_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_semantic_fakes(monkeypatch)
    context = build_gateway_context(tmp_path, allowed_tools=[ToolName.SEMANTIC_DELETE_POINTS])
    server = create_server(context)

    result = call_tool(
        server,
        "semantic_delete_points",
        {
            "session_id": "session-1",
            "collection_name": "memory",
            "point_ids": [POINT_ID_1, POINT_ID_2],
            "wait": True,
        },
    )

    qdrant = FakeQdrantClient.instances[0]
    assert qdrant.delete_calls == [
        {"collection_name": "memory", "point_ids": [POINT_ID_1, POINT_ID_2], "wait": True}
    ]
    assert result["deleted_count"] == 2
    assert result["operation_status"] == "completed"


def test_semantic_delete_points_creates_pending_approval_without_qdrant_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_semantic_fakes(monkeypatch)
    context = build_gateway_context(tmp_path, allowed_tools=[ToolName.SEMANTIC_DELETE_POINTS])
    server = create_server(context)

    with pytest.raises(ToolError, match="requires approval"):
        call_tool(
            server,
            "semantic_delete_points",
            {
                "session_id": "session-1",
                "collection_name": "memory",
                "point_ids": [POINT_ID_1],
            },
        )

    approvals = context.approvals.read_all()
    assert len(approvals) == 1
    approval = next(iter(approvals.values()))
    assert approval.status == ApprovalStatus.PENDING
    assert approval.action_type == "mcp_tool_invocation"
    assert approval.tool_name == ToolName.SEMANTIC_DELETE_POINTS.value
    assert approval.payload["tool_name"] == ToolName.SEMANTIC_DELETE_POINTS.value
    assert approval.payload["arguments"] == {
        "session_id": "session-1",
        "collection_name": "memory",
        "point_ids": [POINT_ID_1],
    }
    assert FakeQdrantClient.instances == []


def test_semantic_rejects_arbitrary_string_point_ids_before_qdrant_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_semantic_fakes(monkeypatch)
    context = build_gateway_context(tmp_path, allowed_tools=[ToolName.SEMANTIC_GET_POINTS])
    server = create_server(context)

    with pytest.raises(ToolError, match="UUID"):
        call_tool(
            server,
            "semantic_get_points",
            {
                "session_id": "session-1",
                "collection_name": "memory",
                "point_ids": "not-a-valid-id",
            },
        )

    assert FakeQdrantClient.instances == []


@pytest.mark.parametrize(
    ("tool_name", "allowed_tool", "arguments"),
    [
        (
            "semantic_search",
            ToolName.SEMANTIC_SEARCH,
            {"collection_name": "missing", "query_text": "find it"},
        ),
        (
            "semantic_get_points",
            ToolName.SEMANTIC_GET_POINTS,
            {"collection_name": "missing", "point_ids": [POINT_ID_1]},
        ),
        (
            "semantic_delete_points",
            ToolName.SEMANTIC_DELETE_POINTS,
            {"collection_name": "missing", "point_ids": [POINT_ID_1]},
        ),
    ],
)
def test_semantic_search_get_and_delete_fail_when_collection_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    allowed_tool: ToolName,
    arguments: dict[str, object],
) -> None:
    install_semantic_fakes(monkeypatch)
    FakeQdrantClient.collection_exists = False
    context = build_gateway_context(tmp_path, allowed_tools=[allowed_tool])
    server = create_server(context)

    with pytest.raises(ToolError, match="does not exist"):
        call_tool(server, tool_name, {"session_id": "session-1", **arguments})

    if tool_name == "semantic_search":
        assert FakeEmbeddingClient.instances == []
