from fastapi import Depends, FastAPI
from pydantic import BaseModel

from .config import get_settings
from .llm.client import build_llm
from .router import Router


class ChatRequest(BaseModel):
    query: str


class ChatResponse(BaseModel):
    tier: int
    reason: str
    plan: str | None
    answer: str  # stubbed in Phase 2; real execution lands in Phase 4/6


def get_router() -> Router:
    s = get_settings()
    return Router(build_llm(s), temperature=s.router_temperature)


def create_app() -> FastAPI:
    app = FastAPI(title="tiered_rag gateway")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, router: Router = Depends(get_router)):
        sel = router.route(req.query)
        return ChatResponse(
            tier=sel.tier,
            reason=sel.reason,
            plan=sel.plan,
            answer=f"[stub] would execute the Tier-{sel.tier} pipeline (Phase 4/6)",
        )

    return app


app = create_app()
