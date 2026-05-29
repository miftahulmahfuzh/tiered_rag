# tiered_rag

A multi-tier support chatbot backend made using Python, built incrementally in
**8 phases** (see [`MAJOR_PHASES.md`](MAJOR_PHASES.md)). This README grows phase by phase.

---

## Phase 1 — RAG Foundation & Grounded Retrieval

Real semantic search with an honest **"I don't know"** state.

- **Embeddings:** ollama `nomic-embed-text:v1.5` (768-dim). The embedder prepends the
  required nomic task prefixes (`search_document: ` for stored docs, `search_query: ` for
  queries).
- **Vector store:** Qdrant (COSINE distance).
- **Knowledge base:** `xlsx/knowledge_base.xlsx` — 20 Q&A pairs across Account, Billing,
  Orders, Items, and General. Queries are matched against the *questions*; the *answer*
  rides along in the payload.
- **Confidence threshold → abstain:** `Retriever.retrieve(query)` returns the top match
  with its cosine `score`. If `top_score < CONFIDENCE_THRESHOLD` (default `0.6`), it
  returns `abstain=True, answer=None` — the foundation of the zero-hallucination guarantee.

### Setup

```bash
# 1. Python env + dependencies
pip install -r requirements.txt

# 2. Vector store
docker compose up -d qdrant

# 3. Embedding model (ollama must be running: `ollama serve`)
ollama pull nomic-embed-text:v1.5

# 4. Build the knowledge base xlsx (reproducible artifact) and ingest into Qdrant
python scripts/build_knowledge_base.py
python -m tiered_rag.ingest
```

### How abstention works

`retrieve()` embeds the query, searches Qdrant for the nearest stored question, and
compares the top cosine similarity against `CONFIDENCE_THRESHOLD`:

- **≥ threshold** → confident: returns the matched answer (`abstain=False`).
- **< threshold** → out of scope: returns `abstain=True`, `answer=None`. The caller/API
  owns the user-facing "I don't know" message.

The abstention evaluation harness (`tiered_rag.eval_abstention.evaluate`) measures, over a
labeled set, the **abstention rate** on out-of-scope questions and the **false-abstention
rate** on in-scope paraphrases — the seed of the eventual `EVAL_REPORT.md`.

### Configuration

All config comes from `Settings` (pydantic-settings, reads `.env`). Copy `.env.example`
to `.env` and adjust. Never hardcode hosts/keys/thresholds.

### Tests

```bash
pytest -m "not integration"   # fast, fully offline (in-memory Qdrant + FakeEmbedder)
pytest -m integration         # real ollama + Qdrant; skips if either is down
```

The offline suite uses an in-memory Qdrant (`QdrantClient(location=":memory:")`) and a
deterministic `FakeEmbedder`, so it needs no running services. The single integration test
ingests the real KB via ollama and asserts an in-scope query is answered while an
out-of-scope query abstains.

---

## Phase 2 — Tier Routing Engine (the "Staging" engine)

The cheap **Tier-1 LLM is the entry point and decides the tier** (1/2/3) for every query.
It emits a structured `TierSelection {tier, reason, plan?}`; a FastAPI gateway takes a query
in, routes it, and returns the decision with a **stubbed** execution answer. Real execution
lands in Phase 4 (Tier 1/2) and Phase 6 (Tier 3).

### The 6-category taxonomy → expected tier

| # | Category | Tier | Example |
|---|---|---|---|
| 1 | Greeting | 1 | "hi there!" |
| 2 | Simple FAQ | 1 | "how do I reset my password?" |
| 3 | Classification | 1 | "Is 'I keep getting logged out' Billing, Technical, or Account?" |
| 4 | Function calling | 2 | "what's the status of order #12345?" |
| 5 | Structured extraction | 2 | "give me the full details for item SKU-42" |
| 6 | Multi-step / sensitive | 3 | "I was double-charged, the refund failed, and now I'm locked out" |

### LLM backend — one interface, two backends behind `LLM_TYPE`

All tiers share a thin `LLMClient` protocol (`complete(system, user, *, temperature)`):

- **`LLM_TYPE=openai`** (default this phase) → `OpenAICompatLLM` calls a real OpenAI model
  (`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`) at `/chat/completions`.
- **`LLM_TYPE=mock`** → the same `OpenAICompatLLM` pointed at `MOCK_LLM_BASE_URL`. The mock
  *servers* themselves are built in **Phase 3**; in Phase 2 the `mock` path is config-only.

We call the OpenAI-compatible HTTP API directly with `httpx` (no `openai` SDK), which keeps
deps light and is forward-compatible with the Phase-3 mock endpoints (same wire shape).
A deterministic `FakeLLM` backs all offline tests — no network.

### Routing: prompt → JSON → validate → safe fallback

`Router.route(query)` prompts the model to reply with **JSON only**, extracts the first JSON
object (tolerating ` ```json ` code fences), and validates it into `TierSelection`. On any
parse/validation failure it **falls back to Tier 1** (cheapest, safe default) so a flaky model
never crashes the gateway.

### Running the gateway

```bash
uvicorn tiered_rag.api:app --reload      # serves on http://localhost:8000
```

Endpoints:

- `GET /healthz` → `{"status": "ok"}` (no LLM call).
- `POST /chat` with `{"query": "..."}` → `{"tier", "reason", "plan", "answer"}` where
  `answer` is a Phase-2 **stub** (`"[stub] would execute the Tier-N pipeline (Phase 4/6)"`).

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
     -d '{"query":"what is the status of order #12345?"}'
# -> {"tier":2,"reason":"...","plan":null,"answer":"[stub] would execute the Tier-2 pipeline (Phase 4/6)"}
```

The `Router` is wired via a FastAPI `get_router` dependency, so tests override it with a
`FakeLLM`-backed router and never touch the network.

### Measuring Routing Intelligence

`tiered_rag.eval_routing.evaluate(router, dataset)` reports overall **accuracy**,
**per-category** accuracy, a **confusion** map, and per-item records over the labeled
6-category set in `tests/data/routing_questions.py`. This is the seed of the eventual
`EVAL_REPORT.md`.

Run the real-OpenAI routing accuracy check (skips automatically without a key):

```bash
pytest -m integration tests/test_integration_routing.py -s   # asserts accuracy >= 0.80
```

> **Result (2026-05-29):** with `OPENAI_MODEL=gpt-5.4-nano`, routing accuracy was **1.00**
> across all six categories.

---

## Phase 3 — Mock-vs-Real LLM Backend + Token Logging

Phase 3 makes the `LLM_TYPE=mock` path **real** and starts **counting tokens from day one**.
Three deterministic mock tier servers run on separate ports, every LLM call surfaces its token
`usage`, and the gateway logs a structured per-request cost record exposed via `/usage` — the
observability backbone for the Phase-7 cost-savings story (Tier-1/2 routing vs all-Tier-3).

### Three mock tier servers (separate ports)

A single `create_mock_app(tier)` factory (`tiered_rag.mock_llm`) backs all three servers; each
instance is pinned to its tier at launch. This is the brief's literal "mock local endpoints on
different ports", with zero duplicated server code:

| Service | Port | Speaks |
|---|---|---|
| `mock_tier1` | 9101 | `POST /v1/chat/completions` + `GET /healthz` |
| `mock_tier2` | 9102 | same |
| `mock_tier3` | 9103 | same |

The `/v1/chat/completions` shape matches `OpenAICompatLLM` (base-url + `/chat/completions`), so the
same client talks to the mocks and to real OpenAI unchanged. Every response carries a real `usage`
block. The replies are **deterministic and fully offline** (ideal for the Phase-7 load test):

- **Tier-1 mock doubles as the router backend.** When the request carries the router system prompt
  (detected by the `ROUTER_MARKER` substring — a guard test keeps it in sync with `ROUTER_SYSTEM`),
  it returns valid `TierSelection` JSON whose tier is chosen by a keyword heuristic (`order`/`sku`/
  `price` → 2; `double`/`locked out`/`2fa` → 3; else 1).
- **Otherwise** it returns a canned `"[mock tier-N] …"` answer (forward-compatible with Phase-4
  execution).

```bash
# bring all three up via docker-compose (uses the new Dockerfile)
docker compose up -d --build mock_tier1 mock_tier2 mock_tier3
# …or locally, one per shell (no docker needed)
python -m tiered_rag.mock_llm --tier 1 --port 9101
python -m tiered_rag.mock_llm --tier 2 --port 9102
python -m tiered_rag.mock_llm --tier 3 --port 9103
```

`LLM_TYPE=mock` now routes through these servers (`build_llm(settings, tier)` selects the port);
`LLM_TYPE=openai` still points the same client at the real model behind all three tiers.

### `complete()` now surfaces token usage

The `LLMClient` contract changed from returning `str` to returning
`LLMResponse{content, usage: TokenUsage}` — token usage is the whole point of this phase, so the
client surfaces it instead of hiding it. Usage comes from the response's `usage` block (both real
OpenAI and our mock return one) and falls back to a deterministic `~4-chars/token` estimate only if
absent, so the number is **always present**. `Router.route()`'s public contract is unchanged; a new
`Router.route_detailed()` returns `RouteResult{selection, usage}` to expose usage to the gateway.

### Per-request structured token/cost log + `/usage`

`tiered_rag.observability` records, per request, a structured JSON line on the `tiered_rag.usage`
logger and accumulates the running spend:

| Field | Source |
|---|---|
| `tier` | router decision (1/2/3) |
| `model` | `OPENAI_MODEL` (or `mock`) |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | response `usage` (or estimate) |
| `cost_usd` | `estimate_cost(tier, usage, settings)` |
| `latency_ms` | `perf_counter` around the routing call |

**Cost is simulated, not billed:** per-1K input/output base rates × a per-tier multiplier
(tier-1 = 1×, tier-2 = 3×, tier-3 = 10× by default, all from `Settings`). The point is the
*relative* cost so Phase 7 can compute routing savings — cheap router, pricey deep reasoning.

`/chat` now returns a `usage` block and `GET /usage` reports the running totals:

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
     -d '{"query":"what is the status of order #12345?"}'
# -> {"tier":2,"reason":"mock tier-2 (deterministic)","plan":null,
#     "answer":"[stub] would execute the Tier-2 pipeline (Phase 4/6)",
#     "usage":{"prompt_tokens":151,"completion_tokens":16,"total_tokens":167,"cost_usd":9.675e-05}}

curl -s localhost:8000/usage
# -> {"requests":2,"total_cost_usd":0.00012795}
```

Execution stays **stubbed** in Phase 3 — `/chat` still returns the Phase-2 stub answer; Phase 3 only
adds the `usage` block and logging around the routing call. Real Tier-1/2 execution lands in Phase 4.

### Tests

```bash
pytest -m "not integration"                       # all offline (FakeLLM + mock app via TestClient, no sockets)
pytest -m integration tests/test_integration_mock_llm.py -s   # routes the labeled set through the live mocks
```

The offline suite exercises the mock app via `fastapi.testclient.TestClient` (no sockets). The
integration test probes `:9101/healthz`, **skips** if the mocks aren't up, otherwise routes the
whole labeled 6-category set through `LLM_TYPE=mock` and asserts the deterministic heuristic clears
a modest bar plus end-to-end token usage.

> **Result (2026-05-29):** against the live mock servers, the deterministic keyword router scored
> **0.88** routing accuracy on the labeled set (it's deterministic, not smart — the real-model path
> hit 1.00 in Phase 2).
