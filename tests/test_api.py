import json

from fastapi.testclient import TestClient

from tiered_rag.api import create_app, get_router
from tiered_rag.llm.client import FakeLLM
from tiered_rag.router import Router


def _client_with_canned(responder):
    app = create_app()
    app.dependency_overrides[get_router] = lambda: Router(FakeLLM(responder))
    return TestClient(app)


def test_healthz_ok():
    client = TestClient(create_app())
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chat_returns_routed_tier_and_stub_answer():
    canned = json.dumps({"tier": 2, "reason": "needs a lookup", "plan": None})
    client = _client_with_canned(canned)
    resp = client.post("/chat", json={"query": "status of order #1?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == 2
    assert body["reason"] == "needs a lookup"
    assert "stub" in body["answer"].lower()


def test_chat_includes_token_usage():
    canned = json.dumps({"tier": 1, "reason": "greeting", "plan": None})
    body = _client_with_canned(canned).post("/chat", json={"query": "hello"}).json()
    assert body["usage"]["total_tokens"] > 0
    assert body["usage"]["cost_usd"] >= 0


def test_usage_endpoint_counts_requests_and_cost():
    canned = json.dumps({"tier": 2, "reason": "lookup", "plan": None})
    client = _client_with_canned(canned)  # fresh app -> fresh UsageLog
    client.post("/chat", json={"query": "status of order #1?"})
    client.post("/chat", json={"query": "status of order #2?"})
    summary = client.get("/usage").json()
    assert summary["requests"] == 2
    assert summary["total_cost_usd"] >= 0
