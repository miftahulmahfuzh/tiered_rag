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
