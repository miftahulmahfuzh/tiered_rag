from __future__ import annotations

from dataclasses import dataclass


def estimate_tokens(text: str) -> int:
    """Deterministic, offline token estimate (~4 chars/token); 0 for empty."""
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @classmethod
    def estimate(cls, prompt: str, completion: str) -> "TokenUsage":
        return cls(estimate_tokens(prompt), estimate_tokens(completion))


@dataclass
class LLMResponse:
    content: str
    usage: TokenUsage
