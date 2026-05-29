import json

from qdrant_client import QdrantClient

from tiered_rag.ingest import ingest
from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import Orchestrator
from tiered_rag.retrieval import Retriever
from tiered_rag.router import Router
from tiered_rag.vector_store import QdrantStore

CATALOG = {"SKU-07": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
                      "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"}}


def _retriever(fake_embedder):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    ingest([{"id": 1, "question": "how do I reset my password",
             "answer": "Open Settings > Security > Reset.", "category": "Account"}],
           store, fake_embedder)
    return Retriever(store, fake_embedder, threshold=0.6)


def _orchestrator(fake_embedder, route_tier, route_plan=None):
    router = Router(FakeLLM(json.dumps({"tier": route_tier, "reason": "x", "plan": route_plan})))
    # tier-1 LLM echoes context; tier-2 LLM returns a plan then echoes context
    def llm_for(tier):
        if tier == 2:
            def r(system, user):
                return (json.dumps({"calls": [{"tool": "get_item_details_from_xlsx",
                                               "args": {"item_id": "SKU-07"}}]})
                        if "plan" in system.lower() else user)
            return FakeLLM(r)
        return FakeLLM(lambda s, u: u)
    return Orchestrator(router, _retriever(fake_embedder), CATALOG, llm_for)


def test_orchestrator_tier1_faq(fake_embedder):
    res = _orchestrator(fake_embedder, 1, "faq").run("how do I reset my password")
    assert res.tier == 1
    assert "Open Settings > Security > Reset." in res.answer
    assert res.usage.total_tokens > 0   # routing + synthesis aggregated


def test_orchestrator_tier2(fake_embedder):
    res = _orchestrator(fake_embedder, 2).run("full details for SKU-07")
    assert res.tier == 2
    assert "Dragon Skin" in res.answer


def test_orchestrator_tier3_is_stub(fake_embedder):
    res = _orchestrator(fake_embedder, 3).run("everything is broken, escalate")
    assert res.tier == 3
    assert "stub" in res.answer.lower()
