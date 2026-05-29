# EVAL_REPORT — tiered_rag

The graded evaluation of the zero-hallucination tiered support chatbot. Every number below
comes from a **real run** (mirroring the Phase-7 README discipline — *assemble, never invent*).
Each figure is labeled with the date it was measured and the backend it was measured against.
Regenerate the abstention + routing blocks with `python scripts/eval_report.py`; the
cost/cache/load figures come from Phase-7 `/stats` + `scripts/load_test.py`.

## Summary

| Metric | Result | Source | Measured |
|---|---|---|---|
| **Abstention rate** (out-of-scope) | **100%** (10/10) | Phase-1 `eval_abstention`, real ollama + Qdrant | 2026-05-29 |
| **False-abstention** (in-scope paraphrases) | **15%** (3/20) @ threshold `0.6` | Phase-1 `eval_abstention` | 2026-05-29 |
| **Routing accuracy** (real model) | **100%** (16/16) | Phase-2 `eval_routing`, `LLM_TYPE=openai` `gpt-5.4-nano` | 2026-05-29 |
| **Routing accuracy** (deterministic mock) | **88%** | Phase-3 `eval_routing`, `LLM_TYPE=mock` keyword heuristic | 2026-05-29 |
| **Token cost-savings** vs all-Tier-3 | **62.6%** | Phase-7 `/stats` `savings_vs_all_tier3` | 2026-05-29 |
| **Cache hit-rate** | **57.1%** | Phase-7 `/stats` `cache_stats` | 2026-05-29 |
| **Load** (100 concurrent) | `rps=16.7 p50=5768ms p95=8418ms p99=9410ms errors=0` | Phase-7 `scripts/load_test.py` | 2026-05-29 |

**Headline:** the system **never answered an out-of-scope question** (100% abstention), routed
**every** query to the correct tier with the real model (100%), and — by routing the cheap cases
to Tier 1/2 instead of Tier 3 — cost **62.6% less** than running everything at Tier 3, with a
**57.1% cache hit-rate** and **zero errors** at 100-way concurrency.

---

## 1. Abstention rate (Phase 1 — zero-hallucination foundation)

`tiered_rag.eval_abstention.evaluate` runs a real `Retriever` (ollama `nomic-embed-text:v1.5`
embeddings + Qdrant COSINE) over a labeled set: 20 **in-scope** paraphrases of the
`knowledge_base.xlsx` questions (`should_answer=True`) and 10 clearly **out-of-scope** questions
(`should_answer=False`) from `tests/data/eval_questions.py`.

| Class | n | Result |
|---|---|---|
| Out-of-scope (should abstain) | 10 | **abstention rate = 100%** — every OOD question got the honest "I don't know" |
| In-scope paraphrases (should answer) | 20 | **false-abstention = 15%** (3 paraphrases fell below the `0.6` threshold) |

**Interpretation.** The core safety property holds: **the chatbot does not answer questions it has
no grounding for.** At the locked default threshold `0.6`, three heavily-reworded in-scope
paraphrases dip below the bar and over-abstain. A threshold-calibration sweep (Phase 1) found that
**lowering the threshold to ~0.55 removes the false-abstentions while still abstaining on 100% of
OOD** — a safe tuning knob if recall on paraphrases matters more than the conservative default.

> Reproduce: `docker compose up -d qdrant && ollama serve` → `python -m tiered_rag.ingest` →
> `python scripts/eval_report.py`.

---

## 2. Routing accuracy (Phase 2 / Phase 3 — Routing Intelligence)

The cheap Tier-1 LLM is the entry point and decides the tier (1/2/3) for every query.
`tiered_rag.eval_routing.evaluate` scores it over the labeled 6-category set
(`tests/data/routing_questions.py`).

### Real model — `LLM_TYPE=openai`, `gpt-5.4-nano` (measured 2026-05-29)

**Overall accuracy: 100% (16/16).**

| Category | Accuracy |
|---|---|
| greeting | 100% |
| simple_faq | 100% |
| classification | 100% |
| function_calling | 100% |
| structured_extraction | 100% |
| multi_step | 100% |

Confusion matrix is diagonal — no misroutes.

### Deterministic mock — `LLM_TYPE=mock` (measured 2026-05-29)

**Overall accuracy: 88%.** The Tier-1 mock routes by a keyword heuristic (`order`/`sku`/`price` → 2;
`double`/`locked out`/`2fa` → 3; else 1). It is intentionally *deterministic, not smart* — it backs
the offline/load-test path where reproducibility matters more than routing IQ; the real-model path
is what demonstrates Routing Intelligence (1.00 above).

> Reproduce (real): `python scripts/eval_report.py` (with `OPENAI_API_KEY` set).
> Reproduce (mock): bring up the mock tier servers, then
> `pytest -m integration tests/test_integration_mock_llm.py -s`.

---

## 3. Token & cost observability — savings vs all-Tier-3 (Phase 7)

Cost is **simulated, not billed**: per-1K input/output base rates × a per-tier multiplier
(Tier-1 = 1×, Tier-2 = 3×, Tier-3 = 10×). `savings_vs_all_tier3` re-costs every recorded request's
*real* token counts at the Tier-3 multiplier and compares to the actual cost — the graded
"Tier-1/2 routing vs all-Tier-3" number.

From a real `--n 300 --concurrency 100` run against the mock-backed gateway (2026-05-29):

```
savings: actual=$0.030698  all_tier3=$0.082023  savings_pct=62.6%
cache:   hit_rate=57.1%
```

- **62.6% cost-savings** — routing the greeting/FAQ/classification/function-calling cases to the
  cheap tiers instead of Tier 3 cuts simulated spend by nearly two-thirds.
- **57.1% cache hit-rate** — over the repeating query mix, more than half of requests were served
  from the Redis semantic cache at **0 tokens** (only *served* answers are cached — never abstains
  or escalations, so knowledge-gap alerts are never suppressed).

---

## 4. Performance / scale — 100+ concurrent users (Phase 7)

`scripts/load_test.py` drives the 6-category taxonomy at high concurrency against the deterministic
`LLM_TYPE=mock` backend (so the run measures the *gateway*, not a flaky upstream).

```
n=300 concurrency=100 elapsed=17.91s rps=16.7 p50=5768.5ms p95=8418.4ms p99=9410.5ms errors=0
```

**Zero errors** at 100-way concurrency. Latency is dominated by synchronous CPU ollama embedding
behind a single dev uvicorn worker; throughput scales horizontally with more workers and a
GPU/remote embedder. The headline resilience result — **0 errors under 100 concurrent users**,
with `FailoverLLM` skipping a down worker — is what Phase 7 targets.

---

## How these were measured

```bash
# Abstention + routing (live):
docker compose up -d qdrant && ollama serve &
python -m tiered_rag.ingest
python scripts/eval_report.py            # OPENAI_API_KEY set -> real routing; unset -> that block skips

# Cost-savings + cache hit-rate + load (Phase 7):
docker compose up -d --build             # qdrant + redis + mock workers + gateway
python -m tiered_rag.ingest
python scripts/load_test.py --n 300 --concurrency 100
curl -s localhost:8000/stats             # savings_pct + cache hit_rate
```

All figures above are pasted from what these commands printed — never invented.
