from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from .settings import SemanticVectorDistance, normalize_vector_distance


class QdrantError(RuntimeError):
    pass


class QdrantCollectionSchemaMismatch(QdrantError):
    pass


@dataclass(frozen=True)
class QdrantHealth:
    ok: bool
    status_code: int | None
    detail: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class QdrantCollectionInfo:
    name: str
    exists: bool
    status: str | None = None
    vector_size: int | None = None
    distance: str | None = None
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class QdrantEnsureCollectionResult:
    collection: QdrantCollectionInfo
    created: bool


QdrantPointId = int | str


@dataclass(frozen=True)
class QdrantOperationResult:
    status: str | None
    operation_id: int | str | None
    raw: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class QdrantPoint:
    id: QdrantPointId
    payload: Mapping[str, Any] | None = None
    score: float | None = None
    version: int | None = None
    raw: Mapping[str, Any] | None = None


class QdrantClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"api-key": api_key} if api_key else None
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "QdrantClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def health(self) -> QdrantHealth:
        try:
            response = self._client.get("healthz")
        except httpx.RequestError as exc:
            return QdrantHealth(ok=False, status_code=None, error=str(exc))
        detail = response.text.strip() or None
        return QdrantHealth(
            ok=200 <= response.status_code < 300,
            status_code=response.status_code,
            detail=detail,
            error=None if 200 <= response.status_code < 300 else detail,
        )

    def get_collection(self, collection_name: str) -> QdrantCollectionInfo:
        name = _normalize_collection_name(collection_name)
        try:
            response = self._client.get(f"collections/{name}")
        except httpx.RequestError as exc:
            raise QdrantError(f"Qdrant collection lookup failed: {exc}") from exc
        if response.status_code == 404:
            return QdrantCollectionInfo(name=name, exists=False)
        if response.status_code >= 400:
            raise QdrantError(
                f"Qdrant collection lookup failed with HTTP {response.status_code}: "
                f"{response.text.strip()}"
            )
        payload = _json_object(response)
        return _collection_info_from_payload(name, payload)

    def create_collection(
        self,
        collection_name: str,
        *,
        vector_size: int,
        distance: SemanticVectorDistance | str,
    ) -> QdrantCollectionInfo:
        name = _normalize_collection_name(collection_name)
        if vector_size <= 0:
            raise ValueError("Qdrant vector size must be positive")
        normalized_distance = normalize_vector_distance(distance)
        try:
            response = self._client.put(
                f"collections/{name}",
                json={
                    "vectors": {
                        "size": vector_size,
                        "distance": normalized_distance,
                    }
                },
            )
        except httpx.RequestError as exc:
            raise QdrantError(f"Qdrant collection creation failed: {exc}") from exc
        if response.status_code >= 400:
            raise QdrantError(
                f"Qdrant collection creation failed with HTTP {response.status_code}: "
                f"{response.text.strip()}"
            )
        return self.get_collection(name)

    def ensure_collection(
        self,
        collection_name: str,
        *,
        vector_size: int,
        distance: SemanticVectorDistance | str,
    ) -> QdrantEnsureCollectionResult:
        normalized_distance = normalize_vector_distance(distance)
        existing = self.get_collection(collection_name)
        if not existing.exists:
            created = self.create_collection(
                collection_name,
                vector_size=vector_size,
                distance=normalized_distance,
            )
            _assert_collection_schema(
                created,
                expected_size=vector_size,
                expected_distance=normalized_distance,
            )
            return QdrantEnsureCollectionResult(collection=created, created=True)
        _assert_collection_schema(
            existing,
            expected_size=vector_size,
            expected_distance=normalized_distance,
        )
        return QdrantEnsureCollectionResult(collection=existing, created=False)

    def upsert_point(
        self,
        collection_name: str,
        *,
        point_id: QdrantPointId,
        vector: list[float],
        payload: Mapping[str, Any] | None = None,
        wait: bool | None = None,
    ) -> QdrantOperationResult:
        name = _normalize_collection_name(collection_name)
        point: dict[str, Any] = {
            "id": normalize_point_id(point_id),
            "vector": _normalize_vector(vector),
        }
        if payload is not None:
            point["payload"] = dict(payload)
        try:
            response = self._client.put(
                f"collections/{name}/points",
                params=_wait_params(wait),
                json={"points": [point]},
            )
        except httpx.RequestError as exc:
            raise QdrantError(f"Qdrant point upsert failed: {exc}") from exc
        _raise_points_error(response, collection_name=name, action="point upsert")
        return _operation_result_from_payload(_json_object(response))

    def retrieve_points(
        self,
        collection_name: str,
        *,
        point_ids: list[QdrantPointId],
    ) -> list[QdrantPoint]:
        name = _normalize_collection_name(collection_name)
        ids = normalize_point_ids(point_ids)
        try:
            response = self._client.post(
                f"collections/{name}/points",
                json={
                    "ids": ids,
                    "with_payload": True,
                    "with_vector": False,
                },
            )
        except httpx.RequestError as exc:
            raise QdrantError(f"Qdrant point retrieval failed: {exc}") from exc
        _raise_points_error(response, collection_name=name, action="point retrieval")
        result = _json_object(response).get("result")
        if not isinstance(result, list):
            raise QdrantError("Qdrant point retrieval response did not contain a result list")
        return [_point_from_payload(item) for item in result]

    def query_points(
        self,
        collection_name: str,
        *,
        query_vector: list[float],
        filter: Mapping[str, Any] | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
    ) -> list[QdrantPoint]:
        name = _normalize_collection_name(collection_name)
        if limit <= 0:
            raise ValueError("Qdrant point query limit must be positive")
        body: dict[str, Any] = {
            "query": _normalize_vector(query_vector),
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        if filter is not None:
            body["filter"] = dict(filter)
        if score_threshold is not None:
            body["score_threshold"] = float(score_threshold)
        try:
            response = self._client.post(f"collections/{name}/points/query", json=body)
        except httpx.RequestError as exc:
            raise QdrantError(f"Qdrant point query failed: {exc}") from exc
        _raise_points_error(response, collection_name=name, action="point query")
        result = _json_object(response).get("result")
        if not isinstance(result, Mapping):
            raise QdrantError("Qdrant point query response did not contain a result object")
        points = result.get("points")
        if not isinstance(points, list):
            raise QdrantError("Qdrant point query response did not contain a points list")
        return [_point_from_payload(item) for item in points]

    def delete_points(
        self,
        collection_name: str,
        *,
        point_ids: list[QdrantPointId],
        wait: bool | None = None,
    ) -> QdrantOperationResult:
        name = _normalize_collection_name(collection_name)
        ids = normalize_point_ids(point_ids)
        try:
            response = self._client.post(
                f"collections/{name}/points/delete",
                params=_wait_params(wait),
                json={"points": ids},
            )
        except httpx.RequestError as exc:
            raise QdrantError(f"Qdrant point delete failed: {exc}") from exc
        _raise_points_error(response, collection_name=name, action="point delete")
        return _operation_result_from_payload(_json_object(response))


def normalize_point_id(value: object) -> QdrantPointId:
    if isinstance(value, bool) or value is None:
        raise ValueError("Qdrant point ID must be a non-empty string or integer")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("Qdrant integer point IDs must be non-negative")
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("Qdrant point ID must be a non-empty string or integer")
    return text


def normalize_point_ids(values: list[QdrantPointId] | tuple[QdrantPointId, ...]) -> list[QdrantPointId]:
    ids = [normalize_point_id(value) for value in values]
    if not ids:
        raise ValueError("Qdrant point ID list must not be empty")
    return ids


def _normalize_collection_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Qdrant collection name is required")
    return text


def _json_object(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise QdrantError("Qdrant returned a non-JSON response") from exc
    if not isinstance(payload, Mapping):
        raise QdrantError("Qdrant returned an unexpected JSON response")
    return payload


def _collection_info_from_payload(
    collection_name: str, payload: Mapping[str, Any]
) -> QdrantCollectionInfo:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise QdrantError("Qdrant collection response did not contain a result object")
    status = _optional_string(result.get("status"))
    config = result.get("config")
    params: Mapping[str, Any] = {}
    if isinstance(config, Mapping) and isinstance(config.get("params"), Mapping):
        params = config["params"]
    vectors = params.get("vectors")
    vector_size, distance = _extract_single_vector_schema(vectors)
    return QdrantCollectionInfo(
        name=collection_name,
        exists=True,
        status=status,
        vector_size=vector_size,
        distance=distance,
        raw=result,
    )


def _extract_single_vector_schema(value: Any) -> tuple[int | None, str | None]:
    if not isinstance(value, Mapping):
        return None, None
    if "size" in value or "distance" in value:
        size = value.get("size")
        distance = value.get("distance")
        return _optional_int(size), _optional_string(distance)
    return None, None


def _assert_collection_schema(
    collection: QdrantCollectionInfo,
    *,
    expected_size: int,
    expected_distance: SemanticVectorDistance,
) -> None:
    if collection.vector_size is None or collection.distance is None:
        raise QdrantCollectionSchemaMismatch(
            f"Qdrant collection {collection.name!r} does not expose an unnamed vector "
            "schema; named-vector collections are not managed by this v1 effect"
        )
    actual_distance = normalize_vector_distance(collection.distance)
    if collection.vector_size != expected_size or actual_distance != expected_distance:
        raise QdrantCollectionSchemaMismatch(
            f"Qdrant collection {collection.name!r} schema mismatch: "
            f"existing size={collection.vector_size} distance={actual_distance}; "
            f"configured size={expected_size} distance={expected_distance}"
        )


def collection_schema_error(
    collection: QdrantCollectionInfo,
    *,
    expected_size: int,
    expected_distance: SemanticVectorDistance,
) -> str | None:
    try:
        _assert_collection_schema(
            collection,
            expected_size=expected_size,
            expected_distance=expected_distance,
        )
    except QdrantCollectionSchemaMismatch as exc:
        return str(exc)
    return None


def _normalize_vector(values: list[float]) -> list[float]:
    if not isinstance(values, list) or not values:
        raise ValueError("Qdrant vector must be a non-empty list")
    return [float(value) for value in values]


def _wait_params(wait: bool | None) -> dict[str, bool]:
    return {"wait": wait} if wait is not None else {}


def _raise_points_error(
    response: httpx.Response,
    *,
    collection_name: str,
    action: str,
) -> None:
    if response.status_code == 404:
        raise QdrantError(f"Qdrant collection {collection_name!r} does not exist")
    if response.status_code >= 400:
        raise QdrantError(
            f"Qdrant {action} failed with HTTP {response.status_code}: "
            f"{response.text.strip()}"
        )


def _operation_result_from_payload(payload: Mapping[str, Any]) -> QdrantOperationResult:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise QdrantError("Qdrant operation response did not contain a result object")
    return QdrantOperationResult(
        status=_optional_string(result.get("status")),
        operation_id=_optional_int_or_string(result.get("operation_id")),
        raw=result,
    )


def _point_from_payload(value: Any) -> QdrantPoint:
    if not isinstance(value, Mapping):
        raise QdrantError("Qdrant point response item was not an object")
    if "id" not in value:
        raise QdrantError("Qdrant point response item did not include an id")
    payload = value.get("payload")
    if payload is not None and not isinstance(payload, Mapping):
        raise QdrantError("Qdrant point response payload was not an object")
    return QdrantPoint(
        id=normalize_point_id(value.get("id")),
        payload=dict(payload) if isinstance(payload, Mapping) else None,
        score=_optional_float(value.get("score")),
        version=_optional_int(value.get("version")),
        raw=value,
    )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int_or_string(value: Any) -> int | str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return text or None
