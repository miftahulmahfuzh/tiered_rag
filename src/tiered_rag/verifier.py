from __future__ import annotations

from dataclasses import dataclass

from .llm.client import LLMClient
from .llm.usage import TokenUsage
from .router import _extract_json

# Stable substring the Phase-3 mock keys on (guard test in test_mock_llm).
VERIFIER_MARKER = "grounding verifier"

VERIFIER_SYSTEM = (
    "You are a strict grounding verifier for a support chatbot.\n"
    "Given SOURCES and a proposed ANSWER, decide whether EVERY factual claim in the answer is "
    "directly supported by the sources. If the answer states any fact not present in the sources, "
    "or the sources are empty/insufficient, it is NOT supported.\n"
    'Reply with JSON only (no prose, no markdown fence): '
    '{"supported": <true|false>, "reason": "<short reason>"}'
)


@dataclass
class Verdict:
    supported: bool
    reason: str = ""


def _verify_user(query: str, answer: str, evidence: str) -> str:
    return f"SOURCES:\n{evidence}\n\nANSWER:\n{answer}\n\nQUESTION:\n{query}"


class Verifier:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def verify(self, query: str, answer: str, evidence: str) -> tuple[Verdict, TokenUsage]:
        resp = self.llm.complete(VERIFIER_SYSTEM, _verify_user(query, answer, evidence))
        try:
            data = _extract_json(resp.content)
            verdict = Verdict(supported=bool(data["supported"]),
                              reason=str(data.get("reason", "")))
        except Exception:
            verdict = Verdict(supported=False, reason="verifier parse failure (fail-closed)")
        return verdict, resp.usage
