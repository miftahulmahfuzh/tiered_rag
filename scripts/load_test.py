"""Phase-7 load test: drive 100+ concurrent users at the /chat gateway.

Targets the deterministic LLM_TYPE=mock backend, so the numbers reflect the *gateway's*
throughput/latency, not a flaky upstream. Cycles the 6-category query taxonomy. Prints
requests/sec, p50/p95/p99 latency, and error count; then fetches /stats so the run also
reports the cost-savings vs all-Tier-3 and the cache hit-rate.

Usage:
    python scripts/load_test.py --n 300 --concurrency 100
"""
import argparse
import asyncio
import time

import httpx

QUERIES = [
    "hi there!",
    "how do I reset my password?",
    "is 'I keep getting logged out' Billing, Technical, or Account?",
    "what's the status of order #12345?",
    "give me the full details for item SKU-07",
    "I was double-charged, the refund failed, and now I'm locked out",
]


async def _worker(client, url, q, lat):
    t0 = time.perf_counter()
    r = await client.post(url, json={"query": q})
    lat.append((time.perf_counter() - t0) * 1000.0)
    return r.status_code


async def main(url, n, concurrency):
    lat, codes = [], []
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30) as client:
        async def bound(i):
            async with sem:
                codes.append(await _worker(client, url, QUERIES[i % len(QUERIES)], lat))
        t0 = time.perf_counter()
        await asyncio.gather(*(bound(i) for i in range(n)))
        elapsed = time.perf_counter() - t0
    lat.sort()
    p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))]  # noqa: E731
    errors = sum(1 for c in codes if c != 200)
    print(f"n={n} concurrency={concurrency} elapsed={elapsed:.2f}s "
          f"rps={n / elapsed:.1f} p50={p(0.5):.1f}ms p95={p(0.95):.1f}ms "
          f"p99={p(0.99):.1f}ms errors={errors}")

    # report the cost-savings + cache hit-rate the run produced
    stats_url = url.rsplit("/chat", 1)[0] + "/stats"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            stats = (await client.get(stats_url)).json()
        sv, cache = stats["savings"], stats["cache"]
        print(f"savings: actual=${sv['actual_cost_usd']:.6f} "
              f"all_tier3=${sv['all_tier3_cost_usd']:.6f} "
              f"savings_pct={sv['savings_pct'] * 100:.1f}%  "
              f"cache hit_rate={cache['hit_rate'] * 100:.1f}%")
    except Exception as e:  # /stats is best-effort decoration of the run
        print(f"(could not fetch {stats_url}: {e})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/chat")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=100)
    a = ap.parse_args()
    asyncio.run(main(a.url, a.n, a.concurrency))
