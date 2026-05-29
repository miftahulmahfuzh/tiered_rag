from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import PENDING_REVIEW
from tiered_rag.verifier import Verifier

from tests._helpers import build_orchestrator

APPROVE = Verifier(FakeLLM('{"supported": true, "reason": "grounded"}'))
REJECT = Verifier(FakeLLM('{"supported": false, "reason": "claim not in sources"}'))


def test_supported_answer_passes_through(fake_embedder):
    orch = build_orchestrator(fake_embedder, 1, "faq", verifier=APPROVE)
    res = orch.run("how do I reset my password")
    assert "Open Settings > Security > Reset." in res.answer
    assert res.verified is True
    assert res.gap is None


def test_unverified_answer_is_escalated(fake_embedder):
    orch = build_orchestrator(fake_embedder, 1, "faq", verifier=REJECT)
    res = orch.run("how do I reset my password")
    assert res.answer == PENDING_REVIEW
    assert res.verified is False
    assert res.gap is not None and res.gap.kind == "unverified"
    assert res.usage.total_tokens > 0          # router + synth + verifier folded


def test_tier2_unverified_is_escalated(fake_embedder):
    res = build_orchestrator(fake_embedder, 2, verifier=REJECT).run("full details for SKU-07")
    assert res.answer == PENDING_REVIEW
    assert res.gap.kind == "unverified"


def test_abstain_attaches_gap_but_keeps_i_dont_know(fake_embedder):
    orch = build_orchestrator(fake_embedder, 1, "faq", verifier=APPROVE)
    res = orch.run("what is the capital of France")
    from tiered_rag.orchestrator import I_DONT_KNOW
    assert res.answer == I_DONT_KNOW
    assert res.gap is not None and res.gap.kind == "abstain"
    assert res.verified is None                # never ran the verifier (no evidence)


def test_greeting_skips_verification(fake_embedder):
    orch = build_orchestrator(fake_embedder, 1, "greeting", verifier=REJECT)
    res = orch.run("hi there!")
    assert res.answer != PENDING_REVIEW        # no evidence -> verifier never runs
    assert res.verified is None
    assert res.gap is None


def test_tier3_unverified_is_escalated(fake_embedder):
    res = build_orchestrator(fake_embedder, 3, verifier=REJECT).run("complex sensitive complaint")
    assert res.answer == PENDING_REVIEW
    assert res.verified is False
    assert res.gap is not None and res.gap.kind == "unverified"
