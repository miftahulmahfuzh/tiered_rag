import time

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from pydantic import BaseModel
from qdrant_client import QdrantClient

from .alerting import Alerter
from .config import Settings, get_settings
from .embeddings import OllamaEmbedder
from .knowledge_base import catalog_index, load_item_details
from .llm.client import build_llm
from .observability import UsageLog
from .orchestrator import Orchestrator
from .retrieval import Retriever
from .router import Router
from .verifier import Verifier
from .vector_store import QdrantStore


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
    answer: str
    usage: Usage
    verified: bool | None = None
    pending_review: bool = False


def get_settings_dep() -> Settings:
    return get_settings()


def get_orchestrator() -> Orchestrator:
    s = get_settings()
    router = Router(build_llm(s, 1), temperature=s.router_temperature)
    store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
    retriever = Retriever(store, OllamaEmbedder(s.ollama_host, s.embed_model), s.confidence_threshold)
    catalog = catalog_index(load_item_details(s.item_details_path))
    verifier = Verifier(build_llm(s, 1)) if s.verify_answers else None
    return Orchestrator(router, retriever, catalog,
                        llm_for=lambda tier: build_llm(s, tier), verifier=verifier,
                        tier3_max_steps=s.tier3_max_steps)


def get_usage_log(request: Request) -> UsageLog:
    return request.app.state.usage_log


def get_alerter(request: Request) -> Alerter:
    return request.app.state.alerter


def create_app() -> FastAPI:
    app = FastAPI(title="tiered_rag gateway")
    app.state.usage_log = UsageLog()
    app.state.alerter = Alerter(get_settings().alert_webhook_url)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    def chat(
        req: ChatRequest,
        background_tasks: BackgroundTasks,
        orchestrator: Orchestrator = Depends(get_orchestrator),
        usage_log: UsageLog = Depends(get_usage_log),
        alerter: Alerter = Depends(get_alerter),
        settings: Settings = Depends(get_settings_dep),
    ):
        t0 = time.perf_counter()
        res = orchestrator.run(req.query)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        rec = usage_log.record(
            tier=res.tier, model=settings.openai_model,
            usage=res.usage, latency_ms=latency_ms, settings=settings,
        )
        if res.gap is not None:
            background_tasks.add_task(alerter.alert, res.gap)   # async knowledge-gap alert
        return ChatResponse(
            tier=res.tier, reason=res.reason, plan=res.plan, answer=res.answer,
            verified=res.verified,
            pending_review=(res.gap is not None and res.gap.kind == "unverified"),
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
