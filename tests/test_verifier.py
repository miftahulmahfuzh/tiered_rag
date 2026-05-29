import json

from tiered_rag.llm.client import FakeLLM
from tiered_rag.verifier import VERIFIER_MARKER, VERIFIER_SYSTEM, Verdict, Verifier


def test_marker_is_substring_of_system_prompt():
    assert VERIFIER_MARKER in VERIFIER_SYSTEM


def test_supported_answer_passes():
    llm = FakeLLM(json.dumps({"supported": True, "reason": "fully grounded"}))
    verdict, usage = Verifier(llm).verify("q", "Open Settings > Security.", "Open Settings > Security.")
    assert isinstance(verdict, Verdict)
    assert verdict.supported is True
    assert usage.total_tokens > 0


def test_unsupported_answer_is_rejected():
    llm = FakeLLM(json.dumps({"supported": False, "reason": "claim not in sources"}))
    verdict, _ = Verifier(llm).verify("q", "It costs $5.", "name: Dragon Skin")
    assert verdict.supported is False
    assert "not in sources" in verdict.reason


def test_unparseable_verdict_fails_closed():
    verdict, _ = Verifier(FakeLLM("totally not json")).verify("q", "ans", "evidence")
    assert verdict.supported is False          # fail-closed: cannot prove grounded -> reject
    assert "fail-closed" in verdict.reason.lower()


def test_verify_passes_evidence_and_answer_to_the_llm():
    seen = {}

    def responder(system, user):
        seen["system"], seen["user"] = system, user
        return json.dumps({"supported": True, "reason": "ok"})
    Verifier(FakeLLM(responder)).verify("the question", "the answer", "the sources")
    assert VERIFIER_MARKER in seen["system"]
    assert "the sources" in seen["user"] and "the answer" in seen["user"]
