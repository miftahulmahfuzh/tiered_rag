"""Concurrent-burst smoke test against a live gateway: zero errors, all 200 (skips if down).

The headline 100+ concurrent benchmark is run from `scripts/load_test.py` against a real
gateway; this test is a guardrail that a modest concurrent burst survives cleanly.
"""
import asyncio

import httpx
import pytest

pytestmark = pytest.mark.integration

GATEWAY = "http://localhost:8000"
QUERIES = [
    "hi there!",
    "how do I reset my password?",
    "what's the status of order #12345?",
    "give me the full details for item SKU-07",
    "I was double-charged, the refund failed, and now I'm locked out",
]


def _up(url: str) -> bool:
    try:
        return httpx.get(url + "/healthz", timeout=2).status_code == 200
    except Exception:
        return False


async def _burst(n: int, concurrency: int) -> list[int]:
    codes: list[int] = []
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30) as client:
        async def one(i):
            async with sem:
                r = await client.post(f"{GATEWAY}/chat", json={"query": QUERIES[i % len(QUERIES)]})
                codes.append(r.status_code)
        await asyncio.gather(*(one(i) for i in range(n)))
    return codes


def test_gateway_survives_concurrent_burst():
    if not _up(GATEWAY):
        pytest.skip("gateway not running on :8000")
    codes = asyncio.run(_burst(n=50, concurrency=20))
    assert len(codes) == 50
    assert all(c == 200 for c in codes)        # zero errors under a concurrent burst
