from qdrant_client import QdrantClient

from tiered_rag.eval_abstention import evaluate
from tiered_rag.ingest import ingest
from tiered_rag.retrieval import Retriever
from tiered_rag.vector_store import QdrantStore


def test_metrics_shape(fake_embedder):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    ingest([{"id": 1, "question": "reset password",
             "answer": "Settings > Security.", "category": "Account"}],
           store, fake_embedder)
    r = Retriever(store, fake_embedder, threshold=0.6)
    dataset = [
        {"q": "reset password", "should_answer": True},     # exact -> answered
        {"q": "weather on mars", "should_answer": False},    # OOD -> abstain
    ]
    m = evaluate(r, dataset)
    assert 0.0 <= m["abstention_rate"] <= 1.0
    assert set(m) >= {"abstention_rate", "false_abstention_rate", "records"}
    assert len(m["records"]) == 2
