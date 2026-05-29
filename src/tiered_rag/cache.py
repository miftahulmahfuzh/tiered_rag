from __future__ import annotations

import json
import math
from typing import Protocol

from .embeddings import Embedder
from .orchestrator import ExecutionResult


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def cacheable(res: ExecutionResult) -> bool:
    """Cache only *served* answers — never abstains or human-review escalations."""
    return not res.abstained and res.gap is None


class CacheBackend(Protocol):
    def add(self, vector: list[float], payload: dict) -> None: ...
    def scan(self) -> list[tuple[list[float], dict]]: ...


class InMemoryCacheBackend:
    def __init__(self, max_entries: int = 512):
        self.max_entries = max_entries
        self._entries: list[tuple[list[float], dict]] = []

    def add(self, vector: list[float], payload: dict) -> None:
        self._entries.append((vector, payload))
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

    def scan(self) -> list[tuple[list[float], dict]]:
        return list(self._entries)


class SemanticCache:
    def __init__(self, embedder: Embedder, backend: CacheBackend, threshold: float):
        self.embedder, self.backend, self.threshold = embedder, backend, threshold

    def get(self, query: str) -> dict | None:
        vec = self.embedder.embed_query(query)
        best_score, best_payload = self.threshold, None
        for stored_vec, payload in self.backend.scan():
            score = _cosine(vec, stored_vec)
            if score >= best_score:
                best_score, best_payload = score, payload
        return best_payload

    def put(self, query: str, payload: dict) -> None:
        self.backend.add(self.embedder.embed_query(query), {**payload, "query": query})


class RedisCacheBackend:
    def __init__(self, client, prefix: str, ttl: int, max_entries: int):
        self.client, self.prefix, self.ttl, self.max_entries = client, prefix, ttl, max_entries
        self._n = 0

    def add(self, vector: list[float], payload: dict) -> None:
        key = f"{self.prefix}:{self._n % self.max_entries}"
        self._n += 1
        self.client.hset(key, mapping={"vector": json.dumps(vector), "payload": json.dumps(payload)})
        self.client.expire(key, self.ttl)

    def scan(self) -> list[tuple[list[float], dict]]:
        out: list[tuple[list[float], dict]] = []
        for key in self.client.keys(f"{self.prefix}:*"):
            h = self.client.hgetall(key)
            vec_raw, pay_raw = h.get("vector"), h.get("payload")
            if vec_raw is None or pay_raw is None:
                continue
            out.append((json.loads(vec_raw), json.loads(pay_raw)))
        return out
