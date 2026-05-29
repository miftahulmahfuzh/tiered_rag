from qdrant_client import QdrantClient

from tiered_rag.ingest import ingest
from tiered_rag.vector_store import QdrantStore


def test_ingest_counts_and_is_searchable(fake_embedder):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    rows = [
        {"id": 1, "question": "reset my password", "answer": "Go to Settings > Security.", "category": "Account"},
        {"id": 2, "question": "track my order", "answer": "Use the Orders tab.", "category": "Orders"},
    ]
    n = ingest(rows, store, fake_embedder)
    assert n == 2
    hits = store.search(fake_embedder.embed_query("reset my password"), limit=1)
    assert hits[0].payload["answer"] == "Go to Settings > Security."
