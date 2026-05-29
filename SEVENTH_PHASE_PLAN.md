# Phase 7 — High-Scale Engineering — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to
> implement this plan task-by-task. Use superpowers-extended-cc:test-driven-development
> for every task (RED → GREEN → COMMIT).

**Goal:** Turn the working chatbot into a **cost-efficient, resilient, provably-scalable** service. Per
`MAJOR_PHASES.md` §4 (Phase 7) + requirement bucket **C**, this phase adds the four "Section C" pillars on
top of the Phase 1–6 pipeline — **without changing any tier's answer**:

1. **Redis semantic caching** — embed the query, find a near-duplicate of a past query in a cache, and
   serve its stored response on a hit (skip all LLM calls → ~0 tokens, sub-ms latency).
2. **Health checks + failover** — each tier can have **multiple mock workers**; a down worker is detected
   and the request **fails over** to a healthy one, so a single dead instance never breaks `/chat`.
3. **Observability rollup + cost-savings calc** — the per-request `UsageLog` (Phases 3–6) is rolled up
   **by tier** and turned into the headline number: **what Tier-1/2 routing saves vs. running every
   request at Tier-3** (the graded cost-efficiency story).
4. **Load test** — a script driving **100+ concurrent users** against the deterministic mock backend,
   reporting throughput / latency percentiles / error rate.

Everything stays **feature-flagged and offline-testable**: the cache, the failover wrapper, and the
observability rollup are all backed by injected protocols so the offline suite uses `FakeEmbedder` +
in-memory doubles + `FakeLLM` (no Redis, no sockets). The real path uses Redis + the live mock workers,
exercised only under `@pytest.mark.integration` (skips if down). **No tier prompt or answer changes** —
Phase 7 is pure infrastructure around the existing `Orchestrator`.

**Architecture (what Phase 7 adds around the existing `/chat` → `Orchestrator.run` path):**

```
POST /chat (query)
   │
   ▼  ┌──────────────── SEMANTIC CACHE (NEW) ────────────────┐
   │  │ vec = embedder.embed_query(query)                     │
   │  │ hit = cache.get(query)   # nearest past query, cosine │
   │  │   cosine >= cache_similarity_threshold → HIT          │
   │  └───────────────────────────────────────────────────────┘
   │        │ HIT                                  │ MISS
   │        ▼                                       ▼
   │   cached payload                        Orchestrator.run(query)
   │   {answer, tier, usage:0, cached:true}     │  router → tierN executor → guardrail (Phases 2–6)
   │        │                                    │  build_llm(s, tier) now returns a FAILOVER pool:
   │        │                                    │     FailoverLLM([worker_a, worker_b, …])  (NEW)
   │        │                                    │       try worker_a → down? → worker_b → … (health-aware)
   │        │                                    ▼
   │        │                              ExecutionResult{tier, answer, usage, verified, gap}
   │        │                                    │  cacheable? (served, not abstain/escalation) → cache.put
   │        ▼                                    ▼
   └────────────── usage_log.record(..., cached) ───────────────┐
                                                                 ▼
   GET /usage  → running totals + cache hit-rate                 │
   GET /stats  → per-tier breakdown + COST-SAVINGS vs all-Tier-3 │  (NEW, from UsageLog rollup)
                                                                 ▼
   scripts/load_test.py → 100+ concurrent users → p50/p95/throughput/errors  (NEW)
```

**Tech Stack:** builds on Phase 1 (`Embedder` — `embed_query`, `FakeEmbedder`/`OllamaEmbedder`), Phase 3
(`OpenAICompatLLM`, `build_llm`, `LLMClient`, `TokenUsage`, `UsageLog`/`estimate_cost`, the mock servers +
`/healthz`), and Phases 4–6 (`Orchestrator.run` → `ExecutionResult{tier, answer, usage, verified, gap}`).
**One new runtime dep:** `redis>=5.0` (only used on the real path; offline tests inject an in-memory
backend / a tiny dict-backed `FakeRedis` double). Offline tests use `FakeEmbedder` + `InMemoryCacheBackend`
+ `FakeLLM` + `TestClient`; `@integration` tests cover the live Redis cache, live-worker failover, and the
load smoke test (each skips if its service is down).

**Key design decisions (locked for this phase):**

- **Cache = injected `CacheBackend` protocol, like `Embedder`/`QdrantStore`.** `SemanticCache(embedder,
  backend, threshold)` owns the embedding + cosine + threshold logic; the backend only stores/scans
  `(vector, payload)` entries. Two backends: `InMemoryCacheBackend` (offline default, bounded ring buffer)
  and `RedisCacheBackend` (real path). This mirrors how `FakeEmbedder` + in-memory Qdrant back the offline
  suite while `OllamaEmbedder` + real Qdrant run in production. **No new vector DB** — the cache reuses the
  same `Embedder` the retriever already uses.
- **Brute-force similarity over a bounded cache (deliberate simplification).** The cache is capped at
  `cache_max_entries` with a TTL, so a linear cosine scan per lookup is cheap at take-home scale. The
  `CacheBackend` interface is intentionally shaped so a future RediSearch / Qdrant vector-index backend
  drops in without touching `SemanticCache`. We `log()` the chosen backend + cap so the trade-off is
  explicit, never silent.
- **Only cache *served* answers.** Abstain (`gap.kind=="abstain"`) and escalation
  (`pending_review`/`gap.kind=="unverified"`) results are **never cached** — caching a "Pending Human
  Review" would suppress the Phase-5 knowledge-gap alert and freeze a gap we want humans to close. A cache
  hit returns the stored answer with **`usage = 0` and `cached = true`** (the whole point: a hit costs no
  tokens), and is still recorded in `UsageLog` (as a cached request) so hit-rate is observable.
- **Failover = `FailoverLLM` wrapping an ordered worker list; fail over on exception.** `build_llm(s,
  tier)` returns a `FailoverLLM([OpenAICompatLLM(url) for url in workers])` when a tier has >1 worker, else
  the single client unchanged (additive, backward-compatible). `complete()` tries workers in health order;
  on any `httpx`/transport error it marks that worker down and tries the next; raises only if **all** are
  down. A lightweight `WorkerHealth` deprioritizes recently-failed workers and an optional `/healthz` probe
  reorders them — but the **core guarantee (try next on failure)** needs no probe, so it's fully testable
  offline with fake workers (one raising, one healthy).
- **Cost-savings is a pure function of the existing `UsageLog`.** No new accounting: `savings_vs_all_tier3`
  re-costs every recorded request's `(prompt_tokens, completion_tokens)` at the **Tier-3 multiplier** and
  compares to the actual cost. This is exactly the graded "Tier-1/2 routing vs all-Tier-3" number, and it
  only became meaningful now that Phases 4–6 produce *real* per-tier token counts.
- **Load test targets the deterministic mock backend.** `LLM_TYPE=mock` makes every answer deterministic
  and offline, so a 100+ concurrent run measures the *gateway's* throughput/latency, not a flaky upstream.
  The script is a standalone `scripts/load_test.py`; a small `@integration` smoke test asserts the gateway
  survives a concurrent burst with zero errors (skips if mocks down). We **never** claim a load result we
  didn't run — the README records the actual numbers from a real run.

**New/changed files at a glance:**

| File | Change |
|---|---|
| `src/tiered_rag/config.py` | + cache settings (`cache_enabled`, `redis_url`, `cache_similarity_threshold`, `cache_ttl_seconds`, `cache_max_entries`, `cache_key_prefix`) + per-tier worker lists (`mock_tier{1,2,3}_workers`) + `health_check_timeout` |
| `src/tiered_rag/cache.py` | **new** — `CacheBackend` protocol, `InMemoryCacheBackend`, `RedisCacheBackend`, `SemanticCache`, `_cosine`, `cacheable(res)` |
| `src/tiered_rag/llm/failover.py` | **new** — `FailoverLLM`, `WorkerHealth` |
| `src/tiered_rag/llm/client.py` | `build_llm` parses worker lists → returns `FailoverLLM` when a tier has >1 worker |
| `src/tiered_rag/observability.py` | `UsageRecord` gains `cached: bool`; `UsageLog` gains `by_tier()`, `savings_vs_all_tier3(settings)`, `cache_stats()` |
| `src/tiered_rag/api.py` | `get_cache` dependency (app-state `SemanticCache`); `/chat` does cache get/put + `cached` field; new `GET /stats`; `ChatResponse` gains `cached` |
| `requirements.txt` | + `redis>=5.0` |
| `docker-compose.yml` | + `redis` service; + a Tier-1 replica (`mock_tier1b`) wired via `MOCK_TIER1_WORKERS` to demo failover |
| `scripts/load_test.py` | **new** — async 100+ concurrent-user driver (p50/p95/throughput/errors) |
| `tests/test_config.py` | + Phase-7 defaults |
| `tests/test_cache.py` | **new** — cosine, hit/miss/threshold, TTL/cap, `cacheable`, `FakeRedis`-backed `RedisCacheBackend` |
| `tests/test_failover.py` | **new** — fail over on a down worker, all-down raises, health ordering |
| `tests/test_observability.py` | + `by_tier`, `savings_vs_all_tier3`, `cache_stats` |
| `tests/test_api.py` | + cache hit short-circuits the orchestrator, `cached` field, `/stats` |
| `tests/test_integration_cache.py` | **new** `@integration` — live Redis round-trip (skips if down) |
| `tests/test_integration_failover.py` | **new** `@integration` — kill one live worker, `/chat` still 200 (skips if down) |
| `tests/test_integration_load.py` | **new** `@integration` — concurrent burst, zero errors (skips if mocks down) |
| `README.md` | Phase-7 section |

---

## Task 0: Phase-7 config + `redis` dependency

**Files:**
- Modify: `src/tiered_rag/config.py` (cache + worker + health settings)
- Modify: `requirements.txt` (+ `redis>=5.0`)
- Test: `tests/test_config.py` (extend)

**Design:** all new behaviour is gated by `Settings`, never hardcoded. Worker lists are comma-separated
strings (empty → fall back to the single `mock_tier{N}_base_url`, so Phase-3 config still works unchanged).

**Step 1: Write the failing tests** — append to `tests/test_config.py`:
```python
def test_phase7_cache_defaults():
    s = Settings()
    assert s.cache_enabled is True
    assert 0.0 < s.cache_similarity_threshold <= 1.0
    assert s.cache_ttl_seconds > 0 and s.cache_max_entries > 0
    assert s.redis_url.startswith("redis://")
    assert s.cache_key_prefix  # non-empty


def test_phase7_worker_and_health_defaults():
    s = Settings()
    # empty by default -> build_llm falls back to the single per-tier base url (Phase-3 behaviour)
    assert s.mock_tier1_workers == "" and s.mock_tier2_workers == "" and s.mock_tier3_workers == ""
    assert s.health_check_timeout > 0


def test_phase7_worker_list_parses(monkeypatch):
    monkeypatch.setenv("MOCK_TIER1_WORKERS", "http://a:9101/v1, http://b:9111/v1")
    assert Settings().tier_workers(1) == ["http://a:9101/v1", "http://b:9111/v1"]


def test_phase7_worker_list_falls_back_to_single_url():
    s = Settings()
    assert s.tier_workers(1) == [s.mock_llm_base_url]      # no workers configured -> single tier-1 url
    assert s.tier_workers(2) == [s.mock_tier2_base_url]
```

**Step 2: Run → expect FAIL** (`AttributeError`/missing settings + `tier_workers`)
Run: `pytest tests/test_config.py -v`

**Step 3: Implement** — add to `Settings` (after the Phase-6 block) and a helper:
```python
    # --- High-scale engineering (Phase 7) ---
    cache_enabled: bool = True
    redis_url: str = "redis://localhost:6379/0"
    cache_similarity_threshold: float = 0.95   # near-duplicate queries only (high bar -> safe hits)
    cache_ttl_seconds: int = 3600
    cache_max_entries: int = 512               # bound the brute-force scan
    cache_key_prefix: str = "tiered_rag:cache"

    # multiple workers per tier (comma-separated); empty -> the single mock_tier{N}_base_url
    mock_tier1_workers: str = ""
    mock_tier2_workers: str = ""
    mock_tier3_workers: str = ""
    health_check_timeout: float = 2.0

    def tier_workers(self, tier: int) -> list[str]:
        raw = {1: self.mock_tier1_workers, 2: self.mock_tier2_workers,
               3: self.mock_tier3_workers}.get(tier, "")
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        if urls:
            return urls
        single = {1: self.mock_llm_base_url, 2: self.mock_tier2_base_url,
                  3: self.mock_tier3_base_url}.get(tier, self.mock_llm_base_url)
        return [single]
```
Append `redis>=5.0` to `requirements.txt`.

**Step 4: Run → expect PASS**  ·  **Step 5: Commit**
```bash
git add src/tiered_rag/config.py requirements.txt tests/test_config.py
git commit -m "feat(p7): cache/worker/health settings + tier_workers() helper + redis dep"
```

---

## Task 1: Semantic cache core (`SemanticCache` + `InMemoryCacheBackend`)

**Files:**
- Create: `src/tiered_rag/cache.py` (`CacheBackend`, `InMemoryCacheBackend`, `SemanticCache`, `_cosine`,
  `cacheable`)
- Test: `tests/test_cache.py` (**new**)

**Design:** `SemanticCache(embedder, backend, threshold)`.
- `put(query, payload)` → `backend.add(embedder.embed_query(query), {**payload, "query": query})`.
- `get(query)` → embed, ask `backend.scan()` for `(vector, payload)` entries, return the payload of the
  **best cosine match ≥ threshold**, else `None`.
- `_cosine(a, b)` = dot / (‖a‖·‖b‖) (works whether or not vectors are pre-normalized).
- `cacheable(res: ExecutionResult)` → `True` only when the answer was *served*: `not res.abstained` and
  `res.gap is None` (so abstains + escalations are never cached).
- `InMemoryCacheBackend(max_entries)` keeps a bounded list (drop oldest past the cap).

**Step 1: Write the failing tests** (`tests/test_cache.py`)
```python
from tiered_rag.cache import InMemoryCacheBackend, SemanticCache, cacheable
from tiered_rag.embeddings import FakeEmbedder
from tiered_rag.orchestrator import ExecutionResult
from tiered_rag.alerting import GapAlert


def _cache(threshold=0.95, max_entries=512):
    return SemanticCache(FakeEmbedder(dim=64), InMemoryCacheBackend(max_entries), threshold)


def test_exact_repeat_query_is_a_hit():
    c = _cache()
    assert c.get("how do I reset my password") is None          # cold miss
    c.put("how do I reset my password", {"answer": "Open Settings > Security > Reset.", "tier": 1})
    hit = c.get("how do I reset my password")
    assert hit is not None and hit["answer"].startswith("Open Settings")


def test_unrelated_query_is_a_miss():
    c = _cache()
    c.put("how do I reset my password", {"answer": "x", "tier": 1})
    # FakeEmbedder: different strings -> different vectors -> cosine below the 0.95 bar
    assert c.get("what is the capital of France") is None


def test_threshold_controls_hit_strictness():
    loose = _cache(threshold=-1.0)                              # everything clears the bar
    loose.put("anything", {"answer": "a", "tier": 1})
    assert loose.get("totally different") is not None


def test_cache_respects_max_entries_cap():
    c = _cache(max_entries=2)
    for i in range(5):
        c.put(f"q{i}", {"answer": f"a{i}", "tier": 1})
    assert len(c.backend.scan()) == 2                           # bounded ring buffer


def test_cacheable_excludes_abstain_and_escalation():
    assert cacheable(ExecutionResult(tier=1, answer="ok"))               # served answer
    assert not cacheable(ExecutionResult(tier=1, answer="idk", abstained=True))
    esc = ExecutionResult(tier=2, answer="pending", gap=GapAlert(kind="unverified", query="q", answer="a"))
    assert not cacheable(esc)
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.cache`)
Run: `pytest tests/test_cache.py -v`

**Step 3: Implement** (`src/tiered_rag/cache.py`)
```python
from __future__ import annotations

import math
from typing import Protocol

from .embeddings import Embedder
from .orchestrator import ExecutionResult


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def cacheable(res: ExecutionResult) -> bool:
    """Cache only *served* answers — never abstains or human-review escalations."""
    return not res.abstained and res.gap is None


class CacheBackend(Protocol):
    def add(self, vector: list[float], payload: dict) -> None: ...
    def scan(self) -> list[tuple[list[float], dict]]: ...


class InMemoryCacheBackend:
    def __init__(self, max_entries: int = 512):
        self.max_entries = max_entries
        self._entries: list[tuple[list[float], dict]] = []

    def add(self, vector: list[float], payload: dict) -> None:
        self._entries.append((vector, payload))
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

    def scan(self) -> list[tuple[list[float], dict]]:
        return list(self._entries)


class SemanticCache:
    def __init__(self, embedder: Embedder, backend: CacheBackend, threshold: float):
        self.embedder, self.backend, self.threshold = embedder, backend, threshold

    def get(self, query: str) -> dict | None:
        vec = self.embedder.embed_query(query)
        best_score, best_payload = self.threshold, None
        for stored_vec, payload in self.backend.scan():
            score = _cosine(vec, stored_vec)
            if score >= best_score:
                best_score, best_payload = score, payload
        return best_payload

    def put(self, query: str, payload: dict) -> None:
        self.backend.add(self.embedder.embed_query(query), {**payload, "query": query})
```
*(`get` seeds `best_score` at `threshold`, so only matches ≥ threshold ever win; `>=` lets `threshold=-1.0`
in the test always hit.)*

**Step 4: Run → expect PASS**  ·  **Step 5: Commit**
```bash
git add src/tiered_rag/cache.py tests/test_cache.py
git commit -m "feat(p7): semantic cache core (cosine + threshold + bounded backend + cacheable guard)"
```

---

## Task 2: `RedisCacheBackend` (real path, dict-double-tested)

**Files:**
- Modify: `src/tiered_rag/cache.py` (add `RedisCacheBackend`)
- Test: `tests/test_cache.py` (extend, with a tiny `FakeRedis` double)

**Design:** a `RedisCacheBackend(client, prefix, ttl, max_entries)` that stores each entry as a Redis hash
`{prefix}:{n}` → `{"vector": json, "payload": json}` with `EXPIRE ttl`, and keeps an inserts counter so the
key id rolls over `mod max_entries` (bounded set). `scan()` reads the live keys and decodes them. The
backend only uses a handful of ops (`hset`, `expire`, `keys`, `hgetall`), so tests inject a dict-backed
`FakeRedis` double — **no `redis` server, no `fakeredis` dep**. The live server is covered by an
`@integration` test in Task 6.

**Step 1: Write the failing tests** — append to `tests/test_cache.py`:
```python
import json


class FakeRedis:
    """Minimal in-process double of the redis ops RedisCacheBackend uses."""
    def __init__(self):
        self.store: dict[str, dict] = {}

    def hset(self, key, mapping):
        self.store.setdefault(key, {}).update(mapping)

    def expire(self, key, ttl):  # TTL behaviour is not asserted offline
        return True

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def hgetall(self, key):
        return self.store.get(key, {})


def test_redis_backend_round_trips_an_entry():
    from tiered_rag.cache import RedisCacheBackend, SemanticCache
    from tiered_rag.embeddings import FakeEmbedder
    backend = RedisCacheBackend(FakeRedis(), prefix="t:cache", ttl=60, max_entries=4)
    c = SemanticCache(FakeEmbedder(dim=64), backend, threshold=0.95)
    c.put("reset my password", {"answer": "Open Settings > Security > Reset.", "tier": 1})
    assert c.get("reset my password")["answer"].startswith("Open Settings")


def test_redis_backend_bounds_entries_by_modulo():
    from tiered_rag.cache import RedisCacheBackend
    backend = RedisCacheBackend(FakeRedis(), prefix="t:cache", ttl=60, max_entries=2)
    for i in range(5):
        backend.add([float(i)] * 4, {"answer": f"a{i}"})
    assert len(backend.scan()) == 2                            # rolls over mod max_entries
```

**Step 2: Run → expect FAIL** (`ImportError: RedisCacheBackend`)
Run: `pytest tests/test_cache.py -v`

**Step 3: Implement** — add to `src/tiered_rag/cache.py`:
```python
import json


class RedisCacheBackend:
    def __init__(self, client, prefix: str, ttl: int, max_entries: int):
        self.client, self.prefix, self.ttl, self.max_entries = client, prefix, ttl, max_entries
        self._n = 0

    def add(self, vector: list[float], payload: dict) -> None:
        key = f"{self.prefix}:{self._n % self.max_entries}"
        self._n += 1
        self.client.hset(key, mapping={"vector": json.dumps(vector), "payload": json.dumps(payload)})
        self.client.expire(key, self.ttl)

    def scan(self) -> list[tuple[list[float], dict]]:
        out: list[tuple[list[float], dict]] = []
        for key in self.client.keys(f"{self.prefix}:*"):
            h = self.client.hgetall(key)
            vec_raw, pay_raw = h.get("vector"), h.get("payload")
            if vec_raw is None or pay_raw is None:
                continue
            out.append((json.loads(vec_raw), json.loads(pay_raw)))
        return out
```
*(Note: a real `redis.Redis(decode_responses=True)` returns `str` keys/values, matching the `FakeRedis`
double; `get_cache` in Task 4 constructs the client with `decode_responses=True`.)*

**Step 4: Run → expect PASS**  ·  **Step 5: Commit**
```bash
git add src/tiered_rag/cache.py tests/test_cache.py
git commit -m "feat(p7): RedisCacheBackend (hash-per-entry + TTL + modulo cap), dict-double tested"
```

---

## Task 3: `FailoverLLM` — health-aware worker pool

**Files:**
- Create: `src/tiered_rag/llm/failover.py` (`FailoverLLM`, `WorkerHealth`)
- Modify: `src/tiered_rag/llm/client.py` (`build_llm` returns `FailoverLLM` when a tier has >1 worker)
- Test: `tests/test_failover.py` (**new**)

**Design:** `FailoverLLM(workers: list[LLMClient])` implements the `LLMClient` protocol.
- `complete()` iterates workers in **health order** (healthy first, then by fewest recent failures); on a
  successful call it records success and returns the `LLMResponse`; on any exception it records a failure
  and tries the next worker; if all fail it re-raises the **last** exception.
- `WorkerHealth` tracks a failure counter per worker index; `order()` returns indices sorted by failure
  count (stable, so a fresh pool keeps declared order). This is enough to satisfy "detect a down instance,
  fail over to a healthy worker" — an explicit `/healthz` probe is an optional optimisation, not required
  for correctness, and is added on the live path only.
- `build_llm(s, tier)`: build one `OpenAICompatLLM` per URL from `s.tier_workers(tier)`; return the single
  client if there's one URL, else wrap them in `FailoverLLM` (the `openai` path is a single worker, so its
  behaviour is unchanged).

**Step 1: Write the failing tests** (`tests/test_failover.py`)
```python
import pytest

from tiered_rag.llm.client import FakeLLM
from tiered_rag.llm.failover import FailoverLLM
from tiered_rag.llm.usage import LLMResponse, TokenUsage


class DownLLM:
    """A worker that is always down (raises on every call)."""
    def complete(self, system, user, *, temperature=0.0):
        raise ConnectionError("worker down")


def test_failover_uses_next_worker_when_first_is_down():
    pool = FailoverLLM([DownLLM(), FakeLLM("healthy answer")])
    resp = pool.complete("sys", "user")
    assert isinstance(resp, LLMResponse) and resp.content == "healthy answer"


def test_failover_raises_when_all_workers_down():
    pool = FailoverLLM([DownLLM(), DownLLM()])
    with pytest.raises(ConnectionError):
        pool.complete("sys", "user")


def test_failover_deprioritizes_a_worker_that_failed():
    down = DownLLM()
    pool = FailoverLLM([down, FakeLLM("ok")])
    pool.complete("s", "u")                       # first call: worker 0 fails, falls over to worker 1
    # worker 0 now has a failure on record -> health order tries the healthy worker first next time
    assert pool.health.order()[0] == 1


def test_build_llm_wraps_multiple_workers(monkeypatch):
    from tiered_rag.config import Settings
    from tiered_rag.llm.client import build_llm
    monkeypatch.setenv("LLM_TYPE", "mock")
    monkeypatch.setenv("MOCK_TIER1_WORKERS", "http://a:9101/v1,http://b:9111/v1")
    llm = build_llm(Settings(), 1)
    assert isinstance(llm, FailoverLLM) and len(llm.workers) == 2
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.llm.failover`)
Run: `pytest tests/test_failover.py -v`

**Step 3: Implement** (`src/tiered_rag/llm/failover.py`)
```python
from __future__ import annotations

from .client import LLMClient
from .usage import LLMResponse


class WorkerHealth:
    def __init__(self, n: int):
        self.failures = [0] * n

    def order(self) -> list[int]:
        # fewest failures first; stable so a fresh pool keeps declared order
        return sorted(range(len(self.failures)), key=lambda i: self.failures[i])

    def record_success(self, i: int) -> None:
        self.failures[i] = 0

    def record_failure(self, i: int) -> None:
        self.failures[i] += 1


class FailoverLLM:
    """Ordered worker pool: try the healthiest worker, fail over to the next on any error."""

    def __init__(self, workers: list[LLMClient]):
        if not workers:
            raise ValueError("FailoverLLM needs at least one worker")
        self.workers = workers
        self.health = WorkerHealth(len(workers))

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> LLMResponse:
        last_err: Exception | None = None
        for i in self.health.order():
            try:
                resp = self.workers[i].complete(system, user, temperature=temperature)
                self.health.record_success(i)
                return resp
            except Exception as e:  # transport/connection error -> try the next worker
                self.health.record_failure(i)
                last_err = e
        raise last_err  # all workers down
```
Then update `build_llm` in `src/tiered_rag/llm/client.py`:
```python
def build_llm(settings, tier: int = 1) -> LLMClient:
    if settings.llm_type == "mock":
        from .failover import FailoverLLM
        urls = settings.tier_workers(tier)
        clients = [OpenAICompatLLM(u, "mock-key", settings.openai_model) for u in urls]
        return clients[0] if len(clients) == 1 else FailoverLLM(clients)
    return OpenAICompatLLM(settings.openai_base_url, settings.openai_api_key, settings.openai_model)
```

**Step 4: Run → expect PASS** (also re-run `tests/test_llm_client.py` to confirm the single-worker path is
unchanged)
```bash
pytest tests/test_failover.py tests/test_llm_client.py -v
```

**Step 5: Commit**
```bash
git add src/tiered_rag/llm/failover.py src/tiered_rag/llm/client.py tests/test_failover.py
git commit -m "feat(p7): FailoverLLM worker pool (health-ordered, fail over on error) + build_llm wiring"
```

---

## Task 4: Wire the cache into `/chat` (get/put + `cached` field)

**Files:**
- Modify: `src/tiered_rag/api.py` (app-state `SemanticCache`; `get_cache` dep; `/chat` cache get/put;
  `ChatResponse.cached`)
- Modify: `src/tiered_rag/observability.py` (`UsageRecord.cached: bool`; `UsageLog.record(..., cached=False)`)
- Test: `tests/test_api.py` (extend: cache hit short-circuits the orchestrator + `cached` field)

**Design:** the cache lives on `app.state` like `UsageLog`/`Alerter`. `/chat`:
1. If `cache_enabled` and `cache.get(query)` returns a payload → build the response from it, record usage
   with **`cached=True`** and zero cost (a hit costs no tokens), and return `cached=True` **without calling
   the orchestrator**.
2. Else run `orchestrator.run(query)`; if `cacheable(res)`, `cache.put(query, payload)`; return as today
   with `cached=False`.

`get_cache` builds `SemanticCache(OllamaEmbedder(...), backend, threshold)` where the backend is
`RedisCacheBackend(redis.Redis.from_url(redis_url, decode_responses=True), …)` when `cache_enabled`, and is
overridable in tests with an `InMemoryCacheBackend` + `FakeEmbedder`. The orchestrator is **not aware** of
the cache — it stays a pure pipeline (single-responsibility, mirrors how the Phase-5 alert I/O lives in the
API, not the orchestrator).

**Step 1: Write / change the failing tests** — add to `tests/test_api.py`. (The suite already overrides
`get_orchestrator` with a `FakeLLM`-backed one; add a parallel `get_cache` override using an in-memory
cache, plus a spy orchestrator to prove a hit skips it.)
```python
def test_chat_caches_and_serves_a_repeat_query(client_with_inmemory_cache):
    client, spy = client_with_inmemory_cache         # spy counts orchestrator.run calls
    first = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert first["cached"] is False
    second = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert second["cached"] is True
    assert second["answer"] == first["answer"]
    assert second["usage"]["total_tokens"] == 0      # a cache hit costs no tokens
    assert spy.calls == 1                             # orchestrator ran only for the cold miss


def test_chat_does_not_cache_escalations(client_with_inmemory_cache):
    # an escalated (pending_review) answer must NOT be cached -> the gap keeps alerting
    ...
```
*(Define the `client_with_inmemory_cache` fixture in `tests/_helpers.py` or `conftest.py`: it builds the
app, overrides `get_orchestrator` with a counting spy wrapping `build_orchestrator(...)`, and overrides
`get_cache` with `SemanticCache(FakeEmbedder(64), InMemoryCacheBackend(64), threshold=0.95)`.)*

**Step 2: Run → expect FAIL** (`cached` not in response; cache not wired)
Run: `pytest tests/test_api.py -v`

**Step 3: Implement**
- `observability.py`: add `cached: bool = False` to `UsageRecord` (last field, default keeps Phase-3/6
  callers green); add a `cached: bool = False` kwarg to `UsageLog.record`.
- `api.py`: add `cached: bool = False` to `ChatResponse`; add `get_cache` dep + `app.state.cache`; in
  `/chat`, do the get/put around `orchestrator.run` exactly as the Design describes; on a hit, build
  `Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0, cost_usd=0.0)`.

**Step 4: Run → expect PASS** (cache tests + every untouched Phase-2–6 API test)
```bash
pytest tests/test_api.py tests/test_cache.py -v
```

**Step 5: Commit**
```bash
git add src/tiered_rag/api.py src/tiered_rag/observability.py tests/test_api.py tests/_helpers.py tests/conftest.py
git commit -m "feat(p7): /chat semantic-cache get/put (hit -> 0 tokens, cached flag) + UsageRecord.cached"
```

---

## Task 5: Observability rollup — per-tier breakdown + cost-savings + `/stats`

**Files:**
- Modify: `src/tiered_rag/observability.py` (`by_tier`, `savings_vs_all_tier3`, `cache_stats`)
- Modify: `src/tiered_rag/api.py` (new `GET /stats`; `/usage` adds cache hit-rate)
- Test: `tests/test_observability.py` (extend) + `tests/test_api.py` (extend: `/stats`)

**Design:** all three are pure reductions over `UsageLog.records` (no new state):
- `by_tier()` → `{tier: {requests, prompt_tokens, completion_tokens, total_tokens, cost_usd, avg_latency_ms}}`.
- `savings_vs_all_tier3(settings)` → re-cost each record's tokens at tier 3 and compare:
  `{actual_cost_usd, all_tier3_cost_usd, savings_usd, savings_pct}` (this is the graded number).
- `cache_stats()` → `{requests, cache_hits, cache_misses, hit_rate}` from the `cached` flag.

**Step 1: Write the failing tests** — append to `tests/test_observability.py`:
```python
def test_savings_vs_all_tier3_is_positive_when_routing_cheaply():
    from tiered_rag.config import Settings
    from tiered_rag.llm.usage import TokenUsage
    from tiered_rag.observability import UsageLog
    s, log = Settings(), UsageLog()
    log.record(tier=1, model="mock", usage=TokenUsage(100, 50), latency_ms=5, settings=s)
    log.record(tier=2, model="mock", usage=TokenUsage(100, 50), latency_ms=5, settings=s)
    sv = log.savings_vs_all_tier3(s)
    # tier-1/2 multipliers (1x, 3x) are cheaper than charging both at tier-3 (10x)
    assert sv["all_tier3_cost_usd"] > sv["actual_cost_usd"] > 0
    assert 0.0 < sv["savings_pct"] <= 1.0


def test_by_tier_groups_records():
    from tiered_rag.config import Settings
    from tiered_rag.llm.usage import TokenUsage
    from tiered_rag.observability import UsageLog
    s, log = Settings(), UsageLog()
    log.record(tier=1, model="mock", usage=TokenUsage(10, 5), latency_ms=1, settings=s)
    log.record(tier=1, model="mock", usage=TokenUsage(10, 5), latency_ms=3, settings=s)
    bt = log.by_tier()
    assert bt[1]["requests"] == 2 and bt[1]["total_tokens"] == 30


def test_cache_stats_counts_hits():
    from tiered_rag.config import Settings
    from tiered_rag.llm.usage import TokenUsage
    from tiered_rag.observability import UsageLog
    s, log = Settings(), UsageLog()
    log.record(tier=1, model="mock", usage=TokenUsage(10, 5), latency_ms=1, settings=s, cached=False)
    log.record(tier=1, model="mock", usage=TokenUsage(0, 0), latency_ms=0, settings=s, cached=True)
    cs = log.cache_stats()
    assert cs["cache_hits"] == 1 and cs["cache_misses"] == 1 and cs["hit_rate"] == 0.5
```
Add to `tests/test_api.py`: a `/stats` test asserting the JSON has `by_tier`, `savings`, and `cache` keys.

**Step 2: Run → expect FAIL** (no `savings_vs_all_tier3`/`by_tier`/`cache_stats`; no `/stats` route)
Run: `pytest tests/test_observability.py tests/test_api.py -v`

**Step 3: Implement**
- `observability.py`:
```python
    def by_tier(self) -> dict:
        out: dict[int, dict] = {}
        for r in self.records:
            t = out.setdefault(r.tier, {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                        "total_tokens": 0, "cost_usd": 0.0, "_lat": 0.0})
            t["requests"] += 1
            t["prompt_tokens"] += r.prompt_tokens
            t["completion_tokens"] += r.completion_tokens
            t["total_tokens"] += r.total_tokens
            t["cost_usd"] = round(t["cost_usd"] + r.cost_usd, 8)
            t["_lat"] += r.latency_ms
        for t in out.values():
            t["avg_latency_ms"] = round(t.pop("_lat") / t["requests"], 2)
        return out

    def savings_vs_all_tier3(self, settings: Settings) -> dict:
        actual = sum(r.cost_usd for r in self.records)
        hypothetical = sum(
            estimate_cost(3, TokenUsage(r.prompt_tokens, r.completion_tokens), settings)
            for r in self.records
        )
        savings = hypothetical - actual
        pct = (savings / hypothetical) if hypothetical else 0.0
        return {"actual_cost_usd": round(actual, 8), "all_tier3_cost_usd": round(hypothetical, 8),
                "savings_usd": round(savings, 8), "savings_pct": round(pct, 4)}

    def cache_stats(self) -> dict:
        hits = sum(1 for r in self.records if r.cached)
        total = len(self.records)
        return {"requests": total, "cache_hits": hits, "cache_misses": total - hits,
                "hit_rate": round(hits / total, 4) if total else 0.0}
```
- `api.py`: add
```python
    @app.get("/stats")
    def stats(usage_log: UsageLog = Depends(get_usage_log),
              settings: Settings = Depends(get_settings_dep)):
        return {"by_tier": usage_log.by_tier(),
                "savings": usage_log.savings_vs_all_tier3(settings),
                "cache": usage_log.cache_stats()}
```
and fold `usage_log.cache_stats()` into the `/usage` response.

**Step 4: Run → expect PASS**  ·  **Step 5: Commit**
```bash
git add src/tiered_rag/observability.py src/tiered_rag/api.py \
        tests/test_observability.py tests/test_api.py
git commit -m "feat(p7): observability rollup — by_tier + savings_vs_all_tier3 + cache_stats + /stats"
```

---

## Task 6: Load test + Redis/replica compose + integration tests + README

**Files:**
- Create: `scripts/load_test.py` (async 100+ concurrent-user driver)
- Modify: `docker-compose.yml` (+ `redis`; + Tier-1 replica `mock_tier1b`; gateway gets
  `MOCK_TIER1_WORKERS` + `REDIS_URL`)
- Test: `tests/test_integration_cache.py`, `tests/test_integration_failover.py`,
  `tests/test_integration_load.py` (**new**, all `@integration`, skip if their service is down)
- Modify: `README.md` (Phase-7 section)

**Design:**
- **`scripts/load_test.py`** — `asyncio` + `httpx.AsyncClient`, args `--n` (total requests, default 200),
  `--concurrency` (default 100), `--url` (default `http://localhost:8000/chat`), cycling the 6-category
  query taxonomy. Prints requests/sec, p50/p95/p99 latency, error count, and the final `/stats`
  (cost-savings + cache hit-rate). This is the artifact that produces the README's headline numbers.
- **`docker-compose.yml`** — add a `redis:7` service (`6379:6379`); add `mock_tier1b` (tier 1 on `9111`);
  add a `gateway` service running `uvicorn tiered_rag.api:app` with `LLM_TYPE=mock`,
  `MOCK_TIER1_WORKERS=http://mock_tier1:9101/v1,http://mock_tier1b:9111/v1`, and
  `REDIS_URL=redis://redis:6379/0`, so `docker compose up` brings up a failover-capable, cache-backed
  gateway.
- **Integration tests** (mirror `tests/test_integration_pipeline.py`'s `_up()`-skip pattern):
  - `test_integration_cache.py` — round-trip a payload through a real `redis.Redis.from_url(s.redis_url)`;
    skip if Redis is down.
  - `test_integration_failover.py` — point a `FailoverLLM` at `[<down port>, <live tier-1 mock>]`; assert
    `complete()` still returns the live worker's answer; skip if the live mock is down.
  - `test_integration_load.py` — fire a modest concurrent burst (e.g. 50 requests, concurrency 20) at the
    gateway via the load-test helper; assert **zero errors** and every response is 200; skip if mocks down.
    *(Full 100+ is run from `scripts/load_test.py` against a real gateway — the test is a guardrail, not the
    headline benchmark.)*

**Step 1: Write the failing/integration tests**
Write the three `@integration` modules (each `pytestmark = pytest.mark.integration` + an `_up()` skip
guard, copying the pattern from `tests/test_integration_pipeline.py`). They fail to import only if a symbol
is missing; otherwise they **skip** cleanly when the service is down — so "RED" here is "collected + skips",
and the real RED→GREEN is the offline suite from Tasks 0–5.

**Step 2: Implement `scripts/load_test.py`** and the compose changes.

`scripts/load_test.py` sketch:
```python
import argparse, asyncio, time
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
    p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))]
    errors = sum(1 for c in codes if c != 200)
    print(f"n={n} concurrency={concurrency} elapsed={elapsed:.2f}s "
          f"rps={n / elapsed:.1f} p50={p(0.5):.1f}ms p95={p(0.95):.1f}ms "
          f"p99={p(0.99):.1f}ms errors={errors}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/chat")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=100)
    a = ap.parse_args()
    asyncio.run(main(a.url, a.n, a.concurrency))
```

**Step 3: Run the full offline suite, then the live stack**
```bash
pytest -m "not integration" -v          # all offline, green (Tasks 0–5)
docker compose up -d --build            # qdrant + redis + mock_tier1/1b/2/3 + gateway
python -m tiered_rag.ingest             # KB into Qdrant (for the FAQ path)
pytest -m integration -v                # cache + failover + load smoke + Phase 1–6; skips what's down
python scripts/load_test.py --n 300 --concurrency 100   # the headline 100+ concurrent run
curl -s localhost:8000/stats            # capture savings_pct + cache hit_rate for the README
```

**Step 4: Write the README Phase-7 section** — cover: the semantic cache (embed → cosine ≥ threshold → hit
serves a stored answer at **0 tokens**; only served answers cached, never abstain/escalation; bounded
Redis backend + the brute-force-scan trade-off), health-checks + failover (`FailoverLLM` worker pool, tries
the healthiest worker, fails over on error; `MOCK_TIER{N}_WORKERS` config; the compose replica), the
observability rollup (`/stats`: `by_tier`, **cost-savings vs all-Tier-3**, cache hit-rate), and the **load
test** with the *actual measured* rps / p50 / p95 / error-count from the run above. Record the headline
**savings_pct** and **cache hit_rate**. Do **not** invent numbers — paste what the run printed.

**Step 5: Commit**
```bash
git add scripts/load_test.py docker-compose.yml \
        tests/test_integration_cache.py tests/test_integration_failover.py \
        tests/test_integration_load.py README.md
git commit -m "feat(p7): load-test script + redis/replica compose + cache/failover/load integration tests + README"
```

---

## Phase 7 Definition of Done

- [ ] `pytest -m "not integration"` → all green, fully offline (FakeEmbedder + in-memory cache + FakeLLM +
      `FakeRedis` double + `TestClient`; no Redis, no sockets).
- [ ] **Semantic cache**: a repeated/near-duplicate query (cosine ≥ `cache_similarity_threshold`) is a
      **hit** served from the cache at **0 tokens** with `cached=true`, **skipping the orchestrator**;
      abstain + escalation answers are **never cached**; the cache is bounded (`cache_max_entries`) with a
      TTL; backend is swappable (`InMemoryCacheBackend` offline, `RedisCacheBackend` live).
- [ ] **Health checks + failover**: `FailoverLLM` tries the healthiest worker and **fails over to the next
      on error**, raising only when **all** workers are down; `build_llm` wraps multiple workers per tier
      and is **backward-compatible** (single worker → unchanged Phase-3 behaviour). Proven offline with a
      down-worker double; the `@integration` test kills a live worker and `/chat` still returns 200.
- [ ] **Observability rollup**: `GET /stats` reports per-tier breakdown, the **cost-savings vs all-Tier-3**
      headline (`savings_usd` + `savings_pct`), and the **cache hit-rate** — all pure reductions over the
      existing `UsageLog` (no answer changes).
- [ ] **Load test**: `scripts/load_test.py` drives **100+ concurrent users** against the mock backend and
      reports rps / p50 / p95 / p99 / errors; the `@integration` smoke test asserts a concurrent burst
      returns **zero errors**. README records the *actual* numbers from a real run (not invented).
- [ ] `docker compose up` brings up Qdrant + Redis + the mock tier workers (incl. a Tier-1 replica) + a
      failover-capable, cache-backed gateway. README Phase-7 section written. All work committed.

**Next:** write `EIGHTH_PHASE_PLAN.md` (Telegram + Final Packaging) — a **Telegram bot front-end** over the
`/chat` gateway, the final `Dockerfile` + `docker-compose` (Gateway + Redis + Mock LLM + Qdrant), and the
two submission documents: **`README.md`** (architecture + the Phase-7 load-test results) and
**`EVAL_REPORT.md`** (the **abstention rate** from the Phase-1 `eval_abstention` harness + the Phase-2/3
**routing accuracy** + the Phase-7 **token/cost-savings** analysis). Phases 1–7 have already produced every
input number EVAL_REPORT needs — Phase 8 assembles them into the graded deliverable and ships it.
