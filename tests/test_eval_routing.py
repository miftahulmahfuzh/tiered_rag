import json

from tiered_rag.eval_routing import evaluate
from tiered_rag.llm.client import FakeLLM
from tiered_rag.router import Router


def _heuristic(system, user):
    u = user.lower()
    if any(k in u for k in ["order", "price", "details", "account tier"]):
        tier = 2
    elif any(k in u for k in ["double", "failed", "locked", "and then", "step"]):
        tier = 3
    else:
        tier = 1
    return json.dumps({"tier": tier, "reason": "heuristic", "plan": None})


def test_eval_shape_and_accuracy():
    r = Router(FakeLLM(_heuristic))
    dataset = [
        {"q": "hello!", "expected_tier": 1, "category": "greeting"},
        {"q": "how do I reset my password?", "expected_tier": 1, "category": "simple_faq"},
        {"q": "what's the status of my order?", "expected_tier": 2, "category": "function_calling"},
        {"q": "the refund failed and now I'm locked out", "expected_tier": 3, "category": "multi_step"},
    ]
    m = evaluate(r, dataset)
    assert set(m) >= {"accuracy", "per_category", "confusion", "records"}
    assert 0.0 <= m["accuracy"] <= 1.0
    assert len(m["records"]) == 4
    assert m["accuracy"] == 1.0  # heuristic matches all four labels
