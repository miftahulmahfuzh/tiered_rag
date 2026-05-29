from __future__ import annotations

import hashlib
import math
from typing import Protocol

from .http import post_with_retry, shared_client


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class FakeEmbedder:
    """Deterministic, normalized vectors derived from a hash of the text.

    Same text -> same vector (so an exact-match query yields cosine 1.0).
    Similar text need not be similar; tests control which exact strings are
    stored vs queried, so that is fine for offline unit tests.
    """

    def __init__(self, dim: int = 768):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        raw = [h[i % len(h)] - 128 for i in range(self.dim)]
        return _normalize([float(x) for x in raw])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class OllamaEmbedder:
    """Real embeddings via ollama. Prepends the nomic task prefixes internally."""

    def __init__(self, host: str, model: str, timeout: float = 60.0,
                 max_retries: int = 4, retry_backoff: float = 0.5):
        self.host, self.model, self.timeout = host.rstrip("/"), model, timeout
        self.max_retries, self.retry_backoff = max_retries, retry_backoff
        self._client = shared_client(f"ollama:{self.host}", timeout)

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        r = post_with_retry(
            self._client,
            f"{self.host}/api/embed",
            json={"model": self.model, "input": inputs},
            max_retries=self.max_retries,
            retry_backoff=self.retry_backoff,
        )
        r.raise_for_status()
        return r.json()["embeddings"]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([f"search_document: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"search_query: {text}"])[0]
