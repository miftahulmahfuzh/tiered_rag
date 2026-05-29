# tiered_rag

A **zero-hallucination, multi-tier support chatbot backend**, built incrementally in **8 phases**
(see [`MAJOR_PHASES.md`](MAJOR_PHASES.md)). A cheap Tier-1 LLM routes every query to Tier 1/2/3,
the chosen tier executes a plan grounded in real RAG, a verifier guards against hallucination, and
a semantic cache + failover pool keep it cheap and resilient at scale. The graded evaluation lives
in [`EVAL_REPORT.md`](EVAL_REPORT.md). This README grows phase by phase; the overview below is the
top-level map, the per-phase sections that follow are the detailed record.

## Architecture at a glance

```
            Telegram user                         curl / HTTP client
                 │ message                               │ POST /chat {query}
                 ▼                                        ▼
   POST /telegram/webhook ──(secret check)──►  process_query(query)   ◄── single source of truth
                 │  background reply                      │
                 │                                        ▼
                 │                 ┌──────────── SEMANTIC CACHE (Phase 7) ─────────────┐
                 │                 │ embed → cosine ≥ threshold → HIT: serve @0 tokens │
                 │                 └───────────────────────┬───────────────────────────┘
                 │                                         │ MISS
                 │                                         ▼
                 │                          Orchestrator.run(query)
                 │           ┌─────────────────────────────────────────────────────────┐
                 │           │  Tier-1 router LLM → tier ∈ {1,2,3}      (Phase 2)      │
                 │           │   ├─ T1: greeting / FAQ(+RAG) / classify  (Phase 4)     │
                 │           │   ├─ T2: LLM-planned tool pipeline        (Phase 4)     │
                 │           │   └─ T3: multi-step reasoning chain       (Phase 6)     │
                 │           │  build_llm → FailoverLLM worker pool      (Phase 7)     │
                 │           │  RAG: ollama nomic-embed + Qdrant, abstain (Phase 1)    │
                 │           └───────────────────────┬─────────────────────────────────┘
                 │                                   ▼
                 │                 GUARDRAIL: verifier + knowledge-gap alert (Phase 5)
                 │                  abstain → "I don't know" ; unsupported → "Pending Review"
                 ▼                                   ▼
            send reply                  ChatResponse{answer, tier, usage, verified, cached}
                                        usage_log → /usage, /stats (cost-savings)  (Phase 3→7)
```

## Quick Start

```bash
# 1. dependencies + the whole stack (Qdrant + Redis + mock LLM workers + gateway on :8000)
pip install -r requirements.txt
cp .env.example .env                       # then fill in secrets (see below) — .env is gitignored
docker compose up -d --build

# 2. embeddings model + knowledge base
ollama pull nomic-embed-text:v1.5          # ollama must be running (`ollama serve`)
python -m tiered_rag.ingest                # load xlsx/knowledge_base.xlsx into Qdrant

# 3. talk to it
curl -s localhost:8000/healthz                                            # {"status":"ok"}
curl -s localhost:8000/chat -H 'content-type: application/json' \
     -d '{"query":"how do I reset my password?"}'
```

`LLM_TYPE=mock` (the compose default) makes every answer deterministic and fully offline;
set `LLM_TYPE=openai` + `OPENAI_API_KEY` in `.env` to put a real model behind all three tiers.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | liveness — `{"status":"ok"}`, no LLM call |
| `POST` | `/chat` | main entry: `{query}` → `{tier, reason, plan, answer, usage, verified, pending_review, cached}` |
| `GET` | `/usage` | running request count + total simulated cost + cache hit-rate |
| `GET` | `/stats` | per-tier breakdown + **cost-savings vs all-Tier-3** + cache stats |
| `POST` | `/telegram/webhook` | Telegram transport — validates the shared secret, reuses `process_query` (Phase 8) |

## Telegram front-end (Phase 8)

The Telegram bot is a thin **transport** over the same `/chat` pipeline — no new answer logic. A
message hits `POST /telegram/webhook`, which validates the shared secret, runs `process_query`
(router → tier → guardrail → cache) in a background task, and replies via the Bot API. The
`TelegramClient` speaks the Bot API over raw `httpx` (no SDK), mirroring `OpenAICompatLLM`.

**Security — the bot token lives only in the gitignored `.env`** (`config.py` default is empty,
`.env.example` holds placeholders). Never commit the real token.

```bash
# .env (gitignored)
TELEGRAM_BOT_TOKEN=<token-from-BotFather>
TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 16)
```

**Webhook setup (with ngrok):**

```bash
docker compose up -d --build && python -m tiered_rag.ingest    # gateway on :8000
ngrok http 8000                                                # copy the https URL it prints
python scripts/set_telegram_webhook.py --url https://<id>.ngrok-free.app
#   registers https://<id>.ngrok-free.app/telegram/webhook with the secret, then confirms via getWebhookInfo
python scripts/set_telegram_webhook.py --delete                # remove the webhook
```

**Local-dev fallback (no ngrok)** — long-poll instead of a public webhook (delete the webhook first,
they conflict):

```bash
python scripts/telegram_poll.py --gateway http://localhost:8000
```

Both transports feed the **same** `process_query`, so they produce identical answers.

## Evaluation

See [`EVAL_REPORT.md`](EVAL_REPORT.md) for the graded results from real runs: **100% abstention** on
out-of-scope questions, **100% routing accuracy** (real model) / **88%** (deterministic mock),
**62.6% cost-savings** vs all-Tier-3, a **57.1% cache hit-rate**, and **0 errors at 100 concurrent
users**.

## Tests

```bash
pytest -m "not integration"   # fast, fully offline (in-memory Qdrant + FakeEmbedder + FakeLLM + TestClient)
pytest -m integration         # real ollama/Qdrant/Redis/mock-workers/Telegram; each skips if its service is down
```

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

---

## Phase 4 — Tier 1 & Tier 2 Execution

Phase 4 swaps the Phase-2/3 `/chat` **stub** for **real end-to-end answers** on Tier 1 and Tier 2.
The router still decides the tier (Phase 2) and every LLM call still surfaces token usage (Phase 3);
Phase 4 fills in *execution*. An `Orchestrator` ties the router to per-tier executors:

```
POST /chat → Orchestrator.run(query)
   │  router.route_detailed(query) → TierSelection{tier, reason, plan} + usage
   │
   ├─ tier 1 → Tier1Executor.execute(query, plan)   # plan carried inline by the router
   │     greeting       → synth(greeting prompt, no context)        # no RAG
   │     faq            → Retriever.retrieve → abstain? "I don't know" (no LLM call)
   │                                          : synth(grounded prompt, context = retrieved answer)
   │     classification → synth(label prompt) → category label
   │
   ├─ tier 2 → Tier2Executor.execute(query)
   │     Tier-2 LLM → pipeline plan JSON {"calls":[{tool,args}, …]}
   │     run TOOLS[tool](args, catalog) for each call → tool_calls
   │     assemble final_input_context (formatted tool results) → grounded synth
   │
   └─ tier 3 → stub answer (wired in Phase 6)
   │
   ▼  ExecutionResult{tier, reason, plan, answer, final_input_context, tool_calls, usage}
   ▼  /chat logs the single AGGREGATED usage (router + planner + synthesis) under the chosen tier
```

### Tier-1 execution — inline plan dispatch

Per the locked architecture, the **Tier-1 router carries its plan inline** (no second Tier-1 call):
`ROUTER_SYSTEM` now sets `plan` to `greeting` / `faq` / `classification` for tier 1 (tier 2/3 keep
`plan: null`). `Tier1Executor` dispatches on that plan and **defaults unknown/missing plans to
`faq`**, so a flaky model never crashes execution:

- **greeting** → a one-line warm reply, **no RAG**.
- **faq** → real **RAG retrieval**; if `Retriever.retrieve` **abstains** (top score below
  `CONFIDENCE_THRESHOLD`), it **short-circuits the LLM entirely** and returns the canonical
  "I don't know" message (cheaper + provably grounded — the Phase-1 guarantee, now wired into an
  answer). Otherwise it synthesizes a reply grounded in the retrieved answer.
- **classification** → a single direct label.

Grounding is enforced **in the synthesis prompt** (`FAQ_SYSTEM`): the model is told to answer **only**
from the provided `CONTEXT` and to say it can't answer when the context is empty/insufficient —
forward-compatible with the Phase-5 verifier.

### Tier-2 execution — LLM-planned tool pipeline

`Tier2Executor` runs the brief's **function calling** + **structured extraction**:

1. **Plan** — the **Tier-2 LLM** is shown the tool menu and asked for JSON
   `{"calls": [{"tool": "...", "args": {...}}, …]}`. The reply is parsed with the router's tolerant
   `_extract_json` and validated into a `Tier2Plan`; an unparseable plan degrades to an empty plan.
2. **Execute** — each call is dispatched through the `TOOLS` registry. An unknown tool or bad args is
   recorded as an `{"error": …}` result and execution continues — the pipeline **never crashes**.
3. **Assemble** `final_input_context` — a readable block of `tool(args) -> result` lines.
4. **Synthesize** — one grounded synthesis call (`FAQ_SYSTEM`) over that context.

The four tools live behind a single registry (`tiered_rag.tools.registry`), all with a uniform
`run(args, catalog) -> dict` signature:

| Tool | Kind | Behaviour |
|---|---|---|
| `check_order_status(order_id)` | function calling | deterministic status (`processing`/`shipped`/`delivered`/`cancelled`) + tracking number from a hash of the id |
| `check_account_tier(account_id)` | function calling | deterministic membership tier (`Bronze`/`Silver`/`Gold`/`Platinum`) |
| `check_item_price(item_id\|sku)` | catalog lookup | price from the catalog, or `{"error": "unknown item"}` |
| `get_item_details_from_xlsx(item_id\|sku)` | structured extraction | full catalog row, or `{"error": "item not found"}` |

The catalog is `xlsx/item_details.xlsx` (12 items, generated by `scripts/build_item_details.py`),
loaded once into a lookup keyed by both `item_id` and `sku` (case-insensitive) via
`catalog_index(load_item_details(path))`. Tools are deterministic and fully offline.

### Aggregated per-request usage

`ExecutionResult.usage` **sums the token usage of every LLM call** made for a request (router +
planner + synthesis). `/chat` logs that single aggregated record under the chosen tier, so the
Phase-7 cost-savings math sees the *true* per-tier cost, not just the routing call. The Phase-3
`usage` block and `GET /usage` running totals are unchanged.

```bash
uvicorn tiered_rag.api:app --reload

curl -s localhost:8000/chat -H 'content-type: application/json' \
     -d '{"query":"how do I reset my password?"}'
# -> {"tier":1,"reason":"...","plan":"faq",
#     "answer":"Open Settings > Security > Reset.","usage":{...,"total_tokens": N}}

curl -s localhost:8000/chat -H 'content-type: application/json' \
     -d '{"query":"give me the full details for item SKU-07"}'
# -> {"tier":2,"reason":"...","plan":null,
#     "answer":"... Dragon Skin, Legendary, $19.99 ...","usage":{...}}
```

Tier 3 still returns a **stub** ("[stub] would run the Tier-3 multi-step chain (Phase 6)"); wiring it
is Phase 6.

### Tests

```bash
pytest -m "not integration"                          # all offline (FakeLLM + in-memory Qdrant + TestClient)
# bring the Phase-3 mocks up, then:
pytest -m integration tests/test_integration_pipeline.py -s   # full /chat pipeline via live mock servers
```

Everything new is offline-testable with `FakeLLM` + in-memory Qdrant + `FakeEmbedder`: an injected
`llm_for: Callable[[int], LLMClient]` factory lets tests supply a per-tier `FakeLLM` with zero
network. The one `@integration` test drives `/chat` end-to-end through the live mock tier servers
(skips if they're down).

> **Result (2026-05-29):** the full offline suite is green; the `/chat` pipeline runs end-to-end
> through the live mock servers (Tier-2 structured-extraction path) with aggregated token usage.

---

## Phase 5 — Zero-Hallucination Guardrails

Phase 4 produces a real answer **plus the exact evidence it was built from**
(`ExecutionResult.final_input_context` + `tool_calls`). Phase 5 inserts a **guardrail stage**
between synthesis and the user so the chatbot **provably refuses to hallucinate**: a cheap LLM
**verifier** checks the answer against its evidence, and a **knowledge-gap alerter** escalates
anything the system cannot answer safely.

```
<tier executor> → ExecutionResult{answer, final_input_context, tool_calls, usage, abstained}
   │
   ▼  ┌──────────────────────── GUARDRAIL (Orchestrator._guardrail) ────────────────────────┐
   │  │ 1. abstained?  (retrieval below threshold)                                          │
   │  │       → GapAlert(kind="abstain") ; reply stays the honest "I don't know"            │
   │  │ 2. has evidence AND a verifier is wired?                                            │
   │  │       verdict = Verifier.verify(query, answer, evidence)  (+usage folded in)        │
   │  │       supported    → keep answer ; verified=True                                    │
   │  │       NOT supported → GapAlert(kind="unverified") ; verified=False ;                │
   │  │                       answer := PENDING_REVIEW                                      │
   │  │ 3. no evidence (greeting/classification/T3 stub) → skip (verified=None)             │
   │  └─────────────────────────────────────────────────────────────────────────────────────┘
   ▼  ExecutionResult{…, verified: bool|None, gap: GapAlert|None}
   ▼  /chat → if res.gap: BackgroundTasks.add_task(alerter.alert, res.gap)   # async, after reply
           → ChatResponse{…, verified, pending_review}
```

### Verifier agent — grounded answer check, **fail-closed**

`tiered_rag.verifier.Verifier.verify(query, answer, evidence) -> (Verdict, TokenUsage)` makes **one**
LLM call with a strict grounding instruction (`VERIFIER_SYSTEM`) asking for JSON
`{"supported": bool, "reason": str}`. The reply is parsed with the Phase-2 tolerant `_extract_json`.

- The verifier is built from the **cheapest tier** (`llm_for(1)`) — grounding is a yes/no check — and
  its usage **folds into `ExecutionResult.usage`**, so the Phase-7 cost math sees the true
  per-request cost *including the guardrail call*.
- **Fail-closed:** on any parse/validation failure it returns
  `Verdict(supported=False, reason="verifier parse failure (fail-closed)")`. An unparseable verdict
  means "we cannot prove this is grounded", so the answer is **not** served. Safety over availability.
- **Opt-in by construction:** `Orchestrator(..., verifier: Verifier | None = None)`. When the verifier
  is `None`, verification is skipped (`verified` stays `None`) — production wires a real verifier only
  when `settings.verify_answers` is true (the default).

### Knowledge-gap alerting — two kinds, one channel, two replies

`tiered_rag.alerting.Alerter` mirrors the Phase-3 `UsageLog`: every `GapAlert` is (1) appended to an
in-memory `alerts` list (test hook + inspection), (2) logged as a structured JSON line on the
`tiered_rag.alerts` logger, and (3) — if `ALERT_WEBHOOK_URL` is set — POSTed to that webhook
**best-effort** (errors swallowed; alerting must never break a request).

| Gap kind | When | User-facing reply |
|---|---|---|
| `abstain` | retrieval found nothing usable (out of scope) | the honest **"I don't know"** (Phase-1 contract preserved) |
| `unverified` | we had sources but the answer drifted off them | **"Pending Human Specialist Review"** (`PENDING_REVIEW`) |

Both fire the same async alert so the KB can grow; the `kind` field distinguishes them downstream.
Verification only runs when there **is** evidence — greeting, classification, and the Tier-3 stub carry
an empty `final_input_context` and bypass the verifier (`verified=None`); it applies to Tier-1 **FAQ**
(grounded in the retrieved KB answer) and **Tier-2** (grounded in the formatted tool results).

### Async dispatch from `/chat`

The guardrail *decision* lives in the `Orchestrator` (it owns the answer); the alert *I/O* lives in the
API. `create_app` builds an app-scoped `Alerter(settings.alert_webhook_url)` on `app.state` (like
`UsageLog`). `/chat` takes FastAPI `BackgroundTasks` and, **only if `res.gap is not None`**, schedules
`background_tasks.add_task(alerter.alert, res.gap)` — so alerting fires *after* the response is sent and
never blocks the reply. `ChatResponse` gains two fields:

- `verified`: `true` / `false` / `null` (not applicable / not run).
- `pending_review`: `true` when the answer was escalated (`gap.kind == "unverified"`).

```bash
uvicorn tiered_rag.api:app --reload

# An in-scope answer that drifts off its sources is escalated, not served:
curl -s localhost:8000/chat -H 'content-type: application/json' \
     -d '{"query":"how do I reset my password?"}'
# -> {"tier":1,"reason":"...","plan":"faq",
#     "answer":"This needs a human specialist. I've flagged it for review (Pending Human
#                Specialist Review) and someone will follow up shortly.",
#     "usage":{...},"verified":false,"pending_review":true}
# (and an async knowledge_gap alert is logged on the `tiered_rag.alerts` logger)
```

### Verifier-aware mock

The Tier-1 mock already returns routing JSON when it sees `ROUTER_MARKER`. It now also returns a
deterministic **supported** verdict when it sees `VERIFIER_MARKER` (a stable substring of
`VERIFIER_SYSTEM`, pinned by a guard test), so the live-mock pipeline never spuriously escalates. The
verifier is *meaningfully* exercised on the `LLM_TYPE=openai` path and in the offline guardrail tests
(which inject approving/rejecting verifiers directly).

### Tests

```bash
pytest -m "not integration"      # all offline (FakeLLM + in-memory Qdrant + TestClient)
# bring the Phase-3 mocks up, then:
pytest -m integration            # pipeline + Phase-1/2/3; skips what's down
```

`TestClient` runs `BackgroundTasks` synchronously on response, so tests assert `app.state.alerter.alerts`
after a `/chat` call. The fail-closed verifier, the two gap kinds, the `verified`/`pending_review`
fields, and the verifier-aware mock are all covered offline.

> **Result (2026-05-29):** full offline suite green (FakeLLM + in-memory Qdrant + `TestClient`); the
> guardrail runs end-to-end through the live mock servers without spuriously escalating
> (`pending_review=false` on the mock path).

---

## Phase 6 — Tier-3 Multi-Step Reasoning

Phase 6 replaces the Tier-3 **stub** with a real **multi-step reasoning chain**. Per the locked
architecture (taxonomy #6), Tier 3 handles **super-complex, multi-step troubleshooting and sensitive
complaints**. The cheap Tier-1 router still decides the tier (Phase 2); when it routes to Tier 3 the
**Tier-3 LLM generates a chained plan**, a `Tier3Executor` runs the chain **sequentially with context
threading**, and the accumulated transcript becomes the `final_input_context` for a single **grounded**
synthesis — which then flows through the **exact same Phase-5 guardrail** with zero new wiring.

```
Router.route_detailed(query) → TierSelection{tier=3} + usage
   │
   ▼  Tier3Executor.execute(query)
   │   1. PLAN     Tier-3 LLM → {"steps":[{instruction, tool?, args?}, …]}   (parsed, degrade-to-empty)
   │   2. EXECUTE  each step (capped at tier3_max_steps), threading the running transcript:
   │                 tool != null & known → run_tool(tool, args, catalog)        → record + append
   │                 tool == "retrieve"   → Retriever.retrieve(args.query|query) → record + append
   │                 tool == null         → LLM reasoning over PRIOR STEPS + instruction → append
   │   3. ASSEMBLE final_input_context = the full "[step k] … -> …" transcript
   │   4. SYNTH    synthesize(FAQ_SYSTEM, transcript, query)   (grounded in the chain's evidence)
   ▼  ExecutionResult{tier=3, answer, final_input_context, tool_calls, usage}
   ▼  ┌──── GUARDRAIL (Phase 5, UNCHANGED) ────┐
      │ non-empty context + verifier wired?    │  supported → keep ; NOT supported → escalate
      └────────────────────────────────────────┘
```

### Three step kinds

A chain **step** is one of three kinds — the first two inject **real evidence** so the chain isn't just
LLM free-text, which is what makes a Tier-3 answer genuinely groundable:

| Step kind | Trigger | Behaviour | LLM tokens |
|---|---|---|---|
| **tool** | `tool` set to a known tool | dispatched through the Phase-4 `TOOLS` registry (`check_order_status`, `check_item_price`, `check_account_tier`, `get_item_details_from_xlsx`); unknown tool / bad args → `{"error": …}`, chain never crashes | 0 |
| **retrieve** | `tool == "retrieve"` | grounds the chain in the real KB via the Phase-1 `Retriever` (`{answer, abstain, score}`) | 0 |
| **reasoning** | `tool is None` | an LLM call (`TIER3_STEP_SYSTEM`) over `PRIOR STEPS:\n{transcript}\n\nNOW DO: {instruction}` — step N's output is in step N+1's prompt | folded in |

### Context threading + bounded chains

Each step appends a `"[step k] … -> …"` line to a running transcript; the **next step's prompt includes
that transcript**, so the output of step N threads forward into step N+1. The chain is **bounded** by
`tier3_max_steps` (default 5, from `Settings` — never hardcoded): a plan longer than the cap is silently
truncated to the first N steps (observable as fewer `[step k]` lines / `tool_calls`).

### Cost is honest

Deterministic **tool** and **retrieve** steps cost **0 LLM tokens**; only **reasoning** steps and the
final **synthesis** consume tokens. `ExecutionResult.usage` folds **plan + every reasoning step + synth**
(and then the router call + the Phase-5 verifier call in the orchestrator), so the per-request cost that
feeds the Phase-7 cost math reflects the *true* cost of the chain **plus** the guardrail.

### The guardrail applies to Tier 3 for free

There is **zero new guardrail wiring**. `Orchestrator._guardrail` already verifies any result whose
`final_input_context` is non-empty. Because `Tier3Executor` populates `final_input_context` with the
transcript, the Phase-5 **verifier + knowledge-gap escalation apply to Tier 3 automatically** — an
unsupported chain answer is escalated to **"Pending Human Specialist Review"** exactly like Tier 1/2.
The orchestrator change is a one-line swap of the stub for `Tier3Executor(...).execute(query)`.

### Deterministic Tier-3 mock

The Tier-1 mock already returns routing JSON (`ROUTER_MARKER`) and a supported verdict
(`VERIFIER_MARKER`). It now also recognises `TIER3_PLAN_MARKER` (a stable substring of
`TIER3_PLAN_SYSTEM`, pinned by a guard test) and returns a deterministic **reasoning-only 2-step chain
plan**, so the live Tier-3 mock drives a real chain whose step/synth calls fall through to the canned
`"[mock tier-3] …"` answer. Combined with the verifier-aware mock, the live-mock pipeline runs a real
Tier-3 chain end-to-end and never spuriously escalates.

```bash
uvicorn tiered_rag.api:app --reload     # LLM_TYPE=mock, mock tier servers up

curl -s localhost:8000/chat -H 'content-type: application/json' \
     -d '{"query":"I was double-charged, the refund failed, and now I'\''m locked out"}'
# -> {"tier":3,"reason":"mock tier-3 (deterministic)","plan":null,
#     "answer":"[mock tier-3] deterministic answer for: CONTEXT:\n[step 1] assess the complaint ...",
#     "usage":{"prompt_tokens":948,"completion_tokens":153,"total_tokens":1101,"cost_usd":0.00234},
#     "verified":true,"pending_review":false}
```

The multi-step query routes to Tier 3, the chain runs (plan → 2 reasoning steps with context threading →
grounded synthesis), the verifier confirms the answer is supported, and the aggregated usage covers the
whole request (router + plan + steps + synth + verifier).

### Tests

```bash
pytest -m "not integration"      # all offline (FakeLLM + in-memory Qdrant + TestClient)
# bring the Phase-3 mocks up, then:
pytest -m integration            # pipeline (incl. live-mock Tier-3 chain) + Phase-1/2/3; skips what's down
```

Everything new is offline-testable: chain threading, the tool/retrieve/reasoning step kinds, the
`tier3_max_steps` cap, and degrade-to-empty / never-crash on bad plans/tools are all covered with crafted
plans injected via `FakeLLM`. One `@integration` test routes a multi-step query through the live mock
tier servers (skips if down).

> **Result (2026-05-29):** full offline suite green (91 tests); the live-mock pipeline runs a real
> Tier-3 chain end-to-end (`tier=3`, aggregated `total_tokens > 0`, `pending_review=false`).

---

## Phase 7 — High-Scale Engineering

Phase 7 wraps the working Phase-1–6 chatbot in the four **Section C** pillars — **semantic caching**,
**health-check failover**, an **observability rollup with cost-savings**, and a **load test** — *without
changing any tier's answer*. Everything is feature-flagged and offline-testable: the cache, failover pool,
and rollup are all backed by injected protocols, so the offline suite uses `FakeEmbedder` + an in-memory
cache + `FakeLLM` + a `FakeRedis` double (no Redis, no sockets), and the real path uses Redis + the live
mock workers (exercised only under `@pytest.mark.integration`, which skips if a service is down).

```
POST /chat (query)
   │
   ▼  ┌──────────────── SEMANTIC CACHE ────────────── ──┐
   │  │ vec = embedder.embed_query(query)               │
   │  │ hit = nearest past query, cosine >= threshold   │
   │  └─────────────────────────────────────────────────┘
   │        │ HIT                              │ MISS
   │        ▼                                   ▼
   │   cached payload                     Orchestrator.run(query)   (Phases 2–6)
   │   {answer, tier, usage:0,              build_llm(s, tier) -> FAILOVER pool:
   │    cached:true}                          FailoverLLM([worker_a, worker_b, …])
   │        │                                  try healthiest → down? → next worker
   │        │                                   │
   │        │                                   ▼  cacheable? (served, not abstain/escalation) → cache.put
   │        ▼                                   ▼
   └────────────── usage_log.record(…, cached) ──── ──────────┐
   GET /usage → totals + cache hit-rate                       │
   GET /stats → per-tier breakdown + COST-SAVINGS vs all-Tier-3
   scripts/load_test.py → 100+ concurrent users → p50/p95/rps/errors
```

### Semantic cache — embed → cosine ≥ threshold → serve at 0 tokens

`SemanticCache(embedder, backend, threshold)` owns the embedding + cosine + threshold logic, mirroring how
the retriever already works; the **backend** only stores/scans `(vector, payload)` entries. It reuses the
**same `Embedder`** as the retriever — no new vector DB.

- `put(query, payload)` embeds the query and stores `{**payload, "query": query}`.
- `get(query)` embeds, scans the backend, and returns the payload of the **best cosine match ≥
  `cache_similarity_threshold`** (default `0.95` — a high bar, so only near-duplicate queries hit), else
  `None`.
- **Two backends behind a `CacheBackend` protocol:** `InMemoryCacheBackend` (offline default, a bounded ring
  buffer) and `RedisCacheBackend` (real path — one Redis hash per entry, `EXPIRE cache_ttl_seconds`, an
  inserts counter that rolls the key id `mod cache_max_entries` to bound the set). The cap keeps the
  brute-force cosine scan cheap at take-home scale; the interface is shaped so a future RediSearch / Qdrant
  vector-index backend drops in without touching `SemanticCache`.
- **Only *served* answers are cached** (`cacheable(res)` → `not res.abstained and res.gap is None`).
  Abstains and escalations (`Pending Human Specialist Review`) are **never** cached — caching one would
  suppress the Phase-5 knowledge-gap alert and freeze a gap we want humans to close.
- A **cache hit returns `usage = 0` and `cached = true`**, **skipping the orchestrator entirely** (the whole
  point: a hit costs no tokens), and is still recorded in `UsageLog` so hit-rate is observable.

### Health checks + failover — `FailoverLLM` worker pool

A tier can have **multiple mock workers** (comma-separated `MOCK_TIER{N}_WORKERS`). `build_llm(s, tier)`
builds one `OpenAICompatLLM` per URL and returns the single client unchanged when there's one (so the
Phase-3 path and the `openai` single-model path are **backward-compatible**), else wraps them in a
`FailoverLLM`:

- `complete()` tries workers in **health order** (fewest recent failures first); on success it records
  success and returns the `LLMResponse`; on **any** exception it records a failure and tries the next
  worker; it re-raises only when **all** workers are down.
- A lightweight `WorkerHealth` deprioritizes a worker that just failed, so the next request tries a healthy
  one first. The **core guarantee — try the next worker on failure — needs no `/healthz` probe**, so it's
  fully testable offline with a down-worker double (one raising, one healthy).

### Observability rollup + cost-savings — pure reductions over `UsageLog`

No new accounting — three pure functions over the existing per-request records:

- `by_tier()` → `{tier: {requests, prompt_tokens, completion_tokens, total_tokens, cost_usd,
  avg_latency_ms}}`.
- `savings_vs_all_tier3(settings)` → re-costs every recorded request's tokens at the **Tier-3 multiplier**
  and compares to the actual cost: `{actual_cost_usd, all_tier3_cost_usd, savings_usd, savings_pct}`. This
  is exactly the graded "Tier-1/2 routing vs all-Tier-3" number — meaningful only now that Phases 4–6
  produce *real* per-tier token counts.
- `cache_stats()` → `{requests, cache_hits, cache_misses, hit_rate}` from the `cached` flag.

`GET /stats` returns all three; `GET /usage` folds in the cache hit-rate.

```bash
curl -s localhost:8000/stats | python -m json.tool
# -> {"by_tier": {...}, "savings": {"savings_pct": 0.65, ...}, "cache": {"hit_rate": 0.57, ...}}
```

### Load test — 100+ concurrent users against the deterministic mock backend

`scripts/load_test.py` drives `--n` requests at `--concurrency` (defaults 200 / 100) over the 6-category
query taxonomy with `asyncio` + `httpx.AsyncClient`, then prints rps / p50 / p95 / p99 / errors and fetches
`/stats` for the cost-savings + cache hit-rate. `LLM_TYPE=mock` makes every answer deterministic and
offline, so the run measures the *gateway's* behaviour, not a flaky upstream.

```bash
docker compose up -d --build      # qdrant + redis + mock_tier1/1b/2/3 + a failover-capable gateway
python -m tiered_rag.ingest       # KB into Qdrant (for the FAQ + retrieve paths)
python scripts/load_test.py --n 300 --concurrency 100
curl -s localhost:8000/stats      # capture savings_pct + cache hit_rate
```

`docker compose up` now brings up **Qdrant + Redis + four mock workers (incl. a Tier-1 replica `mock_tier1b`
on `:9111`) + a cache-backed, failover-capable `gateway`** wired with
`MOCK_TIER1_WORKERS=http://mock_tier1:9101/v1,http://mock_tier1b:9111/v1` and `REDIS_URL`.

> **Result (2026-05-29, measured — not invented):** a real `--n 300 --concurrency 100` run against the local
> mock-backed gateway (single uvicorn worker, **real ollama embeddings on CPU** for every FAQ/cache query):
>
> ```
> n=300 concurrency=100 elapsed=17.91s rps=16.7 p50=5768.5ms p95=8418.4ms p99=9410.5ms errors=0
> savings: actual=$0.030698 all_tier3=$0.082023 savings_pct=62.6%  cache hit_rate=57.1%
> ```
>
> **Zero errors** at 100-way concurrency, **~62.6% cost savings** vs running every request at Tier 3, and a
> **57.1% cache hit-rate** over the repeating query mix. (Latency is dominated by synchronous CPU ollama
> embedding behind a single dev gateway — the headline resilience/cost results are what Phase 7 targets;
> throughput would scale horizontally with more uvicorn workers and a GPU/remote embedder.)

### Tests

```bash
pytest -m "not integration"      # all offline: FakeEmbedder + InMemoryCacheBackend + FakeLLM + FakeRedis + TestClient
# bring up the stack, then:
docker compose up -d --build && python -m tiered_rag.ingest
pytest -m integration            # live Redis round-trip + live-worker failover + concurrent-burst smoke + Phase 1–6
```

The cache cosine/threshold/TTL/cap, the `cacheable` guard, the `RedisCacheBackend` (dict-double), the
`FailoverLLM` (fail-over, all-down-raises, health ordering), the `by_tier`/`savings`/`cache_stats` rollups,
and the `/chat` cache short-circuit (hit → 0 tokens, skips the orchestrator) + `/stats` are all covered
offline. Three new `@integration` modules cover the live Redis cache, failover to a live worker, and a
concurrent-burst smoke test (each skips if its service is down).

> **Result (2026-05-29):** full offline suite green (**112 tests**); against the live stack all **9
> `@integration` tests pass** — the Redis cache round-trips, `FailoverLLM` skips a down worker and the live
> Tier-1 mock answers, and a 50-request / 20-concurrency burst returns **zero errors**.

---

## Phase 8 — Telegram Front-End + Final Packaging

Phase 8 ships it: a **Telegram bot front-end** over the already-complete `/chat` pipeline, the
finalized `docker compose` stack, and the two submission documents. The bot is a **new transport,
not new logic** — the `/chat` body was extracted into a module-level `process_query(...)` (single
source of truth) and both `POST /chat` and `POST /telegram/webhook` call it, so answers are
byte-for-byte identical regardless of transport.

```
Telegram → POST /telegram/webhook → validate X-Telegram-Bot-Api-Secret-Token
        → extract_message(update) → BackgroundTasks: process_query(text) → TelegramClient.send_message
        → {"ok": true} returned immediately (so Telegram never times out / retries)
```

- **`TelegramClient`** (`tiered_rag.telegram`) speaks the Bot API over raw `httpx` (no SDK),
  mirroring `OpenAICompatLLM`: `send_message`, `get_me`, `set_webhook`, `delete_webhook`,
  `get_webhook_info`. **`extract_message(update)`** is a pure, never-raises parser returning
  `(chat_id, text)` for a text message or `None` for anything else (edits, callbacks, malformed).
- **Webhook safety:** the handler validates the shared secret (returns a `200` on mismatch so a
  forgery isn't retried), ignores non-text/malformed updates without raising, and does the slow
  work in `BackgroundTasks` after responding.
- **Token hygiene:** `telegram_bot_token` / `telegram_webhook_secret` default empty in `config.py`;
  the real token lives only in the gitignored `.env`; `.env.example` holds placeholders. The
  compose `gateway` receives `TELEGRAM_*` pass-through from the host `.env`.
- **Setup + scripts:** see the **Telegram front-end** section near the top — `scripts/set_telegram_webhook.py`
  (register/delete the webhook via ngrok) and `scripts/telegram_poll.py` (no-ngrok long-poll fallback).

### Tests

```bash
pytest -m "not integration"      # all offline (FakeTelegramClient spy + TestClient; /chat unchanged)
pytest -m integration tests/test_integration_telegram.py -s   # live getMe (skips without a token)
```

The webhook (replies to the right chat, ignores non-message updates, rejects a bad secret, reuses
the cache), the `extract_message` parser, and the `TelegramClient` (against a stubbed `httpx`) are
all covered offline; one `@integration` test hits the real `getMe`.

> **Result (2026-05-29):** full offline suite green (**120 tests**), with all Phase-7 `/chat` tests
> still passing after the `process_query` refactor (byte-for-byte unchanged). `EVAL_REPORT.md`
> assembles the graded numbers from real runs (see [`EVAL_REPORT.md`](EVAL_REPORT.md)).
