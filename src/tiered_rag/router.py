import json

from pydantic import BaseModel, Field

from .llm.client import LLMClient


class TierSelection(BaseModel):
    tier: int = Field(ge=1, le=3)
    reason: str = ""
    plan: str | None = None


ROUTER_SYSTEM = """You are the Tier-1 router for a game-store support chatbot.
Classify the user's message into exactly ONE tier, then reply with ONLY a JSON object.

Tiers:
- 1 = a greeting, a simple FAQ answerable from a knowledge base, or a single
  classification/label request.
- 2 = needs a function call or structured data lookup: order status, item price or
  item details, or account tier.
- 3 = complex multi-step troubleshooting, or a sensitive/escalation complaint.

Reply with JSON only (no prose, no markdown fence):
{"tier": <1|2|3>, "reason": "<short reason>", "plan": null}
"""


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        # drop the opening fence (``` or ```json) and the closing fence
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])


class Router:
    def __init__(self, llm: LLMClient, temperature: float = 0.0):
        self.llm, self.temperature = llm, temperature

    def route(self, query: str) -> TierSelection:
        raw = self.llm.complete(ROUTER_SYSTEM, query, temperature=self.temperature).content
        try:
            return TierSelection(**_extract_json(raw))
        except Exception:
            return TierSelection(tier=1, reason="router parse fallback", plan=None)
