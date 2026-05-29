from tiered_rag.llm.client import FakeLLM
from tiered_rag.llm.usage import LLMResponse, TokenUsage, estimate_tokens


def test_estimate_tokens_empty_is_zero_else_positive():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world, this is a sentence") >= 1


def test_token_usage_total_is_sum():
    u = TokenUsage(prompt_tokens=10, completion_tokens=5)
    assert u.total_tokens == 15


def test_fake_llm_returns_llmresponse_with_usage():
    resp = FakeLLM("hello there friend").complete("the system prompt", "the user query")
    assert isinstance(resp, LLMResponse)
    assert resp.content == "hello there friend"
    assert resp.usage.prompt_tokens > 0
    assert resp.usage.completion_tokens > 0
    assert resp.usage.total_tokens == resp.usage.prompt_tokens + resp.usage.completion_tokens
