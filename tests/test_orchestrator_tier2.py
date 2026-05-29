import json

from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import Tier2Executor

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
