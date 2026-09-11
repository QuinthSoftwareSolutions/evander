from __future__ import annotations

import json
import time
from uuid import NAMESPACE_URL, uuid5

from evander.semantic.qdrant import QdrantClient
from evander.semantic.openai_embeddings import OpenAIEmbeddingClient
from evander.semantic.settings import (
    resolve_optional_secret,
    semantic_settings_from_runtime_context,
)

from .models import digest


EMBEDDING_BATCH_SIZE = 32
NEW_EMBEDDING_LIMIT = 512
CACHED_EMBEDDING_LIMIT = 16_384
INDEX_PASS_SECONDS = 30


class IndexSemantic:
    def __init__(self, runtime):
        self.runtime = runtime

    def settings(self, directory: str):
        return semantic_settings_from_runtime_context(
            self.runtime, directory_path=directory, provider_kind="query"
        )

    def fingerprint(self, directory: str) -> dict:
        settings = self.settings(directory)
        return {
            "provider": settings.semantic_embedding_provider,
            "model": settings.semantic_embedding_model,
            "dimensions": settings.semantic_embedding_dimensions,
            "distance": settings.semantic_vector_distance,
            "endpoint": settings.openai_api_base_url,
            "store": settings.qdrant_base_url,
        }

    def _clients(self, directory: str):
        settings = self.settings(directory)
        return settings, QdrantClient(
            settings.qdrant_base_url,
            api_key=resolve_optional_secret(self.runtime, settings.qdrant_api_key_env),
        )

    def embed(self, directory: str, text: str) -> list[float]:
        from evander.semantic.workflow_helpers import embed_text

        return embed_text(self.runtime, self.settings(directory), text)

    def index_batch(
        self,
        store,
        build: dict,
        *,
        limit: int = NEW_EMBEDDING_LIMIT,
        cached_limit: int = CACHED_EMBEDDING_LIMIT,
    ) -> bool:
        """Bound new embedding work while draining reusable vectors much faster."""
        if limit < 1 or cached_limit < 1:
            raise ValueError("Semantic indexing limits must be positive")
        if self.fingerprint(build["directory"]) != build["embedding"]:
            raise ValueError("Embedding settings changed during indexing; request a new build")
        settings, client = self._clients(build["directory"])
        fingerprint = digest(build["embedding"])
        collection = "code_" + digest([build["repo"], fingerprint])[:32]
        with store.connect() as db:
            rows = db.execute(
                """SELECT DISTINCT c.hash,c.path,t.text,e.vector FROM chunks c JOIN contents t ON c.hash=t.hash
                LEFT JOIN embeddings e ON e.repo=? AND e.fingerprint=? AND e.hash=c.hash WHERE c.snapshot=? ORDER BY c.hash""",
                (build["repo"], fingerprint, build["id"]),
            ).fetchall()
        metrics = dict(build["metrics"])
        completed = int(metrics.get("embedded_chunks", 0))
        new_embeddings = 0
        cached_embeddings = 0
        deadline = time.monotonic() + INDEX_PASS_SECONDS
        with (
            client,
            OpenAIEmbeddingClient(
                base_url=settings.openai_api_base_url,
                api_key=resolve_optional_secret(self.runtime, settings.openai_api_key_env),
                model=settings.semantic_embedding_model,
                dimensions=settings.semantic_embedding_dimensions,
                timeout=min(settings.openai_relay_timeout_seconds, 30),
            ) as embeddings,
        ):
            client.ensure_collection(
                collection,
                vector_size=settings.semantic_embedding_dimensions,
                distance=settings.semantic_vector_distance,
            )
            # Persist the collection before any writes so cleanup can reclaim partial batches.
            store.update(build["id"], semantic_collection=collection)
            while completed < len(rows):
                if store.get(build["id"])["status"] == "cancelled":
                    return False
                batch = []
                batch_new = 0
                batch_cached = 0
                for row in rows[completed : completed + EMBEDDING_BATCH_SIZE]:
                    if row["vector"] is None:
                        if new_embeddings + batch_new >= limit:
                            break
                        batch_new += 1
                    else:
                        if cached_embeddings + batch_cached >= cached_limit:
                            break
                        batch_cached += 1
                    batch.append(row)
                if not batch:
                    break
                missing = [row for row in batch if row["vector"] is None]
                # Bound worst-case request size and per-input token exposure.
                texts = [(row["path"] + "\n" + row["text"])[:2000] for row in missing]
                vectors = embeddings.embed_texts(texts) if texts else []
                new_vectors = {
                    row["hash"]: vector for row, vector in zip(missing, vectors, strict=True)
                }
                if missing:
                    with store.connect(write=True) as db:
                        db.executemany(
                            "INSERT OR REPLACE INTO embeddings VALUES(?,?,?,?)",
                            (
                                (build["repo"], fingerprint, key, json.dumps(value))
                                for key, value in new_vectors.items()
                            ),
                        )
                    metrics["embedding_requests"] = metrics.get("embedding_requests", 0) + 1
                    metrics["embedding_characters"] = metrics.get("embedding_characters", 0) + sum(
                        map(len, texts)
                    )
                    metrics["embedding_tokens"] = metrics.get(
                        "embedding_tokens", 0
                    ) + embeddings.last_usage.get("total_tokens", 0)
                    metrics["embedding_usage_reported"] = "total_tokens" in embeddings.last_usage
                metrics["embedding_cache_hits"] = (
                    metrics.get("embedding_cache_hits", 0) + len(batch) - len(missing)
                )
                new_embeddings += len(missing)
                cached_embeddings += len(batch) - len(missing)
                # A separate point per snapshot makes deletion and query filtering exact.
                points = [
                    {
                        "id": str(uuid5(NAMESPACE_URL, build["id"] + ":" + row["hash"])),
                        "vector": new_vectors[row["hash"]]
                        if row["vector"] is None
                        else json.loads(row["vector"]),
                        "payload": {"snapshot": build["id"], "hash": row["hash"]},
                    }
                    for row in batch
                ]
                store.update(build["id"], metrics=metrics)
                client.upsert_points(collection, points=points, wait=True)
                completed += len(batch)
                metrics["embedded_chunks"] = completed
                store.update(build["id"], metrics=metrics)
                if time.monotonic() >= deadline:
                    break
        return completed >= len(rows)

    def search(self, build: dict, query: str, *, limit: int = 200) -> list[str]:
        if self.fingerprint(build["directory"]) != build["embedding"]:
            raise ValueError("Snapshot uses different embedding settings")
        _, client = self._clients(build["directory"])
        with client:
            points = client.query_points(
                build["semantic_collection"],
                query_vector=self.embed(build["directory"], query),
                filter={"must": [{"key": "snapshot", "match": {"value": build["id"]}}]},
                limit=limit,
            )
        return [
            str(point.payload["hash"])
            for point in points
            if point.payload and point.payload.get("snapshot") == build["id"]
        ]

    def delete(self, store, build: dict) -> None:
        if self.fingerprint(build["directory"]) != build["embedding"]:
            raise ValueError("Old embedding store requires an explicit migration before cleanup")
        with store.connect() as db:
            hashes = [
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT hash FROM chunks WHERE snapshot=?", (build["id"],)
                )
            ]
        _, client = self._clients(build["directory"])
        with client:
            for start in range(0, len(hashes), 100):
                client.delete_points(
                    build["semantic_collection"],
                    point_ids=[
                        str(uuid5(NAMESPACE_URL, build["id"] + ":" + value))
                        for value in hashes[start : start + 100]
                    ],
                    wait=True,
                )
