"""Per-stage token + cost breakdown over the 6-category taxonomy (deterministic).

This measures TOKEN CONSUMPTION, not routing accuracy. Each taxonomy query is run
through its CORRECT tier/path (the labeled tier), so every path is actually
exercised -- greeting and classification really make their Tier-1 synth call
instead of being misrouted by the deterministic mock router. The router still
makes its real call (its tokens are the Tier-1 "overhead"); we only override its
decision so the mock router's classification quirks don't skip a path. Routing
accuracy is reported separately in EVAL_REPORT section 2.

Tokens are grouped by the BILLING tier (the tier of the model that ran each
stage) so cost = tokens x multiplier reconciles exactly. Also prints the per-query
answer-gen vs router/verifier-overhead split and the all-Tier-3 saving baseline.

Usage:
    docker compose up -d --build      # mock workers + Qdrant + Redis
    python -m tiered_rag.ingest
    python scripts/cost_breakdown.py
"""
from qdrant_client import QdrantClient

from tiered_rag.config import Settings
from tiered_rag.embeddings import OllamaEmbedder
from tiered_rag.knowledge_base import catalog_index, load_item_details
from tiered_rag.llm.client import build_llm
from tiered_rag.llm.usage import TokenUsage
from tiered_rag.observability import estimate_cost
from tiered_rag.orchestrator import Orchestrator
from tiered_rag.retrieval import Retriever
from tiered_rag.router import RouteResult, Router, TierSelection
from tiered_rag.vector_store import QdrantStore
from tiered_rag.verifier import Verifier

# query -> (correct tier, tier-1 plan) from the labeled taxonomy
TAXONOMY = [
    ("hi there!", 1, "greeting"),
    ("how do I reset my password?", 1, "faq"),
    ("is 'I keep getting logged out' Billing, Technical, or Account?", 1, "classification"),
    ("what's the status of order #12345?", 2, None),
    ("give me the full details for item SKU-07", 2, None),
    ("I was double-charged, the refund failed, and now I'm locked out", 3, None),
]


class OracleRouter(Router):
    """Calls the real router (so its tokens count as Tier-1 overhead) but overrides the
    decision with the labeled tier/plan, so every path is actually exercised regardless
    of the deterministic mock router's classification quirks. This is a cost measurement
    (token consumption), not a routing-accuracy measurement (that is section 2)."""

    def __init__(self, llm, plan_map, temperature: float = 0.0):
        super().__init__(llm, temperature)
        self.plan_map = plan_map

    def route_detailed(self, query: str) -> RouteResult:
        real = super().route_detailed(query)   # real LLM call -> real Tier-1 overhead tokens
        tier, plan = self.plan_map[query]
        return RouteResult(
            selection=TierSelection(tier=tier, reason="labeled (cost measurement)", plan=plan),
            usage=real.usage,
        )


def _add(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    return TokenUsage(a.prompt_tokens + b.prompt_tokens, a.completion_tokens + b.completion_tokens)


def main() -> None:
    s = Settings(llm_type="mock")   # force the deterministic mock backend
    store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
    retriever = Retriever(store, OllamaEmbedder(s.ollama_host, s.embed_model), s.confidence_threshold)
    catalog = catalog_index(load_item_details(s.item_details_path))
    verifier = Verifier(build_llm(s, 1)) if s.verify_answers else None
    plan_map = {q: (t, p) for q, t, p in TAXONOMY}
    router = OracleRouter(build_llm(s, 1), plan_map, temperature=s.router_temperature)
    orch = Orchestrator(router, retriever, catalog, llm_for=lambda t: build_llm(s, t),
                        verifier=verifier, tier3_max_steps=s.tier3_max_steps)

    billed = {1: TokenUsage(), 2: TokenUsage(), 3: TokenUsage()}   # tokens billed at each tier
    total = TokenUsage()
    answer_total = TokenUsage()                                    # answer-generation only (planner+synth)
    print("per-query split (answer-gen = executor; overhead = router+verifier @ Tier-1):")
    print("  tier | answer in/out | overhead in/out | query")
    for q, _t, _p in TAXONOMY:
        res = orch.run(q)
        for t, u in res.usage_by_tier.items():
            billed[t] = _add(billed[t], u)
        total = _add(total, res.usage)
        answer_total = _add(answer_total, res.answer_usage)
        ans, tot = res.answer_usage, res.usage
        oh_in, oh_out = tot.prompt_tokens - ans.prompt_tokens, tot.completion_tokens - ans.completion_tokens
        print(f"  {res.tier}    | {ans.prompt_tokens:4d}/{ans.completion_tokens:<3d}   | "
              f"{oh_in:4d}/{oh_out:<3d}      | {q}")

    overhead = TokenUsage(total.prompt_tokens - answer_total.prompt_tokens,
                          total.completion_tokens - answer_total.completion_tokens)
    actual = round(sum(estimate_cost(t, billed[t], s) for t in (1, 2, 3)), 8)
    all_tier3 = round(estimate_cost(1, overhead, s) + estimate_cost(3, answer_total, s), 8)
    saving = (all_tier3 - actual) / all_tier3 if all_tier3 else 0.0

    print("\nbilled-at tier  | in_tok | out_tok | mult | cost")
    mult = {1: 1, 2: 3, 3: 10}
    for t in (1, 2, 3):
        u = billed[t]
        print(f"  Tier {t}        | {u.prompt_tokens:6d} | {u.completion_tokens:6d} | {mult[t]:3d}x | "
              f"${estimate_cost(t, u, s):.8f}")
    print(f"  TOTAL (actual)= ${actual:.8f}")
    print(f"\noverhead (router+verifier, Tier-1): in={overhead.prompt_tokens} out={overhead.completion_tokens}")
    print(f"answer-generation (planner+synth): in={answer_total.prompt_tokens} out={answer_total.completion_tokens}")
    print(f"all-Tier-3 baseline = ${all_tier3:.8f}  (overhead stays Tier-1, answer-gen at 10x)")
    print(f"saving = {saving * 100:.1f}%")


if __name__ == "__main__":
    main()
