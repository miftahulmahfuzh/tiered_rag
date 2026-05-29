import httpx
import pytest
from fastapi.testclient import TestClient

from tiered_rag.api import create_app
from tiered_rag.config import get_settings

pytestmark = pytest.mark.integration


def _up(base_url):
    try:
        return httpx.get(base_url.replace("/v1", "") + "/healthz", timeout=2).status_code == 200
    except Exception:
        return False


def test_pipeline_end_to_end_via_mocks(monkeypatch):
    s = get_settings()
    if not _up(s.mock_llm_base_url):
        pytest.skip("mock tier servers not running")
    monkeypatch.setenv("LLM_TYPE", "mock")
    client = TestClient(create_app())
    body = client.post("/chat", json={"query": "give me the full details for item SKU-07"}).json()
    assert body["tier"] == 2
    assert body["usage"]["total_tokens"] > 0


def test_pipeline_guardrail_does_not_break_mocks(monkeypatch):
    s = get_settings()
    if not _up(s.mock_llm_base_url):
        pytest.skip("mock tier servers not running")
    monkeypatch.setenv("LLM_TYPE", "mock")
    client = TestClient(create_app())
    body = client.post("/chat", json={"query": "give me the full details for item SKU-07"}).json()
    assert body["tier"] == 2
    assert body["usage"]["total_tokens"] > 0
    # mock planner returns no usable tool plan -> empty context -> verification is skipped,
    # so the answer is served (not escalated). The verifier is meaningfully exercised on the
    # LLM_TYPE=openai path and in the offline guardrail tests.
    assert body["pending_review"] is False
