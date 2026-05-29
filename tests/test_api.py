from fastapi.testclient import TestClient

from tests._helpers import build_orchestrator
from tiered_rag.api import create_app, get_orchestrator


def _client_with_orchestrator(orch):
    app = create_app()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    return TestClient(app)


def test_healthz_ok():
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chat_returns_real_tier1_answer(fake_embedder):
    orch = build_orchestrator(fake_embedder, 1, "faq")
    body = _client_with_orchestrator(orch).post(
        "/chat", json={"query": "how do I reset my password"}).json()
    assert body["tier"] == 1
    assert "Open Settings > Security > Reset." in body["answer"]
    assert body["usage"]["total_tokens"] > 0


def test_chat_returns_real_tier2_answer(fake_embedder):
    orch = build_orchestrator(fake_embedder, 2)
    body = _client_with_orchestrator(orch).post(
        "/chat", json={"query": "full details for SKU-07"}).json()
    assert body["tier"] == 2
    assert "Dragon Skin" in body["answer"]
    assert body["usage"]["total_tokens"] > 0


def test_usage_endpoint_counts_requests_and_cost(fake_embedder):
    client = _client_with_orchestrator(build_orchestrator(fake_embedder, 2))  # fresh app -> fresh UsageLog
    client.post("/chat", json={"query": "full details for SKU-07"})
    client.post("/chat", json={"query": "full details for SKU-07"})
    summary = client.get("/usage").json()
    assert summary["requests"] == 2
    assert summary["total_cost_usd"] >= 0
