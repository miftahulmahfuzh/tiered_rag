import json

from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import (
    TIER3_PLAN_MARKER,
    Tier3Executor,
)

from tests._helpers import CATALOG, build_retriever


def _chain_llm(steps):
    """FakeLLM: returns the given chain plan on the plan call; echoes its user msg otherwise
    (so reasoning-step + synth outputs are inspectable and prove context threading)."""
    plan_json = json.dumps({"steps": steps})

    def responder(system, user):
        return plan_json if TIER3_PLAN_MARKER in system else user
    return FakeLLM(responder)


def test_tier3_threads_context_and_grounds_answer():
    steps = [{"instruction": "assess the issue", "tool": None, "args": {}},
             {"instruction": "recommend next steps", "tool": None, "args": {}}]
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG).execute("everything broke")
    assert res.tier == 3
    assert "[step 1]" in res.final_input_context and "[step 2]" in res.final_input_context
    # step 2's reasoning output (echoed) embeds step 1's transcript line -> context threaded forward
    assert res.final_input_context.count("[step 1] assess the issue") >= 2
    assert res.usage.total_tokens > 0                      # plan + 2 steps + synth aggregated


def test_tier3_tool_step_uses_registry():
    steps = [{"instruction": "look up the item", "tool": "get_item_details_from_xlsx",
              "args": {"item_id": "SKU-07"}}]
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG).execute("details for SKU-07")
    assert res.tool_calls[0]["tool"] == "get_item_details_from_xlsx"
    assert res.tool_calls[0]["result"]["name"] == "Dragon Skin"
    assert "Dragon Skin" in res.final_input_context        # tool result threaded into context


def test_tier3_retrieve_step_grounds_in_kb(fake_embedder):
    steps = [{"instruction": "find the policy", "tool": "retrieve",
              "args": {"query": "how do I reset my password"}}]
    res = Tier3Executor(_chain_llm(steps), build_retriever(fake_embedder), CATALOG).execute("reset help")
    assert res.tool_calls[0]["tool"] == "retrieve"
    assert "Open Settings > Security > Reset." in res.final_input_context


def test_tier3_unparseable_plan_degrades_without_crashing():
    res = Tier3Executor(FakeLLM("not json at all"), retriever=None, catalog=CATALOG).execute("huh")
    assert res.tier == 3
    assert res.tool_calls == []                            # empty plan -> no steps
    assert res.final_input_context == ""                   # nothing to ground on


def test_tier3_caps_chain_at_max_steps():
    steps = [{"tool": "get_item_details_from_xlsx", "args": {"item_id": "SKU-07"}}] * 4
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG, max_steps=2).execute("x")
    assert len(res.tool_calls) == 2                        # truncated to the first 2 steps


def test_tier3_unknown_tool_does_not_crash():
    steps = [{"instruction": "do a weird thing", "tool": "bogus", "args": {}}]
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG).execute("weird")
    assert "error" in res.tool_calls[0]["result"]


def test_tier3_existing_tool_with_bad_args_is_not_mislabeled_unknown_tool():
    steps = [{"instruction": "look up the item", "tool": "get_item_details_from_xlsx",
              "args": {"foo": "bar"}}]
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG).execute("details")
    err = res.tool_calls[0]["result"]["error"]
    assert "unknown tool" not in err and "bad arguments" in err


def test_tier3_resolves_sku_keyed_item_lookup():
    steps = [{"instruction": "look up the item", "tool": "get_item_details_from_xlsx",
              "args": {"sku": "SKU-07"}}]
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG).execute("details for SKU-07")
    assert res.tool_calls[0]["result"]["name"] == "Dragon Skin"
