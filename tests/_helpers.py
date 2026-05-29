"""Shared test builders for the Phase-4 orchestrator + API tests (no network)."""
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


def build_retriever(fake_embedder):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    ingest([{"id": 1, "question": "how do I reset my password",
             "answer": "Open Settings > Security > Reset.", "category": "Account"}],
           store, fake_embedder)
    return Retriever(store, fake_embedder, threshold=0.6)


def build_orchestrator(fake_embedder, route_tier, route_plan=None, verifier=None):
    router = Router(FakeLLM(json.dumps({"tier": route_tier, "reason": "x", "plan": route_plan})))

    def llm_for(tier):
        if tier == 2:
            def r(system, user):
                return (json.dumps({"calls": [{"tool": "get_item_details_from_xlsx",
                                               "args": {"item_id": "SKU-07"}}]})
                        if "plan" in system.lower() else user)
            return FakeLLM(r)
        if tier == 3:
            plan = json.dumps({"steps": [
                {"instruction": "assess the issue", "tool": None, "args": {}},
                {"instruction": "recommend next steps", "tool": None, "args": {}}]})

            def r3(system, user):
                return plan if "planner" in system.lower() else user
            return FakeLLM(r3)
        return FakeLLM(lambda s, u: u)
    return Orchestrator(router, build_retriever(fake_embedder), CATALOG, llm_for, verifier=verifier)
