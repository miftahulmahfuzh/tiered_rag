import pytest

from tiered_rag.llm.client import FakeLLM
from tiered_rag.llm.failover import FailoverLLM
from tiered_rag.llm.usage import LLMResponse, TokenUsage


class DownLLM:
    """A worker that is always down (raises on every call)."""
    def complete(self, system, user, *, temperature=0.0):
        raise ConnectionError("worker down")


def test_failover_uses_next_worker_when_first_is_down():
    pool = FailoverLLM([DownLLM(), FakeLLM("healthy answer")])
    resp = pool.complete("sys", "user")
    assert isinstance(resp, LLMResponse) and resp.content == "healthy answer"


def test_failover_raises_when_all_workers_down():
    pool = FailoverLLM([DownLLM(), DownLLM()])
    with pytest.raises(ConnectionError):
        pool.complete("sys", "user")


def test_failover_deprioritizes_a_worker_that_failed():
    down = DownLLM()
    pool = FailoverLLM([down, FakeLLM("ok")])
    pool.complete("s", "u")                       # first call: worker 0 fails, falls over to worker 1
    # worker 0 now has a failure on record -> health order tries the healthy worker first next time
    assert pool.health.order()[0] == 1


def test_build_llm_wraps_multiple_workers(monkeypatch):
    from tiered_rag.config import Settings
    from tiered_rag.llm.client import build_llm
    monkeypatch.setenv("LLM_TYPE", "mock")
    monkeypatch.setenv("MOCK_TIER1_WORKERS", "http://a:9101/v1,http://b:9111/v1")
    llm = build_llm(Settings(), 1)
    assert isinstance(llm, FailoverLLM) and len(llm.workers) == 2
