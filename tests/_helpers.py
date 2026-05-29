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


class _SpyOrchestrator:
    """Wraps an Orchestrator and counts .run() calls (proves a cache hit skips it)."""

    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def run(self, query):
        self.calls += 1
        return self.inner.run(query)


def build_cached_client(fake_embedder, route_tier=1, route_plan="faq", verifier=None):
    """A TestClient with an in-memory semantic cache + a counting-spy orchestrator."""
    from fastapi.testclient import TestClient

    from tiered_rag.api import create_app, get_cache, get_orchestrator
    from tiered_rag.cache import InMemoryCacheBackend, SemanticCache
    from tiered_rag.embeddings import FakeEmbedder

    spy = _SpyOrchestrator(build_orchestrator(fake_embedder, route_tier, route_plan, verifier=verifier))
    cache = SemanticCache(FakeEmbedder(64), InMemoryCacheBackend(64), threshold=0.95)
    app = create_app()
    app.dependency_overrides[get_orchestrator] = lambda: spy
    app.dependency_overrides[get_cache] = lambda: cache
    return TestClient(app), spy
