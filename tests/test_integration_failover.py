"""Live failover: a down worker is skipped and a healthy live mock answers (skips if mock down)."""
import httpx
import pytest

from tiered_rag.config import get_settings
from tiered_rag.llm.client import OpenAICompatLLM
from tiered_rag.llm.failover import FailoverLLM
from tiered_rag.llm.usage import LLMResponse

pytestmark = pytest.mark.integration


def _up(base_url: str) -> bool:
    try:
        return httpx.get(base_url.replace("/v1", "") + "/healthz", timeout=2).status_code == 200
    except Exception:
        return False


def test_failover_to_live_worker_when_first_is_down():
    s = get_settings()
    if not _up(s.mock_llm_base_url):
        pytest.skip("tier-1 mock server not running on :9101")
    down = OpenAICompatLLM("http://localhost:59999/v1", "mock-key", s.openai_model, timeout=2.0)
    live = OpenAICompatLLM(s.mock_llm_base_url, "mock-key", s.openai_model, timeout=5.0)
    pool = FailoverLLM([down, live])
    resp = pool.complete("You are a support agent.", "hello there")
    assert isinstance(resp, LLMResponse) and resp.content        # the live worker answered
    assert pool.health.failures[0] >= 1                          # the down worker was recorded as failed
