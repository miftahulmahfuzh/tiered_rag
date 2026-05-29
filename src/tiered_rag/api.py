import time

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from pydantic import BaseModel
from qdrant_client import QdrantClient

from .alerting import Alerter
from .cache import RedisCacheBackend, SemanticCache, cacheable
from .config import Settings, get_settings
from .embeddings import OllamaEmbedder
from .knowledge_base import catalog_index, load_item_details
from .llm.client import build_llm
from .llm.usage import TokenUsage
from .observability import UsageLog
from .orchestrator import Orchestrator
from .retrieval import Retriever
from .router import Router
from .telegram import TelegramClient, extract_message
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
    cached: bool = False


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


def get_cache(request: Request) -> SemanticCache | None:
    """Build the real (Redis-backed) semantic cache lazily; tests override this dependency.

    Returns None when caching is disabled. The Redis client is created lazily and connects
    only on the first command, so building the cache offline never opens a socket.
    """
    s = get_settings()
    if not s.cache_enabled:
        return None
    cache = getattr(request.app.state, "cache", None)
    if cache is None:
        import redis  # local import: redis is only needed on the real path

        backend = RedisCacheBackend(
            redis.Redis.from_url(s.redis_url, decode_responses=True),
            prefix=s.cache_key_prefix, ttl=s.cache_ttl_seconds, max_entries=s.cache_max_entries,
        )
        cache = SemanticCache(OllamaEmbedder(s.ollama_host, s.embed_model), backend,
                              s.cache_similarity_threshold)
        request.app.state.cache = cache
    return cache


def get_telegram(request: Request) -> TelegramClient | None:
    """Lazily build the Telegram client from settings; tests override this dependency.

    Returns None when no bot token is configured, so the webhook stays a no-op offline.
    """
    cli = getattr(request.app.state, "telegram", None)
    if cli is None:
        s = get_settings()
        if not s.telegram_bot_token:
            return None
        cli = TelegramClient(s.telegram_bot_token, s.telegram_api_base)
        request.app.state.telegram = cli
    return cli


def process_query(
    query: str,
    *,
    orchestrator: Orchestrator,
    usage_log: UsageLog,
    cache: SemanticCache | None,
    settings: Settings,
    alerter: Alerter,
    background_tasks: BackgroundTasks,
) -> ChatResponse:
    """Run a query through the full Phase-1–7 pipeline and return the chat response.

    Single source of truth shared by POST /chat and POST /telegram/webhook, so both
    transports produce byte-for-byte identical answers (cache get/put + usage record +
    async guardrail alert).
    """
    # --- semantic cache lookup: a near-duplicate of a past query is served at 0 tokens ---
    if cache is not None:
        hit = cache.get(query)
        if hit is not None:
            usage_log.record(
                tier=hit["tier"], model=settings.openai_model,
                usage=TokenUsage(0, 0), latency_ms=0.0, settings=settings, cached=True,
            )
            return ChatResponse(
                tier=hit["tier"], reason=hit.get("reason", ""), plan=hit.get("plan"),
                answer=hit["answer"], verified=hit.get("verified"), pending_review=False,
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0, cost_usd=0.0),
                cached=True,
            )

    t0 = time.perf_counter()
    res = orchestrator.run(query)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    rec = usage_log.record(
        tier=res.tier, model=settings.openai_model,
        usage=res.usage, latency_ms=latency_ms, settings=settings,
    )
    if res.gap is not None:
        background_tasks.add_task(alerter.alert, res.gap)   # async knowledge-gap alert
    # --- only *served* answers are cached (never abstain/escalation) ---
    if cache is not None and cacheable(res):
        cache.put(query, {"answer": res.answer, "tier": res.tier, "reason": res.reason,
                          "plan": res.plan, "verified": res.verified})
    return ChatResponse(
        tier=res.tier, reason=res.reason, plan=res.plan, answer=res.answer,
        verified=res.verified,
        pending_review=(res.gap is not None and res.gap.kind == "unverified"),
        usage=Usage(
            prompt_tokens=rec.prompt_tokens, completion_tokens=rec.completion_tokens,
            total_tokens=rec.total_tokens, cost_usd=rec.cost_usd,
        ),
        cached=False,
    )


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
        cache: SemanticCache | None = Depends(get_cache),
    ):
        return process_query(req.query, orchestrator=orchestrator, usage_log=usage_log,
                             cache=cache, settings=settings, alerter=alerter,
                             background_tasks=background_tasks)

    @app.post("/telegram/webhook")
    def telegram_webhook(
        update: dict,
        request: Request,
        background_tasks: BackgroundTasks,
        orchestrator: Orchestrator = Depends(get_orchestrator),
        usage_log: UsageLog = Depends(get_usage_log),
        alerter: Alerter = Depends(get_alerter),
        settings: Settings = Depends(get_settings_dep),
        cache: SemanticCache | None = Depends(get_cache),
        telegram: TelegramClient | None = Depends(get_telegram),
    ):
        # 1. validate the shared secret (defence-in-depth; Telegram echoes it in this header)
        if settings.telegram_webhook_secret:
            got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if got != settings.telegram_webhook_secret:
                return {"ok": False, "error": "bad secret"}   # 200 so Telegram won't retry a forgery
        # 2. parse; ignore anything that isn't a text message
        parsed = extract_message(update)
        if parsed is None or telegram is None:
            return {"ok": True}
        chat_id, text = parsed

        # 3. do the slow work AFTER responding, so Telegram never times out (it retries on slow/non-200)
        def _handle():
            resp = process_query(text, orchestrator=orchestrator, usage_log=usage_log,
                                 cache=cache, settings=settings, alerter=alerter,
                                 background_tasks=background_tasks)
            telegram.send_message(chat_id, resp.answer)

        background_tasks.add_task(_handle)
        return {"ok": True}

    @app.get("/usage")
    def usage_summary(usage_log: UsageLog = Depends(get_usage_log)):
        return {"requests": len(usage_log.records), "total_cost_usd": usage_log.total_cost,
                "cache": usage_log.cache_stats()}

    @app.get("/stats")
    def stats(usage_log: UsageLog = Depends(get_usage_log),
              settings: Settings = Depends(get_settings_dep)):
        return {"by_tier": usage_log.by_tier(),
                "savings": usage_log.savings_vs_all_tier3(settings),
                "cache": usage_log.cache_stats()}

    return app


app = create_app()
