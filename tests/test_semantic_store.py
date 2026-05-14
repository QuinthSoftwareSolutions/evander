from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.semantic_delete import SemanticDeleteEffectProvider
from agent_sandbox.effects.providers.semantic_upsert import SemanticUpsertEffectProvider
from agent_sandbox.effects.providers.schemas import (
    SemanticDeleteEffectDefinition,
    SemanticUpsertEffectDefinition,
)
from agent_sandbox.queries.providers.base import QueryRuntimeContext
from agent_sandbox.queries.providers.schemas import (
    SemanticGetQueryDefinition,
    SemanticSearchQueryDefinition,
)
from agent_sandbox.queries.providers.semantic_get import SemanticGetQueryProvider
from agent_sandbox.queries.providers.semantic_search import SemanticSearchQueryProvider
from agent_sandbox.semantic.openai_embeddings import OpenAIEmbeddingClient
from agent_sandbox.semantic.qdrant import (
    QdrantClient,
    QdrantCollectionInfo,
    QdrantCollectionSchemaMismatch,
    QdrantOperationResult,
    QdrantPoint,
)


def _collection_payload(*, size: int = 1536, distance: str = "Cosine") -> dict[str, object]:
    return {
        "result": {
            "status": "green",
            "config": {
                "params": {
                    "vectors": {
                        "size": size,
                        "distance": distance,
                    }
                }
            },
        }
    }


def test_qdrant_health_success() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="healthz check passed"))
    client = QdrantClient("http://qdrant.test", transport=transport)

    health = client.health()

    assert health.ok is True
    assert health.status_code == 200
    assert health.detail == "healthz check passed"


def test_qdrant_health_failure_does_not_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = QdrantClient("http://qdrant.test", transport=httpx.MockTransport(handler))

    health = client.health()

    assert health.ok is False
    assert health.status_code is None
    assert "connection refused" in str(health.error)


def test_qdrant_get_collection_reports_missing_collection() -> None:
    client = QdrantClient(
        "http://qdrant.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(404, json={})),
    )

    collection = client.get_collection("memory")

    assert collection.exists is False
    assert collection.name == "memory"


def test_qdrant_ensure_collection_noops_when_schema_matches() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json=_collection_payload())

    client = QdrantClient("http://qdrant.test", transport=httpx.MockTransport(handler))

    result = client.ensure_collection("memory", vector_size=1536, distance="Cosine")

    assert result.created is False
    assert result.collection.vector_size == 1536
    assert result.collection.distance == "Cosine"
    assert requests == [("GET", "/collections/memory")]


def test_qdrant_ensure_collection_creates_missing_collection() -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.method == "GET" and len(requests) == 1:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=_collection_payload())

    client = QdrantClient("http://qdrant.test", transport=httpx.MockTransport(handler))

    result = client.ensure_collection("memory", vector_size=1536, distance="Cosine")

    assert result.created is True
    assert requests[0] == ("GET", "/collections/memory", None)
    assert requests[1] == (
        "PUT",
        "/collections/memory",
        {"vectors": {"size": 1536, "distance": "Cosine"}},
    )
    assert requests[2] == ("GET", "/collections/memory", None)


def test_qdrant_ensure_collection_fails_on_schema_mismatch() -> None:
    client = QdrantClient(
        "http://qdrant.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_collection_payload(size=768))
        ),
    )

    with pytest.raises(QdrantCollectionSchemaMismatch, match="schema mismatch"):
        client.ensure_collection("memory", vector_size=1536, distance="Cosine")


def test_qdrant_create_collection_surfaces_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={})
        return httpx.Response(500, text="boom")

    client = QdrantClient("http://qdrant.test", transport=httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="HTTP 500"):
        client.ensure_collection("memory", vector_size=1536, distance="Cosine")


def test_qdrant_upsert_point_request_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"result": {"status": "completed", "operation_id": 123}},
        )

    client = QdrantClient("http://qdrant.test", transport=httpx.MockTransport(handler))

    result = client.upsert_point(
        "memory",
        point_id="point-1",
        vector=[0.1, 0.2],
        payload={"source": "test"},
        wait=True,
    )

    assert result.status == "completed"
    assert result.operation_id == 123
    request = requests[0]
    assert request.method == "PUT"
    assert request.url.path == "/collections/memory/points"
    assert request.url.params["wait"] == "true"
    assert json.loads(request.content.decode("utf-8")) == {
        "points": [
            {
                "id": "point-1",
                "vector": [0.1, 0.2],
                "payload": {"source": "test"},
            }
        ]
    }


def test_qdrant_retrieve_points_request_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"result": [{"id": "point-1", "payload": {"source": "test"}}]},
        )

    client = QdrantClient("http://qdrant.test", transport=httpx.MockTransport(handler))

    points = client.retrieve_points("memory", point_ids=["point-1"])

    assert points == [QdrantPoint(id="point-1", payload={"source": "test"}, raw=points[0].raw)]
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/collections/memory/points"
    assert json.loads(request.content.decode("utf-8")) == {
        "ids": ["point-1"],
        "with_payload": True,
        "with_vector": False,
    }


def test_qdrant_query_points_request_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "result": {
                    "points": [
                        {
                            "id": "point-1",
                            "score": 0.92,
                            "payload": {"source": "test"},
                        }
                    ]
                }
            },
        )

    client = QdrantClient("http://qdrant.test", transport=httpx.MockTransport(handler))

    points = client.query_points(
        "memory",
        query_vector=[0.3, 0.4],
        filter={"must": [{"key": "source", "match": {"value": "test"}}]},
        limit=5,
        score_threshold=0.7,
    )

    assert points[0].id == "point-1"
    assert points[0].score == 0.92
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/collections/memory/points/query"
    assert json.loads(request.content.decode("utf-8")) == {
        "query": [0.3, 0.4],
        "filter": {"must": [{"key": "source", "match": {"value": "test"}}]},
        "limit": 5,
        "score_threshold": 0.7,
        "with_payload": True,
        "with_vector": False,
    }


def test_qdrant_delete_points_request_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"result": {"status": "acknowledged", "operation_id": 456}},
        )

    client = QdrantClient("http://qdrant.test", transport=httpx.MockTransport(handler))

    result = client.delete_points("memory", point_ids=["point-1", "point-2"], wait=True)

    assert result.status == "acknowledged"
    assert result.operation_id == 456
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/collections/memory/points/delete"
    assert request.url.params["wait"] == "true"
    assert json.loads(request.content.decode("utf-8")) == {"points": ["point-1", "point-2"]}


def test_openai_embedding_client_request_shape() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    client = OpenAIEmbeddingClient(
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        model="text-embedding-3-small",
        dimensions=1536,
        transport=httpx.MockTransport(handler),
    )

    embedding = client.embed_text("hello")

    assert embedding == [0.1, 0.2]
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/embeddings"
    assert request.headers["authorization"] == "Bearer sk-test"
    assert json.loads(request.content.decode("utf-8")) == {
        "model": "text-embedding-3-small",
        "input": "hello",
        "dimensions": 1536,
    }


class _FakeEmbeddingClient:
    instances: list["_FakeEmbeddingClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.texts: list[str] = []
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def embed_text(self, text: str) -> list[float]:
        self.texts.append(text)
        return [0.11, 0.22]


class _FakeQdrantClient:
    instances: list["_FakeQdrantClient"] = []
    collection_exists = True

    def __init__(self, base_url: str, *, api_key: str | None = None):
        self.base_url = base_url
        self.api_key = api_key
        self.ensure_calls: list[dict[str, object]] = []
        self.upsert_calls: list[dict[str, object]] = []
        self.retrieve_calls: list[dict[str, object]] = []
        self.query_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get_collection(self, collection_name: str) -> QdrantCollectionInfo:
        return QdrantCollectionInfo(
            name=collection_name,
            exists=self.collection_exists,
            status="green" if self.collection_exists else None,
            vector_size=1536 if self.collection_exists else None,
            distance="Cosine" if self.collection_exists else None,
        )

    def ensure_collection(self, collection_name: str, *, vector_size: int, distance: str):
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

    def upsert_point(self, collection_name: str, **kwargs):
        self.upsert_calls.append({"collection_name": collection_name, **kwargs})
        return QdrantOperationResult(status="completed", operation_id=10)

    def retrieve_points(self, collection_name: str, *, point_ids):
        self.retrieve_calls.append({"collection_name": collection_name, "point_ids": point_ids})
        return [
            QdrantPoint(id=point_ids[0], payload={"source": "test", "_semantic_text": "stored"})
        ]

    def query_points(self, collection_name: str, **kwargs):
        self.query_calls.append({"collection_name": collection_name, **kwargs})
        return [
            QdrantPoint(
                id="point-1",
                score=0.91,
                payload={"source": "test", "_semantic_text": "stored"},
            )
        ]

    def delete_points(self, collection_name: str, *, point_ids, wait=None):
        self.delete_calls.append(
            {"collection_name": collection_name, "point_ids": point_ids, "wait": wait}
        )
        return QdrantOperationResult(status="completed", operation_id=11)


def _effect_context() -> EffectRuntimeContext:
    return EffectRuntimeContext(
        provider_config={"runtime_dir": "/tmp/runtime"},
        env={"OPENAI_API_KEY": "sk-test", "QDRANT_API_KEY": "qdrant-key"},
    )


def _query_context() -> QueryRuntimeContext:
    return QueryRuntimeContext(
        provider_config={"runtime_dir": "/tmp/runtime"},
        env={"OPENAI_API_KEY": "sk-test", "QDRANT_API_KEY": "qdrant-key"},
    )


@pytest.fixture(autouse=True)
def _reset_fakes() -> None:
    _FakeEmbeddingClient.instances = []
    _FakeQdrantClient.instances = []
    _FakeQdrantClient.collection_exists = True


def test_semantic_upsert_provider_embeds_ensures_and_upserts(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_sandbox.semantic.workflow_helpers.OpenAIEmbeddingClient",
        _FakeEmbeddingClient,
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.semantic_upsert.QdrantClient",
        _FakeQdrantClient,
    )
    provider = SemanticUpsertEffectProvider()
    provider.initialize(
        SemanticUpsertEffectDefinition(
            id="semantic-upsert-1",
            display_name="Upsert",
            type="effect:semantic:upsert",
            collection_name="memory",
            point_id="point-1",
            text="remember this",
            payload={"source": "test"},
            wait=True,
        ),
        _effect_context(),
    )

    output = provider.execute()

    qdrant = _FakeQdrantClient.instances[0]
    assert qdrant.ensure_calls == [
        {"collection_name": "memory", "vector_size": 1536, "distance": "Cosine"}
    ]
    assert qdrant.upsert_calls == [
        {
            "collection_name": "memory",
            "point_id": "point-1",
            "vector": [0.11, 0.22],
            "payload": {"source": "test", "_semantic_text": "remember this"},
            "wait": True,
        }
    ]
    assert _FakeEmbeddingClient.instances[0].texts == ["remember this"]
    assert output["payload"] == {"source": "test", "_semantic_text": "remember this"}
    assert output["operation_status"] == "completed"


def test_semantic_upsert_rejects_reserved_payload_key() -> None:
    provider = SemanticUpsertEffectProvider()
    provider.initialize(
        SemanticUpsertEffectDefinition(
            id="semantic-upsert-1",
            display_name="Upsert",
            type="effect:semantic:upsert",
            collection_name="memory",
            point_id="point-1",
            text="remember this",
            payload={"_semantic_text": "caller value"},
        ),
        _effect_context(),
    )

    with pytest.raises(ValueError, match="reserved key"):
        provider.execute()


def test_semantic_upsert_requires_text() -> None:
    provider = SemanticUpsertEffectProvider()
    provider.initialize(
        SemanticUpsertEffectDefinition(
            id="semantic-upsert-1",
            display_name="Upsert",
            type="effect:semantic:upsert",
            collection_name="memory",
            point_id="point-1",
            text="initial",
        ),
        _effect_context(),
    )

    with pytest.raises(ValueError, match="requires text"):
        provider.execute(resolved_config={"text": " "})


def test_semantic_search_provider_embeds_and_queries(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_sandbox.semantic.workflow_helpers.OpenAIEmbeddingClient",
        _FakeEmbeddingClient,
    )
    monkeypatch.setattr(
        "agent_sandbox.queries.providers.semantic_search.QdrantClient",
        _FakeQdrantClient,
    )
    provider = SemanticSearchQueryProvider()
    provider.initialize(
        SemanticSearchQueryDefinition(
            id="semantic-search-1",
            display_name="Search",
            type="query:semantic:search",
            collection_name="memory",
            query_text="find it",
            filter={"must": [{"key": "source", "match": {"value": "test"}}]},
            limit=3,
            score_threshold=0.8,
        ),
        _query_context(),
    )

    output = provider.execute()

    qdrant = _FakeQdrantClient.instances[0]
    assert qdrant.query_calls == [
        {
            "collection_name": "memory",
            "query_vector": [0.11, 0.22],
            "filter": {"must": [{"key": "source", "match": {"value": "test"}}]},
            "limit": 3,
            "score_threshold": 0.8,
        }
    ]
    assert output["points"] == [
        {
            "id": "point-1",
            "payload": {"source": "test", "_semantic_text": "stored"},
            "score": 0.91,
        }
    ]
    assert output["count"] == 1


def test_semantic_search_fails_before_embedding_when_collection_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_sandbox.semantic.workflow_helpers.OpenAIEmbeddingClient",
        _FakeEmbeddingClient,
    )
    monkeypatch.setattr(
        "agent_sandbox.queries.providers.semantic_search.QdrantClient",
        _FakeQdrantClient,
    )
    _FakeQdrantClient.collection_exists = False
    provider = SemanticSearchQueryProvider()
    provider.initialize(
        SemanticSearchQueryDefinition(
            id="semantic-search-1",
            display_name="Search",
            type="query:semantic:search",
            collection_name="missing",
            query_text="find it",
        ),
        _query_context(),
    )

    with pytest.raises(ValueError, match="does not exist"):
        provider.execute()
    assert _FakeEmbeddingClient.instances == []


def test_semantic_get_provider_retrieves_explicit_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_sandbox.queries.providers.semantic_get.QdrantClient",
        _FakeQdrantClient,
    )
    provider = SemanticGetQueryProvider()
    provider.initialize(
        SemanticGetQueryDefinition(
            id="semantic-get-1",
            display_name="Get",
            type="query:semantic:get",
            collection_name="memory",
            point_ids=["point-1", "point-2"],
        ),
        _query_context(),
    )

    output = provider.execute()

    qdrant = _FakeQdrantClient.instances[0]
    assert qdrant.retrieve_calls == [
        {"collection_name": "memory", "point_ids": ["point-1", "point-2"]}
    ]
    assert output["point_ids"] == ["point-1", "point-2"]
    assert output["missing_ids"] == ["point-2"]


def test_semantic_delete_provider_deletes_only_explicit_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.semantic_delete.QdrantClient",
        _FakeQdrantClient,
    )
    provider = SemanticDeleteEffectProvider()
    provider.initialize(
        SemanticDeleteEffectDefinition(
            id="semantic-delete-1",
            display_name="Delete",
            type="effect:semantic:delete",
            collection_name="memory",
            point_ids=["point-1", "point-2"],
            wait=True,
        ),
        _effect_context(),
    )

    output = provider.execute()

    qdrant = _FakeQdrantClient.instances[0]
    assert qdrant.delete_calls == [
        {"collection_name": "memory", "point_ids": ["point-1", "point-2"], "wait": True}
    ]
    assert output["deleted_count"] == 2
    assert output["operation_status"] == "completed"
