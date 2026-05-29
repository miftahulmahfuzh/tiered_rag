import json

from tiered_rag.llm.client import FakeLLM
from tiered_rag.router import Router, TierSelection


def _canned(system, user):
    u = user.lower()
    if "hi" in u or "hello" in u:
        return json.dumps({"tier": 1, "reason": "greeting", "plan": None})
    if "order" in u or "price" in u:
        return json.dumps({"tier": 2, "reason": "needs a lookup", "plan": None})
    if "double-charged" in u or "locked out" in u:
        return json.dumps({"tier": 3, "reason": "multi-step", "plan": None})
    return "I am not valid JSON at all"  # malformed -> fallback


def test_router_returns_tier_selection():
    r = Router(FakeLLM(_canned))
    assert isinstance(r.route("hello there"), TierSelection)


def test_router_classifies_each_tier():
    r = Router(FakeLLM(_canned))
    assert r.route("hi there!").tier == 1
    assert r.route("what's the status of my order?").tier == 2
    assert r.route("I was double-charged and now I'm locked out").tier == 3


def test_router_handles_code_fenced_json():
    fenced = FakeLLM('```json\n{"tier": 2, "reason": "fenced"}\n```')
    assert Router(fenced).route("anything").tier == 2


def test_router_falls_back_to_tier_1_on_garbage():
    sel = Router(FakeLLM("not json")).route("blah")
    assert sel.tier == 1
    assert "fallback" in sel.reason.lower()


def test_route_detailed_exposes_usage():
    from tiered_rag.router import RouteResult
    res = Router(FakeLLM(_canned)).route_detailed("hi there!")
    assert isinstance(res, RouteResult)
    assert res.selection.tier == 1
    assert res.usage.total_tokens > 0


def test_tier1_plan_is_an_intent_label():
    canned = json.dumps({"tier": 1, "reason": "greeting", "plan": "greeting"})
    sel = Router(FakeLLM(canned)).route("hi there!")
    assert sel.tier == 1
    assert sel.plan in {"greeting", "faq", "classification"}
