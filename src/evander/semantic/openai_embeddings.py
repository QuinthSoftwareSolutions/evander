from __future__ import annotations

from typing import Any
import math

import httpx


class OpenAIEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        dimensions: int | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.last_usage: dict[str, int] = {}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpenAIEmbeddingClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def embed_text(self, text: str) -> list[float]:
        if not str(text).strip():
            raise ValueError("embedding input text is required")
        payload: dict[str, Any] = {"model": self.model, "input": text}
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions
        response = self._client.post("embeddings", json=payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenAI embeddings request failed with HTTP {response.status_code}: "
                f"{response.text.strip()}"
            )
        data = response.json()
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list) or not entries:
            raise RuntimeError("OpenAI embeddings response did not include embedding data")
        first = entries[0]
        embedding = first.get("embedding") if isinstance(first, dict) else None
        if not isinstance(embedding, list):
            raise RuntimeError("OpenAI embeddings response did not include an embedding vector")
        return [float(value) for value in embedding]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a bounded batch, preserving input order even if the provider reorders it."""
        if not texts or len(texts) > 128 or any(not text.strip() for text in texts):
            raise ValueError("Embedding batch requires 1..128 nonempty texts")
        payload: dict[str, Any] = {"model": self.model, "input": texts}
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions
        response = self._client.post("embeddings", json=payload)
        response.raise_for_status()
        data = response.json()
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list) or len(entries) != len(texts):
            raise RuntimeError("Embedding response count does not match input")
        ordered = {}
        for entry in entries:
            index = entry.get("index") if isinstance(entry, dict) else None
            vector = entry.get("embedding") if isinstance(entry, dict) else None
            if (
                type(index) is not int
                or index not in range(len(texts))
                or index in ordered
                or not isinstance(vector, list)
                or not vector
            ):
                raise RuntimeError("Invalid embedding batch response")
            values = [float(value) for value in vector]
            if any(not math.isfinite(value) for value in values) or (
                self.dimensions is not None and len(values) != self.dimensions
            ):
                raise RuntimeError("Embedding vector does not match the configured dimensions")
            ordered[index] = values
        usage = data.get("usage", {})
        self.last_usage = (
            {key: value for key, value in usage.items() if type(value) is int and value >= 0}
            if isinstance(usage, dict)
            else {}
        )
        return [ordered[index] for index in range(len(texts))]
