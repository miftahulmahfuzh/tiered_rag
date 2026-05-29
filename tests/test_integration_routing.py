import sys

import pytest

from tiered_rag.config import get_settings
from tiered_rag.eval_routing import evaluate
from tiered_rag.llm.client import build_llm
from tiered_rag.router import Router

sys.path.insert(0, "tests")
from data.routing_questions import ROUTING_QUESTIONS  # noqa: E402

pytestmark = pytest.mark.integration

ACCURACY_BAR = 0.80


def test_real_routing_accuracy():
    s = get_settings()
    if s.llm_type == "openai" and not s.openai_api_key:
        pytest.skip("no OPENAI_API_KEY set")
    router = Router(build_llm(s), temperature=s.router_temperature)
    m = evaluate(router, ROUTING_QUESTIONS)
    print(f"\nrouting accuracy = {m['accuracy']:.2f}")
    print(f"per-category    = {m['per_category']}")
    assert m["accuracy"] >= ACCURACY_BAR
