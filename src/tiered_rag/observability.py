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
               latency_ms: float, settings: Settings, cached: bool = False) -> UsageRecord:
        rec = UsageRecord(
            tier=tier,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=estimate_cost(tier, usage, settings),
            latency_ms=round(latency_ms, 2),
            cached=cached,
        )
        self.records.append(rec)
        logger.info("usage %s", json.dumps(asdict(rec)))
        return rec

    @property
    def total_cost(self) -> float:
        return round(sum(r.cost_usd for r in self.records), 8)
