import pytest
from pydantic import ValidationError

from tiered_rag.router import TierSelection


def test_valid_selection():
    sel = TierSelection(tier=2, reason="needs an order lookup")
    assert sel.tier == 2
    assert sel.plan is None  # optional, defaults None


def test_tier_must_be_1_to_3():
    with pytest.raises(ValidationError):
        TierSelection(tier=4, reason="too big")
    with pytest.raises(ValidationError):
        TierSelection(tier=0, reason="too small")


def test_parses_from_json_dict():
    sel = TierSelection(**{"tier": 1, "reason": "greeting", "plan": None})
    assert sel.tier == 1
