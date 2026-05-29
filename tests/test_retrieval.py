from qdrant_client import QdrantClient

from tiered_rag.ingest import ingest
from tiered_rag.retrieval import Retriever
from tiered_rag.vector_store import QdrantStore


def _retriever(fake_embedder, threshold):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    ingest([
        {"id": 1, "question": "how do I reset my password",
         "answer": "Open Settings > Security > Reset.", "category": "Account"},
    ], store, fake_embedder)
    return Retriever(store, fake_embedder, threshold=threshold)


def test_confident_hit_returns_answer(fake_embedder):
    # exact-match query -> cosine 1.0 with FakeEmbedder -> above any threshold < 1
    r = _retriever(fake_embedder, threshold=0.6).retrieve("how do I reset my password")
    assert r.abstain is False
    assert r.answer == "Open Settings > Security > Reset."
    assert r.score >= 0.99


def test_low_confidence_triggers_i_dont_know(fake_embedder):
    # unrelated query + impossibly high threshold -> must abstain
    r = _retriever(fake_embedder, threshold=0.999).retrieve("what is the capital of France")
    assert r.abstain is True
    assert r.answer is None
