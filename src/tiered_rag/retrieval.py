from dataclasses import dataclass

from .embeddings import Embedder
from .vector_store import Hit, QdrantStore


@dataclass
class RetrievalResult:
    abstain: bool
    score: float
    hits: list[Hit]
    answer: str | None


class Retriever:
    def __init__(self, store: QdrantStore, embedder: Embedder, threshold: float):
        self.store, self.embedder, self.threshold = store, embedder, threshold

    def retrieve(self, query: str, limit: int = 3) -> RetrievalResult:
        hits = self.store.search(self.embedder.embed_query(query), limit=limit)
        if not hits or hits[0].score < self.threshold:
            top = hits[0].score if hits else 0.0
            return RetrievalResult(abstain=True, score=top, hits=hits, answer=None)
        return RetrievalResult(
            abstain=False, score=hits[0].score, hits=hits,
            answer=hits[0].payload["answer"],
        )
