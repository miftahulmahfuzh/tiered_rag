from tiered_rag.config import Settings
from tiered_rag.llm.client import FakeLLM, OpenAICompatLLM, build_llm


def test_fake_llm_fixed_string():
    llm = FakeLLM("hello")
    assert llm.complete("sys", "user") == "hello"


def test_fake_llm_callable_sees_prompts():
    llm = FakeLLM(lambda system, user: f"{system}|{user}")
    assert llm.complete("S", "U") == "S|U"


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
