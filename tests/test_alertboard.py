"""Offline tests for the knowledge-gap operator dashboard (alertboard).

The dashboard is a standalone FastAPI app that receives the exact JSON the
Alerter POSTs (asdict(GapAlert) = kind/query/answer/evidence/reason), keeps the
last N in memory, and renders them for a human operator. No external services.
"""
from fastapi.testclient import TestClient

from tiered_rag.alertboard import create_app, MAX_ALERTS

UNVERIFIED = {
    "kind": "unverified",
    "query": "how do I create a new account?",
    "answer": "Pending Human Specialist Review",
    "evidence": "Accounts are managed in the settings page.",
    "reason": "claim about the signup flow is not supported by the sources",
}


def _client() -> TestClient:
    return TestClient(create_app())


def test_posted_alert_is_returned_as_json():
    c = _client()
    assert c.post("/alert", json=UNVERIFIED).status_code == 200
    alerts = c.get("/alerts").json()["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "unverified"
    assert alerts[0]["query"] == UNVERIFIED["query"]
    assert alerts[0]["reason"] == UNVERIFIED["reason"]


def test_dashboard_html_shows_the_query_and_reason():
    c = _client()
    c.post("/alert", json=UNVERIFIED)
    body = c.get("/").text
    assert UNVERIFIED["query"] in body
    assert UNVERIFIED["reason"] in body
    assert "unverified" in body


def test_empty_state_renders_without_alerts():
    body = _client().get("/").text
    assert "No gaps yet" in body


def test_ring_buffer_caps_at_max_alerts():
    c = _client()
    for i in range(MAX_ALERTS + 10):
        c.post("/alert", json={**UNVERIFIED, "query": f"q{i}"})
    alerts = c.get("/alerts").json()["alerts"]
    assert len(alerts) == MAX_ALERTS


def test_newest_alert_is_shown_first():
    c = _client()
    c.post("/alert", json={**UNVERIFIED, "query": "first"})
    c.post("/alert", json={**UNVERIFIED, "query": "second"})
    alerts = c.get("/alerts").json()["alerts"]
    assert alerts[0]["query"] == "second"


def test_malformed_post_does_not_500():
    # A best-effort receiver must never reject a stray payload with a server error.
    c = _client()
    assert c.post("/alert", json={"unexpected": "shape"}).status_code == 200


def test_healthz():
    assert _client().get("/healthz").json() == {"status": "ok"}
