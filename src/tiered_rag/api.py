import time

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel

from .config import Settings, get_settings
from .llm.client import build_llm
from .observability import UsageLog
from .router import Router


class ChatRequest(BaseModel):
    query: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class ChatResponse(BaseModel):
    tier: int
    reason: str
    plan: str | None
    answer: str  # stubbed in Phase 2/3; real execution lands in Phase 4/6
    usage: Usage


def get_settings_dep() -> Settings:
    return get_settings()


def get_router() -> Router:
    s = get_settings()
    return Router(build_llm(s), temperature=s.router_temperature)


def get_usage_log(request: Request) -> UsageLog:
    return request.app.state.usage_log


def create_app() -> FastAPI:
    app = FastAPI(title="tiered_rag gateway")
    app.state.usage_log = UsageLog()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    def chat(
        req: ChatRequest,
        router: Router = Depends(get_router),
        usage_log: UsageLog = Depends(get_usage_log),
        settings: Settings = Depends(get_settings_dep),
    ):
        t0 = time.perf_counter()
        result = router.route_detailed(req.query)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        sel = result.selection
        rec = usage_log.record(
            tier=sel.tier, model=settings.openai_model,
            usage=result.usage, latency_ms=latency_ms, settings=settings,
        )
        return ChatResponse(
            tier=sel.tier, reason=sel.reason, plan=sel.plan,
            answer=f"[stub] would execute the Tier-{sel.tier} pipeline (Phase 4/6)",
            usage=Usage(
                prompt_tokens=rec.prompt_tokens, completion_tokens=rec.completion_tokens,
                total_tokens=rec.total_tokens, cost_usd=rec.cost_usd,
            ),
        )

    @app.get("/usage")
    def usage_summary(usage_log: UsageLog = Depends(get_usage_log)):
        return {"requests": len(usage_log.records), "total_cost_usd": usage_log.total_cost}

    return app


app = create_app()
