from tests._helpers import build_orchestrator


def test_orchestrator_tier1_faq(fake_embedder):
    res = build_orchestrator(fake_embedder, 1, "faq").run("how do I reset my password")
    assert res.tier == 1
    assert "Open Settings > Security > Reset." in res.answer
    assert res.usage.total_tokens > 0   # routing + synthesis aggregated


def test_orchestrator_tier2(fake_embedder):
    res = build_orchestrator(fake_embedder, 2).run("full details for SKU-07")
    assert res.tier == 2
    assert "Dragon Skin" in res.answer


def test_orchestrator_tier3_is_stub(fake_embedder):
    res = build_orchestrator(fake_embedder, 3).run("everything is broken, escalate")
    assert res.tier == 3
    assert "stub" in res.answer.lower()
