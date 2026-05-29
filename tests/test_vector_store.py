from qdrant_client import QdrantClient

from tiered_rag.vector_store import QdrantStore


def test_upsert_then_search_returns_nearest(fake_embedder):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="t")
    store.recreate(dim=64)
    docs = ["how to reset password", "order shipping times", "refund policy"]
    store.upsert([
        {"id": i, "vector": v, "payload": {"text": d}}
        for i, (d, v) in enumerate(zip(docs, fake_embedder.embed_documents(docs)))
    ])
    hits = store.search(fake_embedder.embed_query("how to reset password"), limit=3)
    assert hits[0].payload["text"] == "how to reset password"
    assert hits[0].score == max(h.score for h in hits)
