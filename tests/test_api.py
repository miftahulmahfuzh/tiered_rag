from fastapi.testclient import TestClient

from tests._helpers import build_orchestrator
from tiered_rag.api import create_app, get_cache, get_orchestrator
from tiered_rag.llm.client import FakeLLM
from tiered_rag.verifier import Verifier


def _client_with_orchestrator(orch):
    # these tests predate the cache and exercise the non-cached path -> disable the cache
    app = create_app()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_cache] = lambda: None
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


def test_chat_escalates_and_alerts_on_unverified_answer(fake_embedder):
    reject = Verifier(FakeLLM('{"supported": false, "reason": "ungrounded"}'))
    client = _client_with_orchestrator(build_orchestrator(fake_embedder, 1, "faq", verifier=reject))
    body = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert body["pending_review"] is True
    assert body["verified"] is False
    assert "Pending Human Specialist Review" in body["answer"]
    # async alert fired (TestClient runs BackgroundTasks on response)
    assert len(client.app.state.alerter.alerts) == 1
    assert client.app.state.alerter.alerts[0].kind == "unverified"


def test_chat_supported_answer_has_no_alert(fake_embedder):
    approve = Verifier(FakeLLM('{"supported": true, "reason": "ok"}'))
    client = _client_with_orchestrator(build_orchestrator(fake_embedder, 1, "faq", verifier=approve))
    body = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert body["verified"] is True
    assert body["pending_review"] is False
    assert len(client.app.state.alerter.alerts) == 0


def test_chat_caches_and_serves_a_repeat_query(client_with_inmemory_cache):
    client, spy = client_with_inmemory_cache         # spy counts orchestrator.run calls
    first = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert first["cached"] is False
    second = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert second["cached"] is True
    assert second["answer"] == first["answer"]
    assert second["usage"]["total_tokens"] == 0      # a cache hit costs no tokens
    assert spy.calls == 1                             # orchestrator ran only for the cold miss


def test_stats_endpoint_reports_by_tier_savings_and_cache(fake_embedder):
    client = _client_with_orchestrator(build_orchestrator(fake_embedder, 2))
    client.post("/chat", json={"query": "full details for SKU-07"})
    stats = client.get("/stats").json()
    assert "by_tier" in stats and "savings" in stats and "cache" in stats
    assert "savings_pct" in stats["savings"]
    assert "hit_rate" in stats["cache"]


def test_chat_does_not_cache_escalations(fake_embedder):
    # an escalated (pending_review) answer must NOT be cached -> the gap keeps alerting
    from tests._helpers import build_cached_client
    reject = Verifier(FakeLLM('{"supported": false, "reason": "ungrounded"}'))
    client, spy = build_cached_client(fake_embedder, 1, "faq", verifier=reject)
    first = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert first["pending_review"] is True and first["cached"] is False
    second = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert second["cached"] is False                  # escalation not cached -> orchestrator ran again
    assert spy.calls == 2


def test_telegram_webhook_replies_with_chat_answer(fake_embedder):
    from tests._helpers import FakeTelegramClient, build_cached_client
    from tiered_rag.api import get_telegram
    client, spy = build_cached_client(fake_embedder, 1, "faq")
    tg = FakeTelegramClient()
    client.app.dependency_overrides[get_telegram] = lambda: tg
    update = {"update_id": 1, "message": {"chat": {"id": 99}, "text": "how do I reset my password"}}
    body = client.post("/telegram/webhook", json=update).json()
    assert body == {"ok": True}
    assert tg.sent and tg.sent[0][0] == 99          # replied to the right chat
    assert tg.sent[0][1]                            # the pipeline produced a non-empty answer
    assert spy.calls == 1                           # the shared chat pipeline ran


def test_telegram_webhook_ignores_non_message_update(fake_embedder):
    from tests._helpers import FakeTelegramClient, build_cached_client
    from tiered_rag.api import get_telegram
    client, _ = build_cached_client(fake_embedder, 1, "faq")
    tg = FakeTelegramClient()
    client.app.dependency_overrides[get_telegram] = lambda: tg
    body = client.post("/telegram/webhook", json={"update_id": 2}).json()
    assert body == {"ok": True} and tg.sent == []


def test_telegram_webhook_rejects_bad_secret(fake_embedder, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t")
    from tests._helpers import FakeTelegramClient, build_cached_client
    from tiered_rag.api import get_telegram
    client, _ = build_cached_client(fake_embedder, 1, "faq")
    tg = FakeTelegramClient()
    client.app.dependency_overrides[get_telegram] = lambda: tg
    update = {"message": {"chat": {"id": 1}, "text": "hi"}}
    body = client.post("/telegram/webhook", json=update,
                       headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).json()
    assert body["ok"] is False and tg.sent == []
