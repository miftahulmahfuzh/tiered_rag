from __future__ import annotations

from typing import Callable, Protocol

import httpx

from .usage import LLMResponse, TokenUsage


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> "LLMResponse": ...


class FakeLLM:
    """Deterministic LLM for offline tests.

    `responder` is either a fixed string returned for every call, or a callable
    `(system, user) -> str` so tests can vary the reply by prompt.
    """

    def __init__(self, responder: str | Callable[[str, str], str]):
        self.responder = responder

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> LLMResponse:
        content = self.responder(system, user) if callable(self.responder) else self.responder
        return LLMResponse(content=content, usage=TokenUsage.estimate(system + user, content))


class OpenAICompatLLM:
    """Calls any OpenAI-compatible /chat/completions endpoint.

    Real OpenAI in Phase 2; the Phase-3 mock tier servers implement the same API.
    """

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> LLMResponse:
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        u = data.get("usage") or {}
        if "prompt_tokens" in u and "completion_tokens" in u:
            usage = TokenUsage(u["prompt_tokens"], u["completion_tokens"])
        else:
            usage = TokenUsage.estimate(system + user, content)
        return LLMResponse(content=content, usage=usage)


def build_llm(settings, tier: int = 1) -> LLMClient:
    if settings.llm_type == "mock":
        from .failover import FailoverLLM
        urls = settings.tier_workers(tier)
        clients = [OpenAICompatLLM(u, "mock-key", settings.openai_model) for u in urls]
        return clients[0] if len(clients) == 1 else FailoverLLM(clients)
    return OpenAICompatLLM(settings.openai_base_url, settings.openai_api_key, settings.openai_model)
