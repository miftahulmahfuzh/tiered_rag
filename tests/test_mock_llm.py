import json

from fastapi.testclient import TestClient

from tiered_rag.mock_llm import ROUTER_MARKER, create_mock_app
from tiered_rag.router import ROUTER_SYSTEM


def _post(client, system, user):
    return client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]},
    )


def test_router_marker_present_in_real_prompt():
    # guard: the mock detects routing requests by this substring
    assert ROUTER_MARKER in ROUTER_SYSTEM


def test_healthz_reports_tier():
    assert TestClient(create_mock_app(2)).get("/healthz").json() == {"status": "ok", "tier": 2}


def test_tier1_mock_returns_routing_json_with_usage():
    resp = _post(TestClient(create_mock_app(1)), ROUTER_SYSTEM, "what's the status of order #123?")
    body = resp.json()
    sel = json.loads(body["choices"][0]["message"]["content"])
    assert sel["tier"] == 2  # "order" -> tier 2 by the heuristic
    usage = body["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_tier2_mock_returns_canned_answer():
    resp = _post(TestClient(create_mock_app(2)), "you are a tier-2 assistant", "look up SKU-42")
    content = resp.json()["choices"][0]["message"]["content"]
    assert "mock tier-2" in content.lower()


def test_verifier_marker_present_in_real_prompt():
    from tiered_rag.verifier import VERIFIER_MARKER, VERIFIER_SYSTEM
    assert VERIFIER_MARKER in VERIFIER_SYSTEM


def test_tier1_mock_returns_supported_verdict_for_verifier_prompt():
    import json

    from fastapi.testclient import TestClient

    from tiered_rag.mock_llm import create_mock_app
    from tiered_rag.verifier import VERIFIER_SYSTEM
    client = TestClient(create_mock_app(1))
    body = {"model": "mock", "messages": [
        {"role": "system", "content": VERIFIER_SYSTEM},
        {"role": "user", "content": "SOURCES:\n...\n\nANSWER:\n...\n\nQUESTION:\n..."}]}
    content = client.post("/v1/chat/completions", json=body).json()["choices"][0]["message"]["content"]
    assert json.loads(content)["supported"] is True
