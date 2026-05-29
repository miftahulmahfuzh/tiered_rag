from __future__ import annotations

import argparse
import json
import os

from fastapi import FastAPI
from pydantic import BaseModel

from .llm.usage import estimate_tokens

# Substring of router.ROUTER_SYSTEM used to detect a routing request.
# A guard test asserts this stays in sync with the real prompt.
ROUTER_MARKER = "Tier-1 router"


def _classify(query: str) -> int:
    q = query.lower()
    if any(k in q for k in ["order", "price", "cost", "details", "sku",
                            "account tier", "stock", "rarity"]):
        return 2
    if any(k in q for k in ["double", "refund failed", "locked out", "escalate",
                            "never arrived", "bounced", "2fa", "walk me through"]):
        return 3
    return 1


def _reply(tier: int, system: str, user: str) -> str:
    if ROUTER_MARKER in system:
        chosen = _classify(user)
        return json.dumps({"tier": chosen, "reason": f"mock tier-{chosen} (deterministic)", "plan": None})
    from .verifier import VERIFIER_MARKER
    if VERIFIER_MARKER in system:
        return json.dumps({"supported": True, "reason": "mock verifier (deterministic)"})
    from .orchestrator import TIER3_PLAN_MARKER
    if TIER3_PLAN_MARKER in system:
        return json.dumps({"steps": [
            {"instruction": "assess the complaint and its sub-issues", "tool": None, "args": {}},
            {"instruction": "recommend concrete next steps", "tool": None, "args": {}}]})
    return f"[mock tier-{tier}] deterministic answer for: {user[:1000]}"


class _Msg(BaseModel):
    role: str
    content: str


class _ChatBody(BaseModel):
    model: str = "mock"
    temperature: float = 0.0
    messages: list[_Msg]


def create_mock_app(tier: int) -> FastAPI:
    app = FastAPI(title=f"mock-tier-{tier}")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "tier": tier}

    @app.post("/v1/chat/completions")
    def chat_completions(body: _ChatBody):
        system = next((m.content for m in body.messages if m.role == "system"), "")
        user = next((m.content for m in body.messages if m.role == "user"), "")
        content = _reply(tier, system, user)
        pt, ct = estimate_tokens(system + user), estimate_tokens(content)
        return {
            "id": f"mock-{tier}",
            "object": "chat.completion",
            "model": body.model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
        }

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Run one mock tier LLM server.")
    parser.add_argument("--tier", type=int, default=int(os.getenv("MOCK_TIER", "1")))
    parser.add_argument("--port", type=int, default=int(os.getenv("MOCK_PORT", "9101")))
    parser.add_argument("--host", default=os.getenv("MOCK_HOST", "0.0.0.0"))
    args = parser.parse_args()
    uvicorn.run(create_mock_app(args.tier), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
