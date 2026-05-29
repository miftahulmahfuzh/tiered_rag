# MAJOR PHASES — "Zero-Hallucination" Tiered Support Chatbot

This document is the **master roadmap**. It analyzes user requirements, fixes the
architecture decisions we agreed on, and decomposes the build into **8 incremental
phases**. Each phase ends with a *runnable, tested* deliverable, so the system is
coherent at every phase boundary (good for an early submission).

Detailed, step-by-step implementation plans live in separate files
(`FIRST_PHASE_PLAN.md`, `SECOND_PHASE_PLAN.md`, …). We write each one *just before*
starting that phase, informed by what we actually learned in the previous phase.

---

## 1. Requirement Analysis (from `requiremen.txt`)

The brief asks for a production-grade, cost-efficient, **zero-hallucination** support
chatbot backend. Distilled into capability buckets:

| Brief section | Requirement | Where we deliver it |
|---|---|---|
| A. Multi-Tier Routing | Custom router stages requests into Tier 1/2/3 by complexity | **Phase 2** (decision), **Phase 4 & 6** (execution) |
| A. Tier 1 | Greeting / simple FAQ / classification, no function calling | Phase 2 (route), Phase 4 (answer) |
| A. Tier 2 | Function calling + structured data extraction | Phase 4 |
| A. Tier 3 | Super-complex multi-step reasoning / sensitive complaints | Phase 6 |
| A. Mock tiers | Mock local endpoints on different ports | **Phase 3** |
| B. Strict RAG grounding | Confidence threshold → "I don't know" state | **Phase 1** |
| B. Verification Agent | Verifier compares answer vs retrieved sources | **Phase 5** |
| B. Knowledge-Gap Alerting | Async alert (log/webhook) + "Pending Human Review" reply | **Phase 5** |
| C. Semantic Caching | Redis stores similar query→response pairs | **Phase 7** |
| C. Health Checks / Failover | Detect down model instance, failover to healthy worker | **Phase 7** |
| C. Token & Cost Observability | Structured token logging + Tier 1/2 cost-savings vs all-Tier-3 | **Phase 3** (logging baseline) → **Phase 7** (analysis) |
| 3. Infra | Dockerfile + docker-compose (Gateway + Redis + Mock LLM); Qdrant local | Phases 1, 3, 7 |
| 3. Performance | 100+ concurrent users in load test | **Phase 7** |
| 5. Submission | README.md + EVAL_REPORT.md (abstention rate + token/cost) | **Phase 8** |

---

## 2. Architecture Decisions (locked)

**LLM backend (feature-flagged).** Every tier shares one interface. A flag selects the
backend so we satisfy the brief's "mock local endpoints" *and* get real natural answers:

- `LLM_TYPE=mock` → mock local FastAPI endpoints on separate ports (deterministic,
  offline, ideal for load tests). Satisfies the brief literally.
- `LLM_TYPE=openai` → a single real OpenAI model (`OPENAI_MODEL`, e.g. `gpt-5.4-nano`)
  sits behind **all three tiers**, differentiated by prompt/config/simulated cost. Lets
  us see genuine answers and meaningfully test grounding + verification.

**RAG is always real**, regardless of `LLM_TYPE`: ollama `nomic-embed-text:v1.5`
embeddings + Qdrant vector store, with a confidence threshold driving the "I don't know"
state.

**The Router (the "Staging" engine).** The cheap **Tier 1 LLM is always the entry point
and always decides the route**, emitting a structured `tier_selection_output`:

```
user query
   │
   ▼
[Tier 1 LLM] ── tier_selection_output ──► tier ∈ {1,2,3}
   │
   ├─ tier 1: output ALSO carries the plan inline (no second Tier-1 call)
   ├─ tier 2: call Tier 2 LLM → generate pipeline plan (which functions, what order)
   └─ tier 3: call Tier 3 LLM → generate multi-step chain plan (step→step→step)
   │
   ▼
execute plan → assemble `final_input_context` → final LLM synthesis → answer
```

Using the cheapest model as the router is what we measure for the graded
**"Routing Intelligence"** criterion.

**Test query taxonomy** (drives routing eval + later execution):

1. **Greeting** → T1, no `final_input_context`
2. **Simple FAQ** → T1, single Qdrant retrieval → context
3. **Classification** → T1, direct label (e.g. *"What category is this — Billing,
   Technical, or Account? 'I keep getting logged out.'"* → `Technical`). Sentiment/
   sensitivity classification also feeds T3 escalation.
4. **Function calling** → T2, dummy functions `check_order_status()`,
   `check_item_price()`, `check_account_tier()`
5. **Structured extraction** → T2, `get_item_details_from_xlsx(item_id)` over
   `xlsx/item_details.xlsx`
6. **Multi-step** → T3, 3-step sequential chain (output of step N feeds step N+1)

---

## 3. Target Project Layout (grows across phases)

```
tiered_rag/
├── MAJOR_PHASES.md            # this file
├── FIRST_PHASE_PLAN.md        # detailed plan, written per-phase
├── docker-compose.yml         # Qdrant (P1) → +mock LLMs (P3) → +Redis (P7)
├── .env.example               # committed (placeholders)
├── .env                       # gitignored (real keys)
├── requirements.txt
├── xlsx/
│   ├── knowledge_base.xlsx     # 20 Q&A pairs (Phase 1)
│   └── item_details.xlsx       # structured catalog (Phase 4)
├── src/tiered_rag/
│   ├── config.py               # env-driven settings (LLM_TYPE, thresholds, …)
│   ├── embeddings.py           # ollama nomic-embed-text client + Embedder protocol
│   ├── knowledge_base.py       # xlsx loader
│   ├── vector_store.py         # Qdrant wrapper
│   ├── ingest.py               # build collection from xlsx
│   ├── retrieval.py            # retrieve() + confidence threshold + abstain
│   ├── router.py               # Tier 1 routing (Phase 2)
│   ├── llm/                    # tier backends, mock vs openai (Phase 3)
│   ├── tools/                  # dummy functions (Phase 4)
│   ├── verifier.py             # verification agent (Phase 5)
│   ├── alerting.py             # knowledge-gap alerts (Phase 5)
│   ├── orchestrator.py         # plan execution + synthesis (Phase 4/6)
│   ├── cache.py                # Redis semantic cache (Phase 7)
│   ├── observability.py        # token/cost/latency logging (Phase 3→7)
│   └── api.py                  # FastAPI gateway (Phase 2+)
└── tests/
```

---

## 4. The 8 Phases

### Phase 1 — RAG Foundation & Grounded Retrieval  *(your idea #1)*
**Goal:** Real semantic search with an honest "I don't know" state.
- Qdrant (local) + ollama `nomic-embed-text:v1.5` embeddings.
- `xlsx/knowledge_base.xlsx` with 20 Q&A pairs; ingest into Qdrant.
- `retrieve(query)` returns top matches **with a confidence score**; below threshold →
  **abstain** ("I don't know").
- Tests (offline, in-memory Qdrant + fake embedder) + 1 real-ollama integration test.

**Achieves:** The zero-hallucination *foundation*. Provably abstains on out-of-scope
questions — the core of "Safety" in the rubric. Self-contained, no LLM needed yet.

### Phase 2 — Tier Routing Engine (the "Staging" engine)
**Goal:** Correctly decide Tier 1/2/3; execution stubbed.
- Tier 1 LLM router → `tier_selection_output {tier, reason, plan?}`.
- `LLM_TYPE` / `OPENAI_MODEL` config; FastAPI gateway: query in → tier decided → stub out.
- Labeled routing-accuracy test set over the 6 query categories.

**Achieves:** Measurable **Routing Intelligence**. The "routing works, execution stubbed"
milestone you wanted.

### Phase 3 — Mock-vs-Real LLM Backend + Token Logging
**Goal:** Make `LLM_TYPE` real and start counting tokens from day one.
- 3 mock tier services on separate ports (docker-compose); OpenAI path behind same interface.
- Structured token logging (input vs output) per tier, per request.

**Achieves:** Brief-compliant mock endpoints + the observability backbone for the
cost-savings story.

### Phase 4 — Tier 1 & Tier 2 Execution
**Goal:** Real end-to-end answers for T1 & T2.
- T1 inline answers (greeting / FAQ-with-RAG / classification).
- T2 pipeline-plan → **function calling** (`check_order_status`, `check_item_price`,
  `check_account_tier`) + **structured extraction** (`get_item_details_from_xlsx` over
  `xlsx/item_details.xlsx`) → `final_input_context` → synthesis.

**Achieves:** Function calling + structured data extraction requirements; working chatbot
for the common cases.

### Phase 5 — Zero-Hallucination Guardrails
**Goal:** Never hallucinate; escalate gaps to humans.
- **Verifier agent**: compares the generated answer against retrieved sources; rejects
  unsupported claims.
- **Knowledge-gap alerting**: async mock webhook/log + return "Pending Human Specialist
  Review" when we can't answer accurately.

**Achieves:** Verification Agent + Knowledge-Gap Alerting; trick-question safety.

### Phase 6 — Tier 3 Multi-Step Reasoning  *(your idea #2)*
**Goal:** Handle complex, multi-step troubleshooting.
- Tier 3 LLM generates a **chained plan** (step1 → step2 → step3, each consuming the
  prior step's output) → sequential execution with context threading → final synthesis.

**Achieves:** Tier 3 "super-complex reasoning / multi-step troubleshooting" requirement.

### Phase 7 — High-Scale Engineering
**Goal:** Cost, resilience, and proof it scales.
- **Redis semantic caching** (similar query → cached response).
- **Health checks + failover** across model workers.
- Full **token/cost/latency/staging-efficiency** observability + **cost-savings calc**
  (Tier 1/2 routing vs all-Tier-3).
- **Load test** (100+ concurrent users).

**Achieves:** Section C in full + the performance requirement.

### Phase 8 — Telegram + Final Packaging  *(your idea #3)*
**Goal:** Ship it.
- Telegram bot front-end.
- Final `docker-compose` (Gateway + Redis + Mock LLM + Qdrant), `Dockerfile`.
- `README.md` (architecture + load-test results) + `EVAL_REPORT.md` (abstention rate +
  token/cost-saving analysis).

**Achieves:** A complete, submittable package.

---

## 5. Phase Dependency Graph

```
P1 ─► P2 ─► P3 ─► P4 ─► P5 ─► P6 ─► P7 ─► P8
RAG    route  mock   T1/T2  guard  T3     scale  ship
              +token        rails  multi
              log                  step
```

Each arrow = "builds on". P5 depends on P1 (grounding) + P4 (an answer to verify).
P7 depends on P3 (token logging) + P4/P6 (real calls to cache and balance).

---

## 6. Definition of Done (per phase)

A phase is "done" when: (1) its deliverable runs, (2) its tests pass, (3) a short note is
appended to the eventual `README.md`/`EVAL_REPORT.md` where relevant, and (4) the work is
committed. We then write the next `*_PHASE_PLAN.md`.
