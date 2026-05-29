from qdrant_client import QdrantClient

from tiered_rag.ingest import ingest
from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import I_DONT_KNOW, ExecutionResult, Tier1Executor
from tiered_rag.retrieval import Retriever
from tiered_rag.vector_store import QdrantStore


def _retriever(fake_embedder, threshold):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    ingest([{"id": 1, "question": "how do I reset my password",
             "answer": "Open Settings > Security > Reset.", "category": "Account"}],
           store, fake_embedder)
    return Retriever(store, fake_embedder, threshold=threshold)


def test_greeting_answers_without_rag(fake_embedder):
    ex = Tier1Executor(_retriever(fake_embedder, 0.6), FakeLLM("Hi! How can I help?"))
    res = ex.execute("hi there!", plan="greeting")
    assert isinstance(res, ExecutionResult)
    assert res.answer == "Hi! How can I help?"
    assert res.final_input_context == ""        # no RAG for greetings
    assert res.usage.total_tokens > 0


def test_faq_synthesizes_from_retrieved_context(fake_embedder):
    # FakeLLM echoes the user message so we can prove the context was passed in
    ex = Tier1Executor(_retriever(fake_embedder, 0.6), FakeLLM(lambda s, u: u))
    res = ex.execute("how do I reset my password", plan="faq")
    assert "Open Settings > Security > Reset." in res.final_input_context
    assert "Open Settings > Security > Reset." in res.answer   # grounded in context


def test_faq_abstains_below_threshold_without_calling_llm(fake_embedder):
    def _boom(s, u):
        raise AssertionError("LLM must not be called when retrieval abstains")
    ex = Tier1Executor(_retriever(fake_embedder, 0.999), FakeLLM(_boom))
    res = ex.execute("what is the capital of France", plan="faq")
    assert res.answer == I_DONT_KNOW
    assert res.final_input_context == ""


def test_unknown_plan_defaults_to_faq(fake_embedder):
    ex = Tier1Executor(_retriever(fake_embedder, 0.6), FakeLLM(lambda s, u: u))
    res = ex.execute("how do I reset my password", plan=None)
    assert "Open Settings > Security > Reset." in res.answer
