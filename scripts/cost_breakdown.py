"""Per-stage token + cost breakdown over the 6-category taxonomy (deterministic).

Runs each taxonomy query once through the orchestrator on the MOCK backend, then
reports tokens grouped by the BILLING tier (the tier of the model that actually ran
each stage) so that cost = tokens x multiplier reconciles exactly. Also prints the
all-Tier-3 baseline (router + verifier stay at Tier-1, only answer-generation moves
to Tier-3) and the resulting routing saving.

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
from tiered_rag.router import Router
from tiered_rag.vector_store import QdrantStore
from tiered_rag.verifier import Verifier

QUERIES = [
    "hi there!",
    "how do I reset my password?",
    "is 'I keep getting logged out' Billing, Technical, or Account?",
    "what's the status of order #12345?",
    "give me the full details for item SKU-07",
    "I was double-charged, the refund failed, and now I'm locked out",
]


def _add(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    return TokenUsage(a.prompt_tokens + b.prompt_tokens, a.completion_tokens + b.completion_tokens)


def main() -> None:
    s = Settings(llm_type="mock")   # force the deterministic mock backend
    store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
    retriever = Retriever(store, OllamaEmbedder(s.ollama_host, s.embed_model), s.confidence_threshold)
    catalog = catalog_index(load_item_details(s.item_details_path))
    verifier = Verifier(build_llm(s, 1)) if s.verify_answers else None
    orch = Orchestrator(Router(build_llm(s, 1), temperature=s.router_temperature), retriever, catalog,
                        llm_for=lambda t: build_llm(s, t), verifier=verifier,
                        tier3_max_steps=s.tier3_max_steps)

    billed = {1: TokenUsage(), 2: TokenUsage(), 3: TokenUsage()}   # tokens billed at each tier
    total = TokenUsage()
    answer_total = TokenUsage()                                    # answer-generation only (planner+synth)
    print("per-query split (answer-gen = executor; overhead = router+verifier @ Tier-1):")
    print("  tier | answer in/out | overhead in/out | query")
    for q in QUERIES:
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
