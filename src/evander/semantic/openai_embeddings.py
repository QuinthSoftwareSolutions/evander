from __future__ import annotations

from typing import Any

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
