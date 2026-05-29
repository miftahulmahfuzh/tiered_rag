import sys

import httpx
import pytest

from tiered_rag.config import get_settings
from tiered_rag.eval_routing import evaluate
from tiered_rag.llm.client import build_llm
from tiered_rag.router import Router

sys.path.insert(0, "tests")
from data.routing_questions import ROUTING_QUESTIONS  # noqa: E402

pytestmark = pytest.mark.integration

ACCURACY_BAR = 0.60  # deterministic keyword heuristic, not a real model


def _up(base_url: str) -> bool:
    try:
        return httpx.get(base_url.replace("/v1", "") + "/healthz", timeout=2).status_code == 200
    except Exception:
        return False


def test_mock_routing_end_to_end(monkeypatch):
    if not _up(get_settings().mock_llm_base_url):
        pytest.skip("mock tier-1 server not running on :9101")
    monkeypatch.setenv("LLM_TYPE", "mock")
    s = get_settings()
    router = Router(build_llm(s), temperature=s.router_temperature)
    m = evaluate(router, ROUTING_QUESTIONS)
    print(f"\nmock routing accuracy = {m['accuracy']:.2f}")
    assert m["accuracy"] >= ACCURACY_BAR
