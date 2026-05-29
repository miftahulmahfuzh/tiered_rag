import httpx
import pytest

from tiered_rag.config import Settings
from tiered_rag.llm.client import FakeLLM, OpenAICompatLLM, build_llm


class _OKResponse:
    """Minimal stand-in for an httpx.Response that yields one chat completion."""

    def __init__(self, content: str = "ok"):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}], "usage": {}}


def test_fake_llm_fixed_string():
    assert FakeLLM("hello").complete("sys", "user").content == "hello"


def test_fake_llm_callable_sees_prompts():
    assert FakeLLM(lambda system, user: f"{system}|{user}").complete("S", "U").content == "S|U"


def test_build_llm_openai_backend():
    s = Settings(llm_type="openai", openai_base_url="http://x/v1",
                 openai_api_key="k", openai_model="m")
    llm = build_llm(s)
    assert isinstance(llm, OpenAICompatLLM)
    assert llm.base_url == "http://x/v1" and llm.model == "m"


def test_build_llm_mock_backend_points_at_mock_url():
    s = Settings(llm_type="mock", mock_llm_base_url="http://mock:9101/v1")
    llm = build_llm(s)
    assert isinstance(llm, OpenAICompatLLM)
    assert llm.base_url == "http://mock:9101/v1"


def test_build_llm_mock_tier_selects_port():
    s = Settings(llm_type="mock")
    assert build_llm(s).base_url.endswith(":9101/v1")          # default tier-1
    assert build_llm(s, tier=2).base_url.endswith(":9102/v1")
    assert build_llm(s, tier=3).base_url.endswith(":9103/v1")


def test_build_llm_openai_respects_per_tier_models():
    s = Settings(llm_type="openai", openai_base_url="http://x/v1", openai_api_key="k",
                 openai_model="base", openai_tier2_model="pro", openai_tier3_model="frontier")
    assert build_llm(s, 1).model == "base"      # unset tier-1 -> falls back to openai_model
    assert build_llm(s, 2).model == "pro"
    assert build_llm(s, 3).model == "frontier"


def test_complete_retries_transient_transport_error(monkeypatch):
    """A transient DNS/connect blip should be retried, not surfaced as a crash."""
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return _OKResponse("recovered")

    llm = OpenAICompatLLM("http://x/v1", "retry1", "m", max_retries=2, retry_backoff=0.0)
    monkeypatch.setattr(llm._client, "post", fake_post)
    resp = llm.complete("sys", "user")
    assert resp.content == "recovered"
    assert calls["n"] == 3                                      # failed twice, succeeded on the third


def test_complete_raises_after_exhausting_retries(monkeypatch):
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    llm = OpenAICompatLLM("http://x/v1", "retry2", "m", max_retries=2, retry_backoff=0.0)
    monkeypatch.setattr(llm._client, "post", fake_post)
    with pytest.raises(httpx.ConnectError):
        llm.complete("sys", "user")
    assert calls["n"] == 3                                      # 1 initial + 2 retries, then gives up


def test_complete_does_not_retry_http_status_errors(monkeypatch):
    """Auth/4xx errors are not transient — fail fast instead of retrying."""
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        raise httpx.HTTPStatusError("401", request=None, response=None)  # type: ignore[arg-type]

    llm = OpenAICompatLLM("http://x/v1", "retry3", "m", max_retries=2, retry_backoff=0.0)
    monkeypatch.setattr(llm._client, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        llm.complete("sys", "user")
    assert calls["n"] == 1                                      # no retries on a status error


def test_reuses_one_pooled_client_across_instances():
    """All clients share one process-level httpx.Client so DNS/connections are
    reused — even though build_llm() is called per request."""
    a = OpenAICompatLLM("http://x/v1", "pool-key", "m")
    b = OpenAICompatLLM("http://y/v1", "pool-key", "m")
    assert isinstance(a._client, httpx.Client)
    assert a._client is b._client


def test_default_retry_window_rides_out_multi_second_outage():
    """The DNS outages we saw last several seconds; defaults must span that."""
    llm = OpenAICompatLLM("http://x/v1", "window-key", "m")
    window = sum(llm.retry_backoff * (2 ** i) for i in range(llm.max_retries))
    assert llm.max_retries >= 4 and window >= 5.0
