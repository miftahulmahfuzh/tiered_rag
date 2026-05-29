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


def test_orchestrator_tier3_runs_real_chain(fake_embedder):
    res = build_orchestrator(fake_embedder, 3).run("I was double-charged and got locked out")
    assert res.tier == 3
    assert "stub" not in res.answer.lower()
    assert "[step 1]" in res.final_input_context and "[step 2]" in res.final_input_context
    assert res.usage.total_tokens > 0          # routing + plan + steps + synth aggregated
