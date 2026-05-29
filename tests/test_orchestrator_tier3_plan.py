from tiered_rag.orchestrator import (
    TIER3_PLAN_MARKER,
    TIER3_PLAN_SYSTEM,
    ChainStep,
    Tier3Plan,
)


def test_marker_is_substring_of_plan_prompt():
    assert TIER3_PLAN_MARKER in TIER3_PLAN_SYSTEM


def test_chain_step_defaults():
    s = ChainStep()
    assert s.instruction == "" and s.tool is None and s.args == {}


def test_tier3_plan_parses_mixed_steps():
    plan = Tier3Plan(**{"steps": [
        {"instruction": "look up the order", "tool": "check_order_status", "args": {"order_id": "1"}},
        {"instruction": "explain the status to the user"},
    ]})
    assert len(plan.steps) == 2
    assert plan.steps[0].tool == "check_order_status"
    assert plan.steps[1].tool is None
