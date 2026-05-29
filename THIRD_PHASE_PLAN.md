# Phase 3 — Mock-vs-Real LLM Backend + Token Logging — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to
> implement this plan task-by-task. Use superpowers-extended-cc:test-driven-development
> for every task (RED → GREEN → COMMIT).

**Goal:** Make the `LLM_TYPE=mock` path **real** — stand up the brief-required **3 mock tier
servers on separate ports** (9101 / 9102 / 9103), each speaking the OpenAI-compatible
`/chat/completions` shape — and start **counting tokens from day one**: every LLM call surfaces
its `prompt`/`completion` token usage, the gateway logs a structured per-request record with a
**simulated per-tier cost**, and a `/usage` endpoint summarizes the running total. This lays the
observability backbone for the Phase-7 cost-savings story (Tier-1/2 routing vs all-Tier-3).

**Architecture (what Phase 3 adds):**

```
                        LLM_TYPE=mock                         LLM_TYPE=openai
                     ┌──────────────────┐                  ┌──────────────────┐
  Router (tier-1) ──►│ mock_tier1 :9101 │   build_llm(s,   │  real OpenAI     │
  T2 LLM  (P4) ─────►│ mock_tier2 :9102 │◄─ tier=N) picks  │  (one model      │
  T3 LLM  (P6) ─────►│ mock_tier3 :9103 │   the backend    │  behind all 3)   │
                     └──────────────────┘                  └──────────────────┘
                              │  every /chat/completions response carries `usage`
                              ▼
        OpenAICompatLLM.complete() → LLMResponse{content, usage:TokenUsage}
                              │
                              ▼
        gateway /chat → UsageLog.record(tier, model, usage, latency, cost) → structured log
                              │
                              ▼
                         GET /usage → {requests, total_cost_usd}
```

The mock servers are **deterministic and fully offline** (ideal for load tests in Phase 7).
The tier-1 mock doubles as the router backend: when it sees the router system prompt it returns
valid `TierSelection` JSON chosen by a keyword heuristic; otherwise it returns a canned tier-N
answer (forward-compatible with Phase-4 execution). RAG stays real and untouched.

**Tech Stack:** builds on Phase 1+2 (`fastapi`, `uvicorn`, `httpx`, `pydantic-settings`,
`pytest`). No new runtime deps — the mock servers reuse FastAPI; token estimation is a tiny
pure-Python helper; cost is arithmetic from config. Adds a `Dockerfile` so the mock services
build in `docker-compose`. Offline tests exercise the mock app via `fastapi.testclient.TestClient`
(no sockets); one `@integration` test hits the real running servers on their ports.

**Key design decisions (locked for this phase):**

- **One image, three configured instances.** A single `create_mock_app(tier)` factory backs all
  three servers; each instance is pinned to its tier at launch (`--tier/--port`, or
  `MOCK_TIER`/`MOCK_PORT` env). This is the literal "mock local endpoints on different ports"
  the brief asks for, with zero duplicated server code.
- **`complete()` now returns `LLMResponse{content, usage}`** (was `str`). Token usage is the
  whole point of this phase, so the client surfaces it instead of hiding it. This is a small,
  deliberate refactor of the Phase-2 interface: the **only** production caller is `Router`
  (updated to use `.content`), and only `tests/test_llm_client.py` needs touching
  (`.complete(...).content`). `Router.route()`'s public contract is unchanged — a new
  `route_detailed()` exposes usage for the gateway without breaking `route()`.
- **Usage comes from the response, with a fallback.** Real OpenAI and our mock both return a
  `usage` block; `OpenAICompatLLM` reads it and falls back to a deterministic estimate only if
  it's absent, so the number is always present.
- **Cost is simulated, not billed.** Per-1K input/output base rates × a per-tier multiplier
  (tier-1 = 1×, tier-2 = 3×, tier-3 = 10× by default) — cheap router, pricey deep reasoning.
  All from `Settings`; never hardcoded. The point is the *relative* cost so Phase 7 can compute
  routing savings.
- **Execution stays stubbed.** `/chat` still returns the Phase-2 stub answer; Phase 3 only adds
  the `usage` block and logging around the routing call. Real T1/T2 execution is Phase 4.

**Mock token/cost taxonomy (per request, what we log):**

| Field | Source | Example |
|---|---|---|
| `tier` | router decision (1/2/3) | `2` |
| `model` | `OPENAI_MODEL` (or `mock`) | `gpt-5.4-nano` |
| `prompt_tokens` / `completion_tokens` | response `usage` (or estimate) | `48 / 12` |
| `total_tokens` | sum | `60` |
| `cost_usd` | `estimate_cost(tier, usage, settings)` | `0.000.. ` |
| `latency_ms` | `perf_counter` around the route call | `21.4` |

---

## Task 0: Phase-3 config — per-tier mock URLs + simulated cost rates

**Files:**
- Modify: `src/tiered_rag/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py` (extend)

**Design:** keep the existing `mock_llm_base_url` (it *is* the tier-1 mock, used by Phase-2
`build_llm` + its test) and add tier-2/tier-3 URLs plus the simulated-cost knobs. Tier-1's cost
multiplier is the implicit `1.0` baseline.

**Step 1: Write the failing test** — append to `tests/test_config.py`:
```python
def test_phase3_mock_and_cost_defaults():
    s = Settings()
    # tier-1 mock is the existing mock_llm_base_url (:9101)
    assert s.mock_llm_base_url.endswith(":9101/v1")
    assert s.mock_tier2_base_url.endswith(":9102/v1")
    assert s.mock_tier3_base_url.endswith(":9103/v1")
    assert s.cost_input_per_1k > 0 and s.cost_output_per_1k > 0
    # deeper tiers are simulated as more expensive
    assert s.tier3_cost_multiplier > s.tier2_cost_multiplier > 1.0
```

**Step 2: Run → expect FAIL** (`AttributeError: 'Settings' object has no attribute 'mock_tier2_base_url'`)
Run: `pytest tests/test_config.py -v`

**Step 3: Implement** — add fields to `Settings` (after the Phase-2 `router_temperature`):
```python
    # --- Mock tier servers (Phase 3): separate ports per tier ---
    # tier-1 == mock_llm_base_url above (the router backend)
    mock_tier2_base_url: str = "http://localhost:9102/v1"
    mock_tier3_base_url: str = "http://localhost:9103/v1"

    # --- Simulated token cost (Phase 3): USD per 1K tokens ---
    cost_input_per_1k: float = 0.00015
    cost_output_per_1k: float = 0.00060
    tier2_cost_multiplier: float = 3.0   # tier-1 baseline is 1.0
    tier3_cost_multiplier: float = 10.0
```
Append to `.env.example`:
```
# --- Phase 3: mock tier servers (separate ports) ---
MOCK_TIER2_BASE_URL=http://localhost:9102/v1
MOCK_TIER3_BASE_URL=http://localhost:9103/v1
# --- Phase 3: simulated token cost (USD per 1K tokens) ---
COST_INPUT_PER_1K=0.00015
COST_OUTPUT_PER_1K=0.00060
TIER2_COST_MULTIPLIER=3.0
TIER3_COST_MULTIPLIER=10.0
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_config.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/config.py .env.example tests/test_config.py
git commit -m "feat(p3): per-tier mock URLs + simulated token-cost config"
```

---

## Task 1: Token-usage model + surface usage from the LLM client

**Files:**
- Create: `src/tiered_rag/llm/usage.py`
- Modify: `src/tiered_rag/llm/client.py` (`complete()` → `LLMResponse`)
- Modify: `src/tiered_rag/router.py` (`Router.route` uses `.content`)
- Test: `tests/test_llm_usage.py`
- Modify: `tests/test_llm_client.py` (assert on `.content` + usage)

**Design:** `TokenUsage{prompt_tokens, completion_tokens}` with a `total_tokens` property and an
`estimate(prompt, completion)` classmethod; `LLMResponse{content, usage}`. `estimate_tokens(text)`
is a deterministic ~4-chars/token heuristic (`0` for empty). `FakeLLM.complete` wraps its
responder output and *estimates* usage; `OpenAICompatLLM.complete` reads the response `usage`
block, estimating only as a fallback. `Router.route` switches to `complete(...).content`.

**Step 1: Write the failing test** (`tests/test_llm_usage.py`)
```python
from tiered_rag.llm.client import FakeLLM
from tiered_rag.llm.usage import LLMResponse, TokenUsage, estimate_tokens


def test_estimate_tokens_empty_is_zero_else_positive():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world, this is a sentence") >= 1


def test_token_usage_total_is_sum():
    u = TokenUsage(prompt_tokens=10, completion_tokens=5)
    assert u.total_tokens == 15


def test_fake_llm_returns_llmresponse_with_usage():
    resp = FakeLLM("hello there friend").complete("the system prompt", "the user query")
    assert isinstance(resp, LLMResponse)
    assert resp.content == "hello there friend"
    assert resp.usage.prompt_tokens > 0
    assert resp.usage.completion_tokens > 0
    assert resp.usage.total_tokens == resp.usage.prompt_tokens + resp.usage.completion_tokens
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.llm.usage`)
Run: `pytest tests/test_llm_usage.py -v`

**Step 3: Implement**

`src/tiered_rag/llm/usage.py`:
```python
from __future__ import annotations

from dataclasses import dataclass


def estimate_tokens(text: str) -> int:
    """Deterministic, offline token estimate (~4 chars/token); 0 for empty."""
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @classmethod
    def estimate(cls, prompt: str, completion: str) -> "TokenUsage":
        return cls(estimate_tokens(prompt), estimate_tokens(completion))


@dataclass
class LLMResponse:
    content: str
    usage: TokenUsage
```

Modify `src/tiered_rag/llm/client.py` — import the new types and change both clients to return
`LLMResponse` (update the Protocol's return annotation too):
```python
from .usage import LLMResponse, TokenUsage


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> "LLMResponse": ...


# FakeLLM.complete:
    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> LLMResponse:
        content = self.responder(system, user) if callable(self.responder) else self.responder
        return LLMResponse(content=content, usage=TokenUsage.estimate(system + user, content))


# OpenAICompatLLM.complete (after r.raise_for_status()):
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        u = data.get("usage") or {}
        if "prompt_tokens" in u and "completion_tokens" in u:
            usage = TokenUsage(u["prompt_tokens"], u["completion_tokens"])
        else:
            usage = TokenUsage.estimate(system + user, content)
        return LLMResponse(content=content, usage=usage)
```

Modify `src/tiered_rag/router.py` — `Router.route` reads `.content`:
```python
    def route(self, query: str) -> TierSelection:
        raw = self.llm.complete(ROUTER_SYSTEM, query, temperature=self.temperature).content
        try:
            return TierSelection(**_extract_json(raw))
        except Exception:
            return TierSelection(tier=1, reason="router parse fallback", plan=None)
```

Modify `tests/test_llm_client.py` — the two `complete()` assertions now read `.content`:
```python
def test_fake_llm_fixed_string():
    assert FakeLLM("hello").complete("sys", "user").content == "hello"


def test_fake_llm_callable_sees_prompts():
    assert FakeLLM(lambda system, user: f"{system}|{user}").complete("S", "U").content == "S|U"
```
*(The `build_llm` tests are unaffected — they never call `complete()`.)*

**Step 4: Run → expect PASS** (and confirm the Phase-2 router/eval suites stay green —
`Router.route` is internal-only changed)
Run: `pytest tests/test_llm_usage.py tests/test_llm_client.py tests/test_router.py tests/test_eval_routing.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/llm/usage.py src/tiered_rag/llm/client.py src/tiered_rag/router.py \
        tests/test_llm_usage.py tests/test_llm_client.py
git commit -m "feat(p3): surface token usage from LLM client (complete -> LLMResponse)"
```

---

## Task 2: Mock LLM server (deterministic, OpenAI-compatible, per-tier) + tiered `build_llm`

**Files:**
- Create: `src/tiered_rag/mock_llm.py`
- Modify: `src/tiered_rag/llm/client.py` (`build_llm(settings, tier=1)`)
- Test: `tests/test_mock_llm.py`
- Test: `tests/test_llm_client.py` (extend: tier→port selection)

**Design:** `create_mock_app(tier)` returns a FastAPI app with `GET /healthz` and
`POST /v1/chat/completions` (the `/v1` matches `OpenAICompatLLM`'s base-url + `/chat/completions`).
The reply is deterministic: if the **router system prompt** is present, return `TierSelection`
JSON whose tier is chosen by a keyword heuristic; otherwise return a canned `"[mock tier-N] …"`
answer. Every response includes a real `usage` block computed from `estimate_tokens`. `main()`
runs one instance via `uvicorn` (`--tier/--port`, env-overridable). `build_llm` gains a `tier`
arg so mock mode can pick the right port (default tier-1 keeps Phase-2 behavior).

**Step 1: Write the failing test** (`tests/test_mock_llm.py`)
```python
import json

from fastapi.testclient import TestClient

from tiered_rag.mock_llm import ROUTER_MARKER, create_mock_app
from tiered_rag.router import ROUTER_SYSTEM


def _post(client, system, user):
    return client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]},
    )


def test_router_marker_present_in_real_prompt():
    # guard: the mock detects routing requests by this substring
    assert ROUTER_MARKER in ROUTER_SYSTEM


def test_healthz_reports_tier():
    assert TestClient(create_mock_app(2)).get("/healthz").json() == {"status": "ok", "tier": 2}


def test_tier1_mock_returns_routing_json_with_usage():
    resp = _post(TestClient(create_mock_app(1)), ROUTER_SYSTEM, "what's the status of order #123?")
    body = resp.json()
    sel = json.loads(body["choices"][0]["message"]["content"])
    assert sel["tier"] == 2  # "order" -> tier 2 by the heuristic
    usage = body["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_tier2_mock_returns_canned_answer():
    resp = _post(TestClient(create_mock_app(2)), "you are a tier-2 assistant", "look up SKU-42")
    content = resp.json()["choices"][0]["message"]["content"]
    assert "mock tier-2" in content.lower()
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.mock_llm`)
Run: `pytest tests/test_mock_llm.py -v`

**Step 3: Implement**

`src/tiered_rag/mock_llm.py`:
```python
from __future__ import annotations

import argparse
import json
import os

from fastapi import FastAPI
from pydantic import BaseModel

from .llm.usage import estimate_tokens

# Substring of router.ROUTER_SYSTEM used to detect a routing request.
# A guard test asserts this stays in sync with the real prompt.
ROUTER_MARKER = "Tier-1 router"


def _classify(query: str) -> int:
    q = query.lower()
    if any(k in q for k in ["order", "price", "cost", "details", "sku",
                            "account tier", "stock", "rarity"]):
        return 2
    if any(k in q for k in ["double", "refund failed", "locked out", "escalate",
                            "never arrived", "bounced", "2fa", "walk me through"]):
        return 3
    return 1


def _reply(tier: int, system: str, user: str) -> str:
    if ROUTER_MARKER in system:
        chosen = _classify(user)
        return json.dumps({"tier": chosen, "reason": f"mock tier-{chosen} (deterministic)", "plan": None})
    return f"[mock tier-{tier}] deterministic answer for: {user[:80]}"


class _Msg(BaseModel):
    role: str
    content: str


class _ChatBody(BaseModel):
    model: str = "mock"
    temperature: float = 0.0
    messages: list[_Msg]


def create_mock_app(tier: int) -> FastAPI:
    app = FastAPI(title=f"mock-tier-{tier}")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "tier": tier}

    @app.post("/v1/chat/completions")
    def chat_completions(body: _ChatBody):
        system = next((m.content for m in body.messages if m.role == "system"), "")
        user = next((m.content for m in body.messages if m.role == "user"), "")
        content = _reply(tier, system, user)
        pt, ct = estimate_tokens(system + user), estimate_tokens(content)
        return {
            "id": f"mock-{tier}",
            "object": "chat.completion",
            "model": body.model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
        }

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Run one mock tier LLM server.")
    parser.add_argument("--tier", type=int, default=int(os.getenv("MOCK_TIER", "1")))
    parser.add_argument("--port", type=int, default=int(os.getenv("MOCK_PORT", "9101")))
    parser.add_argument("--host", default=os.getenv("MOCK_HOST", "0.0.0.0"))
    args = parser.parse_args()
    uvicorn.run(create_mock_app(args.tier), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
```

Modify `build_llm` in `src/tiered_rag/llm/client.py` to select a per-tier mock URL:
```python
def build_llm(settings, tier: int = 1) -> LLMClient:
    if settings.llm_type == "mock":
        url = {
            1: settings.mock_llm_base_url,
            2: settings.mock_tier2_base_url,
            3: settings.mock_tier3_base_url,
        }.get(tier, settings.mock_llm_base_url)
        return OpenAICompatLLM(url, "mock-key", settings.openai_model)
    return OpenAICompatLLM(settings.openai_base_url, settings.openai_api_key, settings.openai_model)
```

Extend `tests/test_llm_client.py`:
```python
def test_build_llm_mock_tier_selects_port():
    s = Settings(llm_type="mock")
    assert build_llm(s).base_url.endswith(":9101/v1")          # default tier-1
    assert build_llm(s, tier=2).base_url.endswith(":9102/v1")
    assert build_llm(s, tier=3).base_url.endswith(":9103/v1")
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_mock_llm.py tests/test_llm_client.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/mock_llm.py src/tiered_rag/llm/client.py \
        tests/test_mock_llm.py tests/test_llm_client.py
git commit -m "feat(p3): deterministic per-tier mock LLM server + tiered build_llm"
```

---

## Task 3: Observability — structured token/cost logging

**Files:**
- Create: `src/tiered_rag/observability.py`
- Test: `tests/test_observability.py`

**Design:** `estimate_cost(tier, usage, settings)` = (`prompt/1k`·input + `completion/1k`·output)
× tier multiplier (tier-1 = 1.0). `UsageRecord` is the per-request dataclass. `UsageLog`
accumulates records in memory **and** emits one structured JSON line per record on the
`tiered_rag.usage` logger; `total_cost` sums the running spend (seed of the Phase-7 savings calc).

**Step 1: Write the failing test** (`tests/test_observability.py`)
```python
import logging

from tiered_rag.config import Settings
from tiered_rag.llm.usage import TokenUsage
from tiered_rag.observability import UsageLog, estimate_cost


def test_cost_increases_with_tier():
    s = Settings()
    u = TokenUsage(prompt_tokens=1000, completion_tokens=1000)
    c1, c2, c3 = estimate_cost(1, u, s), estimate_cost(2, u, s), estimate_cost(3, u, s)
    assert c1 > 0
    assert c3 > c2 > c1  # deeper tiers simulated as pricier


def test_usage_log_accumulates_and_emits(caplog):
    s = Settings()
    log = UsageLog()
    with caplog.at_level(logging.INFO, logger="tiered_rag.usage"):
        rec = log.record(tier=2, model="mock", usage=TokenUsage(40, 10), latency_ms=21.4, settings=s)
    assert rec.total_tokens == 50
    assert rec.cost_usd > 0
    assert len(log.records) == 1
    assert log.total_cost == rec.cost_usd
    assert any("usage" in m for m in caplog.messages)
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.observability`)
Run: `pytest tests/test_observability.py -v`

**Step 3: Implement** `src/tiered_rag/observability.py`:
```python
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from .config import Settings
from .llm.usage import TokenUsage

logger = logging.getLogger("tiered_rag.usage")


def estimate_cost(tier: int, usage: TokenUsage, settings: Settings) -> float:
    multiplier = {1: 1.0, 2: settings.tier2_cost_multiplier, 3: settings.tier3_cost_multiplier}
    base = (usage.prompt_tokens / 1000.0) * settings.cost_input_per_1k \
        + (usage.completion_tokens / 1000.0) * settings.cost_output_per_1k
    return round(base * multiplier.get(tier, 1.0), 8)


@dataclass
class UsageRecord:
    tier: int
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: float


class UsageLog:
    """In-memory collector + structured logger for per-request token/cost usage."""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def record(self, *, tier: int, model: str, usage: TokenUsage,
               latency_ms: float, settings: Settings) -> UsageRecord:
        rec = UsageRecord(
            tier=tier,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=estimate_cost(tier, usage, settings),
            latency_ms=round(latency_ms, 2),
        )
        self.records.append(rec)
        logger.info("usage %s", json.dumps(asdict(rec)))
        return rec

    @property
    def total_cost(self) -> float:
        return round(sum(r.cost_usd for r in self.records), 8)
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_observability.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/observability.py tests/test_observability.py
git commit -m "feat(p3): token/cost observability (UsageRecord + UsageLog + estimate_cost)"
```

---

## Task 4: Wire token logging into the gateway (`/chat` usage + `/usage` summary)

**Files:**
- Modify: `src/tiered_rag/router.py` (add `RouteResult` + `route_detailed`)
- Modify: `src/tiered_rag/api.py` (log usage on `/chat`; add `/usage`)
- Test: `tests/test_router.py` (extend: `route_detailed` exposes usage)
- Test: `tests/test_api.py` (extend: `/chat` usage block + `/usage` summary)

**Design:** `Router.route_detailed(query)` returns `RouteResult{selection, usage}` and `route()`
becomes a thin wrapper (`route_detailed(query).selection`) — so every Phase-2 caller is
untouched. The gateway times the routing call with `perf_counter`, records a `UsageRecord` into a
per-app `UsageLog` (stored on `app.state` for test isolation), returns a `usage` block in the
`/chat` response, and exposes `GET /usage` for the running totals.

**Step 1: Write the failing tests**

Append to `tests/test_router.py`:
```python
def test_route_detailed_exposes_usage():
    from tiered_rag.router import RouteResult
    res = Router(FakeLLM(_canned)).route_detailed("hi there!")
    assert isinstance(res, RouteResult)
    assert res.selection.tier == 1
    assert res.usage.total_tokens > 0
```

Append to `tests/test_api.py`:
```python
def test_chat_includes_token_usage():
    canned = json.dumps({"tier": 1, "reason": "greeting", "plan": None})
    body = _client_with_canned(canned).post("/chat", json={"query": "hello"}).json()
    assert body["usage"]["total_tokens"] > 0
    assert body["usage"]["cost_usd"] >= 0


def test_usage_endpoint_counts_requests_and_cost():
    canned = json.dumps({"tier": 2, "reason": "lookup", "plan": None})
    client = _client_with_canned(canned)  # fresh app -> fresh UsageLog
    client.post("/chat", json={"query": "status of order #1?"})
    client.post("/chat", json={"query": "status of order #2?"})
    summary = client.get("/usage").json()
    assert summary["requests"] == 2
    assert summary["total_cost_usd"] >= 0
```

**Step 2: Run → expect FAIL** (`ImportError: cannot import name 'RouteResult'`; `/chat` body has
no `usage`)
Run: `pytest tests/test_router.py tests/test_api.py -v`

**Step 3: Implement**

Append to `src/tiered_rag/router.py` (and import `TokenUsage`):
```python
from dataclasses import dataclass

from .llm.usage import TokenUsage


@dataclass
class RouteResult:
    selection: TierSelection
    usage: TokenUsage
```
Replace `Router.route` with a detailed variant + thin wrapper:
```python
    def route_detailed(self, query: str) -> RouteResult:
        resp = self.llm.complete(ROUTER_SYSTEM, query, temperature=self.temperature)
        try:
            sel = TierSelection(**_extract_json(resp.content))
        except Exception:
            sel = TierSelection(tier=1, reason="router parse fallback", plan=None)
        return RouteResult(selection=sel, usage=resp.usage)

    def route(self, query: str) -> TierSelection:
        return self.route_detailed(query).selection
```
*(Place `RouteResult` after `TierSelection` is defined; keep imports near the top.)*

Rewrite `src/tiered_rag/api.py`:
```python
import time

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel

from .config import Settings, get_settings
from .llm.client import build_llm
from .observability import UsageLog
from .router import Router


class ChatRequest(BaseModel):
    query: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class ChatResponse(BaseModel):
    tier: int
    reason: str
    plan: str | None
    answer: str  # stubbed in Phase 2/3; real execution lands in Phase 4/6
    usage: Usage


def get_settings_dep() -> Settings:
    return get_settings()


def get_router() -> Router:
    s = get_settings()
    return Router(build_llm(s), temperature=s.router_temperature)


def get_usage_log(request: Request) -> UsageLog:
    return request.app.state.usage_log


def create_app() -> FastAPI:
    app = FastAPI(title="tiered_rag gateway")
    app.state.usage_log = UsageLog()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatResponse)
    def chat(
        req: ChatRequest,
        router: Router = Depends(get_router),
        usage_log: UsageLog = Depends(get_usage_log),
        settings: Settings = Depends(get_settings_dep),
    ):
        t0 = time.perf_counter()
        result = router.route_detailed(req.query)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        sel = result.selection
        rec = usage_log.record(
            tier=sel.tier, model=settings.openai_model,
            usage=result.usage, latency_ms=latency_ms, settings=settings,
        )
        return ChatResponse(
            tier=sel.tier, reason=sel.reason, plan=sel.plan,
            answer=f"[stub] would execute the Tier-{sel.tier} pipeline (Phase 4/6)",
            usage=Usage(
                prompt_tokens=rec.prompt_tokens, completion_tokens=rec.completion_tokens,
                total_tokens=rec.total_tokens, cost_usd=rec.cost_usd,
            ),
        )

    @app.get("/usage")
    def usage_summary(usage_log: UsageLog = Depends(get_usage_log)):
        return {"requests": len(usage_log.records), "total_cost_usd": usage_log.total_cost}

    return app


app = create_app()
```

**Step 4: Run → expect PASS** (and the whole offline suite)
Run: `pytest -m "not integration" -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/router.py src/tiered_rag/api.py tests/test_router.py tests/test_api.py
git commit -m "feat(p3): log per-request token usage on /chat + /usage summary endpoint"
```

---

## Task 5: Dockerfile + compose mock services + integration test + README Phase-3

**Files:**
- Create: `Dockerfile`
- Modify: `docker-compose.yml` (add `mock_tier1/2/3`)
- Create: `tests/test_integration_mock_llm.py` (marked `@pytest.mark.integration`)
- Modify: `README.md` (add a Phase-3 section)

**Design:** a single image runs any tier (`python -m tiered_rag.mock_llm --tier N --port 91xN`);
compose stands up all three alongside Qdrant. The integration test probes `:9101/healthz`,
**skips** if the mock isn't up, otherwise routes the whole labeled set through `LLM_TYPE=mock`
and asserts the deterministic heuristic clears a modest bar (it's deterministic, not smart) and
that token usage is produced end-to-end.

**Step 1: Write the failing/skippable integration test** (`tests/test_integration_mock_llm.py`)
```python
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
```

**Step 2: Run → expect SKIP** (until the mock servers are up)
Run: `pytest -m integration tests/test_integration_mock_llm.py -v`

**Step 3: Implement infra + bring services up**

`Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
RUN pip install --no-cache-dir -e .
# default command runs tier-1 on 9101; compose overrides per service
CMD ["python", "-m", "tiered_rag.mock_llm", "--tier", "1", "--port", "9101"]
```

Append the three mock services to `docker-compose.yml`:
```yaml
  mock_tier1:
    build: .
    command: python -m tiered_rag.mock_llm --tier 1 --port 9101
    ports: ["9101:9101"]
  mock_tier2:
    build: .
    command: python -m tiered_rag.mock_llm --tier 2 --port 9102
    ports: ["9102:9102"]
  mock_tier3:
    build: .
    command: python -m tiered_rag.mock_llm --tier 3 --port 9103
    ports: ["9103:9103"]
```

Bring them up (either way works):
```bash
# via docker-compose
docker compose up -d --build mock_tier1 mock_tier2 mock_tier3
# …or locally, one per shell (no docker needed)
python -m tiered_rag.mock_llm --tier 1 --port 9101
python -m tiered_rag.mock_llm --tier 2 --port 9102
python -m tiered_rag.mock_llm --tier 3 --port 9103
```

`README.md` — add a **Phase-3 "Mock-vs-Real LLM Backend + Token Logging"** section covering: the
3 mock tier servers (ports 9101/9102/9103) + the OpenAI-compatible `/v1/chat/completions` shape;
how `LLM_TYPE=mock` now routes through the tier-1 mock (deterministic heuristic) vs `openai`;
the `complete() → LLMResponse{content, usage}` change; the per-request structured token log + the
simulated per-tier cost model; the `/usage` endpoint; how to run the mocks (compose or local) and
the integration test.

**Step 4: Run the full suite**
```bash
pytest -m "not integration" -v      # all offline unit tests PASS (FakeLLM + TestClient, no sockets)
pytest -m integration -v            # mock routing + Phase-1/2 integration; skips what's down
```

**Step 5: Commit**
```bash
git add Dockerfile docker-compose.yml tests/test_integration_mock_llm.py README.md
git commit -m "feat(p3): Dockerfile + compose mock tier servers + integration test + README"
```

---

## Phase 3 Definition of Done

- [ ] `pytest -m "not integration"` → all green, fully offline (FakeLLM + mock app via TestClient).
- [ ] Three mock tier servers run on **9101/9102/9103**, each serving OpenAI-compatible
      `POST /v1/chat/completions` + `GET /healthz`, deterministic, returning a `usage` block.
- [ ] `LLM_TYPE=mock` routes a query end-to-end through the tier-1 mock (router heuristic).
- [ ] `complete()` surfaces `TokenUsage`; the gateway logs a structured per-request
      token/cost/latency record and `GET /usage` reports running totals.
- [ ] `docker compose up --build mock_tier1 mock_tier2 mock_tier3` brings the servers up from the
      new `Dockerfile`.
- [ ] `pytest -m integration` → mock routing runs against the live servers (skips if down).
- [ ] README Phase-3 section written. All work committed.

**Next:** write `FOURTH_PHASE_PLAN.md` (Tier 1 & Tier 2 Execution) once Phase 3 is green — it
wires RAG retrieval into Tier-1 answers (greeting / FAQ / classification) and builds the Tier-2
pipeline plan → **function calling** (`check_order_status`, `check_item_price`,
`check_account_tier`) + **structured extraction** (`get_item_details_from_xlsx` over
`xlsx/item_details.xlsx`) → `final_input_context` → synthesis, swapping the `/chat` stub for real
answers. The per-tier mock backends and token logging from this phase make that execution both
testable offline and measurable.
```
