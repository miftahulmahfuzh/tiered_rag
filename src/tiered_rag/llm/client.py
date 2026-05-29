from __future__ import annotations

from typing import Callable, Protocol

import httpx


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str: ...


class FakeLLM:
    """Deterministic LLM for offline tests.

    `responder` is either a fixed string returned for every call, or a callable
    `(system, user) -> str` so tests can vary the reply by prompt.
    """

    def __init__(self, responder: str | Callable[[str, str], str]):
        self.responder = responder

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
        if callable(self.responder):
            return self.responder(system, user)
        return self.responder


class OpenAICompatLLM:
    """Calls any OpenAI-compatible /chat/completions endpoint.

    Real OpenAI in Phase 2; the Phase-3 mock tier servers implement the same API.
    """

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> str:
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
        return r.json()["choices"][0]["message"]["content"]


def build_llm(settings) -> LLMClient:
    if settings.llm_type == "mock":
        return OpenAICompatLLM(settings.mock_llm_base_url, "mock-key", settings.openai_model)
    return OpenAICompatLLM(settings.openai_base_url, settings.openai_api_key, settings.openai_model)
