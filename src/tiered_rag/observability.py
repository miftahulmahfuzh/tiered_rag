from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from .config import Settings
from .llm.usage import TokenUsage

logger = logging.getLogger("tiered_rag.usage")


def estimate_cost(tier: int, usage: TokenUsage, settings: Settings) -> float:
    multiplier = {1: 1.0, 2: settings.tier2_cost_multiplier, 3: settings.tier3_cost_multiplier}
    base = (usage.prompt_tokens / 1000.0) * settings.cost_input_per_1k \
        + (usage.completion_tokens / 1000.0) * settings.cost_output_per_1k
    return round(base * multiplier.get(tier, 1.0), 8)


def estimate_cost_breakdown(usage_by_tier: dict[int, TokenUsage], settings: Settings) -> float:
    """Cost each stage at the multiplier of the model that actually ran it. The router and
    verifier run on the tier-1 model; the planner + synthesizer on the route tier — so a
    tier-2 request is NOT a single tier-2 bill, it's tier-1 work + tier-2 work summed."""
    return round(sum(estimate_cost(t, u, settings) for t, u in usage_by_tier.items()), 8)


@dataclass
class UsageRecord:
    tier: int
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: float
    cached: bool = False


class UsageLog:
    """In-memory collector + structured logger for per-request token/cost usage."""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def record(self, *, tier: int, model: str, usage: TokenUsage,
               latency_ms: float, settings: Settings, cached: bool = False,
               usage_by_tier: dict[int, TokenUsage] | None = None) -> UsageRecord:
        # When a per-stage breakdown is supplied, bill each stage at its own tier's
        # multiplier; otherwise fall back to charging the whole usage at `tier`.
        cost = (estimate_cost_breakdown(usage_by_tier, settings) if usage_by_tier
                else estimate_cost(tier, usage, settings))
        rec = UsageRecord(
            tier=tier,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=cost,
            latency_ms=round(latency_ms, 2),
            cached=cached,
        )
        self.records.append(rec)
        logger.info("usage %s", json.dumps(asdict(rec)))
        return rec

    @property
    def total_cost(self) -> float:
        return round(sum(r.cost_usd for r in self.records), 8)

    def by_tier(self) -> dict:
        out: dict[int, dict] = {}
        for r in self.records:
            t = out.setdefault(r.tier, {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                        "total_tokens": 0, "cost_usd": 0.0, "_lat": 0.0})
            t["requests"] += 1
            t["prompt_tokens"] += r.prompt_tokens
            t["completion_tokens"] += r.completion_tokens
            t["total_tokens"] += r.total_tokens
            t["cost_usd"] = round(t["cost_usd"] + r.cost_usd, 8)
            t["_lat"] += r.latency_ms
        for t in out.values():
            t["avg_latency_ms"] = round(t.pop("_lat") / t["requests"], 2)
        return out

    def savings_vs_all_tier3(self, settings: Settings) -> dict:
        actual = sum(r.cost_usd for r in self.records)
        hypothetical = sum(
            estimate_cost(3, TokenUsage(r.prompt_tokens, r.completion_tokens), settings)
            for r in self.records
        )
        savings = hypothetical - actual
        pct = (savings / hypothetical) if hypothetical else 0.0
        return {"actual_cost_usd": round(actual, 8), "all_tier3_cost_usd": round(hypothetical, 8),
                "savings_usd": round(savings, 8), "savings_pct": round(pct, 4)}

    def cache_stats(self) -> dict:
        hits = sum(1 for r in self.records if r.cached)
        total = len(self.records)
        return {"requests": total, "cache_hits": hits, "cache_misses": total - hits,
                "hit_rate": round(hits / total, 4) if total else 0.0}
