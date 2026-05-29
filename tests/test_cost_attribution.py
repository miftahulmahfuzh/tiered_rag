"""Per-stage cost attribution: the router and verifier run on the tier-1 model, so
their tokens must be billed at the tier-1 multiplier even when the request routes to
tier 2/3. Only the planner + synthesizer (the executor's own calls) are billed at the
route tier. The grand-total token count is unchanged — only the cost split changes."""
from tiered_rag.config import Settings
from tiered_rag.llm.client import FakeLLM
from tiered_rag.observability import estimate_cost, estimate_cost_breakdown
from tiered_rag.verifier import Verifier

from tests._helpers import build_orchestrator

APPROVE = Verifier(FakeLLM('{"supported": true, "reason": "grounded"}'))


def test_tier2_attributes_router_and_verifier_to_tier1(fake_embedder):
    res = build_orchestrator(fake_embedder, 2, verifier=APPROVE).run("full details for SKU-07")
    # both billing tiers present: tier-1 (router + verifier) and tier-2 (planner + synth)
    assert set(res.usage_by_tier) == {1, 2}
    assert res.usage_by_tier[1].total_tokens > 0
    assert res.usage_by_tier[2].total_tokens > 0
    # no drift: the grand total equals the sum of the per-tier breakdown
    grand = sum(u.total_tokens for u in res.usage_by_tier.values())
    assert res.usage.total_tokens == grand


def test_tier2_per_stage_cost_is_cheaper_than_lumping_at_route_tier(fake_embedder):
    s = Settings()
    res = build_orchestrator(fake_embedder, 2, verifier=APPROVE).run("full details for SKU-07")
    per_stage = estimate_cost_breakdown(res.usage_by_tier, s)
    lumped = estimate_cost(2, res.usage, s)          # old behaviour: whole aggregate at tier-2
    assert per_stage < lumped


def test_tier1_request_bills_everything_at_tier1(fake_embedder):
    res = build_orchestrator(fake_embedder, 1, "faq", verifier=APPROVE).run("how do I reset my password")
    # a tier-1 route has no deeper-tier work -> single tier-1 bucket
    assert set(res.usage_by_tier) == {1}
