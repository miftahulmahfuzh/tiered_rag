import logging

from tiered_rag.config import Settings
from tiered_rag.llm.usage import TokenUsage
from tiered_rag.observability import UsageLog, estimate_cost


def test_cost_increases_with_tier():
    s = Settings()
    u = TokenUsage(prompt_tokens=1000, completion_tokens=1000)
    c1, c2, c3 = estimate_cost(1, u, s), estimate_cost(2, u, s), estimate_cost(3, u, s)
    assert c1 > 0
    assert c3 > c2 > c1  # deeper tiers simulated as pricier


def test_usage_log_accumulates_and_emits(caplog):
    s = Settings()
    log = UsageLog()
    with caplog.at_level(logging.INFO, logger="tiered_rag.usage"):
        rec = log.record(tier=2, model="mock", usage=TokenUsage(40, 10), latency_ms=21.4, settings=s)
    assert rec.total_tokens == 50
    assert rec.cost_usd > 0
    assert len(log.records) == 1
    assert log.total_cost == rec.cost_usd
    assert any("usage" in m for m in caplog.messages)


def test_savings_vs_all_tier3_is_positive_when_routing_cheaply():
    from tiered_rag.config import Settings
    from tiered_rag.llm.usage import TokenUsage
    from tiered_rag.observability import UsageLog
    s, log = Settings(), UsageLog()
    log.record(tier=1, model="mock", usage=TokenUsage(100, 50), latency_ms=5, settings=s)
    log.record(tier=2, model="mock", usage=TokenUsage(100, 50), latency_ms=5, settings=s)
    sv = log.savings_vs_all_tier3(s)
    # tier-1/2 multipliers (1x, 3x) are cheaper than charging both at tier-3 (10x)
    assert sv["all_tier3_cost_usd"] > sv["actual_cost_usd"] > 0
    assert 0.0 < sv["savings_pct"] <= 1.0


def test_by_tier_groups_records():
    from tiered_rag.config import Settings
    from tiered_rag.llm.usage import TokenUsage
    from tiered_rag.observability import UsageLog
    s, log = Settings(), UsageLog()
    log.record(tier=1, model="mock", usage=TokenUsage(10, 5), latency_ms=1, settings=s)
    log.record(tier=1, model="mock", usage=TokenUsage(10, 5), latency_ms=3, settings=s)
    bt = log.by_tier()
    assert bt[1]["requests"] == 2 and bt[1]["total_tokens"] == 30


def test_cache_stats_counts_hits():
    from tiered_rag.config import Settings
    from tiered_rag.llm.usage import TokenUsage
    from tiered_rag.observability import UsageLog
    s, log = Settings(), UsageLog()
    log.record(tier=1, model="mock", usage=TokenUsage(10, 5), latency_ms=1, settings=s, cached=False)
    log.record(tier=1, model="mock", usage=TokenUsage(0, 0), latency_ms=0, settings=s, cached=True)
    cs = log.cache_stats()
    assert cs["cache_hits"] == 1 and cs["cache_misses"] == 1 and cs["hit_rate"] == 0.5
