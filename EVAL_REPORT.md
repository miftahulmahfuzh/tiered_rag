# EVAL_REPORT

All numbers here are from real runs on my machine, measured on 2026-05-29. I did
not make up any number. If a service was down the harness skips that part instead
of guessing. How to reproduce everything is at the bottom.

Setup when measuring:
- RAG is always real: ollama `nomic-embed-text:v1.5` (768-dim) + Qdrant (cosine).
- Routing accuracy was measured with the real model (`gpt-5.4-nano`, `LLM_TYPE=openai`).
- Load test was run against the mock backend (`LLM_TYPE=mock`), on purpose, so the
  numbers reflect my gateway and not OpenAI rate limits. Mock answers are deterministic.

## Summary

| What | Result |
|---|---|
| Abstention on out-of-scope questions | 100% (10/10) |
| False-abstention on in-scope paraphrases | 15% (3/20), threshold 0.6 |
| Routing accuracy, real model `gpt-5.4-nano` | 15/16 (93.75%), sometimes 16/16 |
| Routing accuracy, deterministic mock | 14/16 (87.5%) |
| Cost saving vs all-Tier-3 (routing, deterministic) | 31.5% |
| Cache hit-rate (under load) | 39.0% (117/300) |
| Load, 100 concurrent | rps=18.1, p50=4745ms, p95=8386ms, p99=9779ms, 0 errors |

## 1. Abstention (the zero-hallucination part)

This is the most important one for the brief. I run the retriever over a labeled set:
20 in-scope paraphrases of the knowledge-base questions (should answer) and 10 clearly
out-of-scope questions (should abstain). Data is in `tests/data/eval_questions.py`.

| Group | n | Result |
|---|---|---|
| Out-of-scope (should say "I don't know") | 10 | 100% abstained |
| In-scope paraphrases (should answer) | 20 | 15% wrongly abstained (3 of 20) |

So the bot never answered a question it had no grounding for. That is the point.

At threshold 0.6, three heavily reworded paraphrases fall just below the bar and get a
wrong "I don't know". I checked a lower threshold around 0.55, it removes those 3 false
abstentions and still abstains on 100% of the out-of-scope set. I kept 0.6 as the default
because I prefer the safe side, but the knob is there.

## 2. Routing accuracy

The cheap Tier-1 model is the entry point and decides the tier (1/2/3) for every query.
I score it on a labeled set of 16 questions across 6 categories
(`tests/data/routing_questions.py`).

Real model (`gpt-5.4-nano`): 15/16 = 93.75% in most runs, 16/16 in some runs.
The only query that flips between runs is "what account tier am I on?". This is a fair
case: my router rule says go to Tier 2 only when the user actually gives an identifier
(an order id, a SKU, an account id). This question asks about an account tier but gives
no account id, so the model is not sure if it is a lookup or a general question. Even when
it picks the wrong tier, the Tier-2 path falls back to RAG, so the user still gets an
answer instead of a dead end.

| Category | Accuracy (real model) |
|---|---|
| greeting | 100% |
| simple_faq | 100% |
| classification | 100% |
| function_calling | 66.7% (the "account tier" question above) |
| structured_extraction | 100% |
| multi_step | 100% |

Deterministic mock router: 14/16 = 87.5%. The mock routes by keywords (order/sku/price -> 2,
double/locked out/2fa -> 3, else 1). It is dumb on purpose, it only exists so the load test
is reproducible and offline. The real model is the one that shows actual routing quality.

## 3. Token usage and cost saving

Cost is simulated, not a real bill. Per-1k input/output base rate times a per-tier
multiplier (Tier 1 = 1x, Tier 2 = 3x, Tier 3 = 10x).

Cost is counted per stage, the way the code actually runs it. The router and the verifier
always run on the Tier-1 model, so I bill their tokens at Tier 1 even when the request ends
up at Tier 2 or 3. Only the answer-generation (LLM planner and LLM final answer) are billed at the route tier. So a
Tier-2 request is Tier-1 overhead + Tier-2 work, not pure Tier-2.

This section is about token consumption, not routing accuracy (that is section 2). So for this
run I send each of the 6 taxonomy queries through its correct tier and path on purpose, so
every path is actually exercised: the greeting really does its short Tier-1 greeting reply, the
classification really does its label call, and so on. The router still makes its real call (its
tokens are the Tier-1 overhead), I only override the decision so the deterministic mock router
does not misroute a greeting and skip a path. One pass, 3 Tier-1 + 2 Tier-2 + 1 Tier-3, same
mix as the load test. Reproduce with `python scripts/cost_breakdown.py`.

The honest way to show tokens is grouped by the tier that was actually **billed**, not by where
the request was **routed**. Then every row is just tokens x multiplier and the rows add up to
the total, no magic:

| Billed at | Input tok | Output tok | Mult | Cost |
|---|---|---|---|---|
| Tier 1 (router + verifier + Tier-1 answers) | 2,762 | 226 | 1x | $0.00054990 |
| Tier 2 (Tier-2 answers) | 434 | 90 | 3x | $0.00035730 |
| Tier 3 (Tier-3 answers) | 573 | 247 | 10x | $0.00234150 |
| **Total (actual)** | 3,769 | 563 | | **$0.00324870** |

Check the rows yourself: e.g. Tier 2 = (434/1000 x $0.00015 + 90/1000 x $0.00060) x 3 =
$0.0003573. The three rows sum to $0.0032487, which is the actual cost.

Same tokens split by stage instead of by tier: the fixed Tier-1 overhead (router + verifier)
is 2,613 in / 141 out, and the answer-generation (LLM planner + LLM final answer) is
1,156 in / 422 out.

Per-query, so the two stage totals are auditable without re-running. The "answer-gen" column
is the executor (planner + final synthesis), billed at the route tier; the "overhead" column
is the router + verifier, always billed at Tier-1. Each column sums to the totals above.

| Query | Tier | Answer-gen in/out | Overhead in/out |
|---|---|---|---|
| hi there! | 1 | 22 / 12 | 304 / 16 |
| how do I reset my password? | 1 | 79 / 48 | 499 / 31 |
| is 'I keep getting logged out' Billing, Technical, or Account? | 1 | 48 / 25 | 317 / 16 |
| what's the status of order #12345? | 2 | 174 / 18 | 310 / 16 |
| give me the full details for item SKU-07 | 2 | 260 / 72 | 511 / 31 |
| I was double-charged, the refund failed, and now I'm locked out | 3 | 573 / 247 | 672 / 31 |
| **Sum** | | **1,156 / 422** | **2,613 / 141** |

The Tier-1 answers (greeting, FAQ, classification) are small (22-79 input tokens) because they
are short single calls. Overhead (2,613 in) is larger than the whole answer-generation
(1,156 in) because the router fires on every query and its system prompt is the biggest single
input each time; that overhead is billed at 1x in both the actual and the all-Tier-3 world, so
it never contributes to the saving.

Saving vs answering everything at Tier 3: I keep the Tier-1 overhead at Tier 1 in both cases
(you cannot avoid the router or the verifier), and only move the answer-generation to Tier 3:

```
actual = $0.00324870   all_tier3 = $0.00474255   saving = 31.5%
```

Where `all_tier3 = $0.00474255` comes from: take the two stage totals above and price them for
the all-Tier-3 world.

- Tier-1 overhead stays at Tier 1 (1x):
  (2,613/1000 x $0.00015 + 141/1000 x $0.00060) x 1  = $0.00047655
- answer-generation moves to Tier 3 (10x):
  (1,156/1000 x $0.00015 + 422/1000 x $0.00060) x 10 = $0.00426600
- sum = $0.00047655 + $0.00426600 = $0.00474255

The only thing that changed vs the actual cost is the answer-generation tier: in reality it is
1x / 3x / 10x depending on the route, in this baseline it is all 10x. The Tier-1 overhead is
identical in both, so it cancels and the whole gap ($0.00474255 - $0.00324870 = $0.00149385)
is exactly the 31.5% saving.

This is stricter than just multiplying the whole request by 10x (that would show a much bigger
but fake number). The Tier-3 answer is already the most expensive part and it gives no saving,
so 31.5% is the honest figure for this mix.

(Side note: the concurrent load test's /stats reported a higher saving, ~33.6%, because the
cache absorbed most of the repeated Tier-3 query so fewer of the expensive Tier-3 answers were
actually billed. That is real, but it mixes caching with routing, so for the cost of routing
itself I trust the deterministic 31.5% above.)

Every request is also logged as one structured JSON line (tier, model, input vs output
tokens, cost, latency, cached). `/usage` and `/stats` expose the running totals, the
per-tier breakdown, the saving, and the cache hit-rate live.

## 4. Cache and load (100 concurrent users)

`scripts/load_test.py` fires 300 requests at concurrency 100, cycling the 6-category
queries, against the mock gateway. I flushed Redis first so the cache hit-rate is clean.

```
n=300 concurrency=100 elapsed=16.55s rps=18.1 p50=4744.9ms p95=8386.2ms p99=9779.4ms errors=0
cache hit_rate = 39.0% (117/300)
```

0 errors at 100 concurrent users. The cache hit-rate is 39% and not higher because at this
concurrency a lot of the first requests for each query fire at the same time, before the
first answer is cached, so they all miss together. Only served answers get cached, never an
abstain or an escalation, so a knowledge gap keeps alerting instead of being frozen in the
cache.

Latency is high because every query does a real ollama embedding on CPU behind a single dev
uvicorn worker. The resilience and cost results are the point here. Throughput would scale
with more workers and a GPU or remote embedder, the design already supports a failover pool
of workers per tier.

## How to reproduce

```bash
# 1. abstention + routing (needs ollama + Qdrant up, OPENAI_API_KEY set for real routing)
docker compose up -d qdrant
./deploy_ollama.sh
python -m tiered_rag.ingest
python scripts/eval_report.py

# 2. per-stage cost breakdown + load + cache (mock backend, deterministic)
docker compose up -d --build          # qdrant + redis + mock workers + gateway
python -m tiered_rag.ingest
python scripts/cost_breakdown.py       # per-billing-tier token split + the 27.2% saving
./clear_cache.sh                       # clean cache hit-rate
python scripts/load_test.py --n 300 --concurrency 100
curl -s localhost:8000/stats
```

Note: routing on the real model is not 100% deterministic even at temperature 0, so you may
see 15/16 or 16/16. The cost saving and cache numbers depend on the query mix, but the
method is fixed. Everything above is what these commands actually printed for me.
