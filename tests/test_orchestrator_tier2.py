import json

from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import Orchestrator, Tier2Executor
from tiered_rag.router import Router

from tests._helpers import build_retriever

CATALOG = {"7": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
                 "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"},
           "SKU-07": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
                      "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"}}


def _planner(calls):
    """A FakeLLM that returns a plan for the plan-call and echoes context for synthesis."""
    plan_json = json.dumps({"calls": calls})

    def responder(system, user):
        return plan_json if "plan" in system.lower() else user  # synth echoes its user msg
    return FakeLLM(responder)


def test_tier2_runs_planned_tools_and_grounds_answer():
    calls = [{"tool": "get_item_details_from_xlsx", "args": {"item_id": "SKU-07"}}]
    ex = Tier2Executor(_planner(calls), CATALOG)
    res = ex.execute("give me the full details for item SKU-07")
    assert res.tier == 2
    assert res.tool_calls[0]["tool"] == "get_item_details_from_xlsx"
    assert res.tool_calls[0]["result"]["name"] == "Dragon Skin"
    assert "Dragon Skin" in res.final_input_context
    assert "Dragon Skin" in res.answer            # grounded
    assert res.usage.total_tokens > 0             # plan + synth aggregated


def test_tier2_plan_label_is_tool_pipeline():
    # explainability: tier-2 carries a stable plan label all the way to the output
    calls = [{"tool": "get_item_details_from_xlsx", "args": {"item_id": "SKU-07"}}]
    res = Tier2Executor(_planner(calls), CATALOG).execute("details for SKU-07")
    assert res.plan == "tool_pipeline"


def test_tier2_unknown_tool_does_not_crash():
    ex = Tier2Executor(_planner([{"tool": "bogus", "args": {}}]), CATALOG)
    res = ex.execute("do something weird")
    assert "error" in res.tool_calls[0]["result"]


def test_tier2_unknown_tool_name_reads_as_unknown_tool():
    ex = Tier2Executor(_planner([{"tool": "bogus", "args": {}}]), CATALOG)
    res = ex.execute("do something weird")
    assert "unknown tool" in res.tool_calls[0]["result"]["error"]


def test_tier2_existing_tool_with_bad_args_is_not_mislabeled_unknown_tool():
    # A real tool called with an unusable arg key must report a *bad argument*
    # error, never "unknown tool" — that was the SKU-07 misdiagnosis.
    ex = Tier2Executor(_planner([{"tool": "get_item_details_from_xlsx", "args": {"foo": "bar"}}]), CATALOG)
    res = ex.execute("details please")
    err = res.tool_calls[0]["result"]["error"]
    assert "unknown tool" not in err
    assert "bad arguments" in err


def test_tier2_resolves_sku_keyed_item_lookup():
    # The exact failing case: planner keys the arg 'sku'. Must resolve, not error.
    calls = [{"tool": "get_item_details_from_xlsx", "args": {"sku": "SKU-07"}}]
    res = Tier2Executor(_planner(calls), CATALOG).execute("give me the details of SKU-07")
    assert "error" not in res.tool_calls[0]["result"]
    assert res.tool_calls[0]["result"]["name"] == "Dragon Skin"
    assert "Dragon Skin" in res.answer


def test_tool_menu_advertises_exact_arg_keys():
    # Hardening: the menu must show the canonical args JSON so the LLM keys
    # them correctly (e.g. item_id) instead of guessing 'sku'.
    from tiered_rag.orchestrator import _tool_menu
    menu = _tool_menu()
    assert '{"order_id"' in menu
    assert '{"item_id"' in menu
    assert '{"account_id"' in menu


def test_tier2_unparseable_plan_yields_empty_plan():
    ex = Tier2Executor(FakeLLM("not json at all"), CATALOG)
    res = ex.execute("status of order #1?")
    assert res.tool_calls == []


# --- Fix 1 + Fix 3: no applicable tool -> RAG fallback, then abstain if the KB also misses ---

def test_tier2_empty_plan_falls_back_to_kb(fake_embedder):
    # planner finds no applicable tool -> fall back to RAG so an in-KB answer is still served
    res = Tier2Executor(_planner([]), CATALOG, build_retriever(fake_embedder)).execute(
        "how do I reset my password")
    assert res.tool_calls == []
    assert res.plan == "rag_fallback"
    assert res.abstained is False
    assert "Open Settings > Security > Reset." in res.final_input_context
    assert "Open Settings > Security > Reset." in res.answer


def test_tier2_empty_plan_with_kb_miss_abstains(fake_embedder):
    # no tool AND nothing in the KB -> abstain (so a gap alert can fire), not a silent "I don't know"
    from tiered_rag.orchestrator import I_DONT_KNOW
    res = Tier2Executor(_planner([]), CATALOG, build_retriever(fake_embedder)).execute(
        "airspeed velocity of an unladen swallow")
    assert res.abstained is True
    assert res.answer == I_DONT_KNOW
    assert res.tool_calls == []


def test_tier2_with_tool_calls_unaffected_by_fallback(fake_embedder):
    # when a tool DOES apply, behaviour is unchanged: tool_pipeline label + real steps
    calls = [{"tool": "get_item_details_from_xlsx", "args": {"item_id": "SKU-07"}}]
    res = Tier2Executor(_planner(calls), CATALOG, build_retriever(fake_embedder)).execute(
        "details for SKU-07")
    assert res.plan == "tool_pipeline"
    assert res.tool_calls[0]["result"]["name"] == "Dragon Skin"


def _tier2_orchestrator(fake_embedder, llm):
    router = Router(FakeLLM(json.dumps({"tier": 2, "reason": "x", "plan": None})))
    return Orchestrator(router, build_retriever(fake_embedder), CATALOG, llm_for=lambda t: llm)


def test_orchestrator_tier2_empty_plan_kb_hit_answers_from_kb(fake_embedder):
    res = _tier2_orchestrator(fake_embedder, _planner([])).run("how do I reset my password")
    assert res.tier == 2 and res.plan == "rag_fallback"
    assert "Open Settings > Security > Reset." in res.answer
    assert res.gap is None


def test_orchestrator_tier2_empty_plan_kb_miss_fires_abstain_gap(fake_embedder):
    res = _tier2_orchestrator(fake_embedder, _planner([])).run("airspeed of an unladen swallow")
    assert res.tier == 2 and res.abstained is True
    assert res.gap is not None and res.gap.kind == "abstain"
