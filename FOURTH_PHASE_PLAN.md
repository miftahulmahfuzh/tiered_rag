# Phase 4 — Tier 1 & Tier 2 Execution — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to
> implement this plan task-by-task. Use superpowers-extended-cc:test-driven-development
> for every task (RED → GREEN → COMMIT).

**Goal:** Swap the Phase-2/3 `/chat` **stub** for **real end-to-end answers** on Tier 1 and
Tier 2. The router already decides the tier (Phase 2) and every LLM call already surfaces token
usage (Phase 3); Phase 4 fills in *execution*:

- **Tier 1** answers **inline** (the tier-1 router output carries the plan, per the locked
  architecture — no second tier-1 call):
  - **greeting** → a friendly reply, **no RAG**;
  - **simple FAQ** → real **RAG retrieval** → grounded synthesis, **abstaining** ("I don't know")
    when retrieval is below threshold (the Phase-1 guarantee, now wired into an answer);
  - **classification** → a direct label.
- **Tier 2** runs a **pipeline plan**: the **Tier-2 LLM generates the plan** (which tool(s), in
  what order, with what args) → execute the tools → assemble `final_input_context` → final LLM
  synthesis. Tools are the brief's **function calling** (`check_order_status`, `check_item_price`,
  `check_account_tier`) + **structured extraction** (`get_item_details_from_xlsx` over
  `xlsx/item_details.xlsx`).
- **Tier 3** stays **stubbed** (the multi-step chain lands in Phase 6).

RAG stays real; the LLM stays feature-flagged (`mock`/`openai`). Everything new is offline-testable
with `FakeLLM` + in-memory Qdrant + `FakeEmbedder`; one `@integration` test runs the full pipeline
through the live mock tier servers.

**Architecture (what Phase 4 adds):**

```
POST /chat
   │
   ▼
Router.route_detailed(query) ── TierSelection{tier, reason, plan} + usage
   │
   ├─ tier 1 ─► Tier1Executor (dispatch on inline plan ∈ {greeting, faq, classification})
   │              greeting        → synth(greeting prompt, no context)
   │              faq             → Retriever.retrieve → abstain? "I don't know"
   │                                                   : synth(grounded prompt, context=hits)
   │              classification  → synth(label prompt) → category label
   │
   ├─ tier 2 ─► Tier2Executor
   │              Tier-2 LLM → pipeline plan JSON {calls:[{tool,args}, …]}
   │              execute TOOLS[tool](args, catalog) for each call
   │              assemble final_input_context (tool results, formatted)
   │              synth(grounded prompt, context=final_input_context)
   │
   └─ tier 3 ─► stub answer (Phase 6)
   │
   ▼
ExecutionResult{tier, reason, plan, answer, final_input_context, tool_calls, usage(aggregated)}
   │
   ▼
/chat → UsageLog.record(tier, model, aggregated_usage, latency, cost) → response{answer, usage}
```

**Tech Stack:** builds on Phase 1 (retrieval), Phase 2 (router), Phase 3 (LLM client + usage +
`UsageLog`). **No new runtime deps** — tools are pure-Python over a `pandas`-loaded xlsx; the
planner/synthesis reuse the existing `LLMClient`. Offline tests use `FakeLLM`, in-memory Qdrant
(`QdrantClient(location=":memory:")`) + `FakeEmbedder`; one `@integration` test hits the live mock
servers from Phase 3.

**Key design decisions (locked for this phase):**

- **Tier-1 carries its plan inline.** `ROUTER_SYSTEM` is extended so a tier-1 decision sets
  `plan` to one of `greeting` / `faq` / `classification`. Tier-2/3 keep `plan: null` at route time
  (the Tier-2 LLM generates its pipeline plan in a *second* call, per the locked architecture). The
  **Phase-2 routing eval only checks `tier`**, so this stays green; the executor also **defaults
  unknown/missing tier-1 plans to `faq`** so a flaky model never crashes execution.
- **Grounding is enforced in the synthesis prompt** (forward-compatible with the Phase-5 verifier):
  synthesis is instructed to answer **only** from the provided `final_input_context` and to say it
  cannot answer when the context is empty/insufficient. Tier-1 FAQ **abstention short-circuits the
  LLM entirely** — if `Retriever.retrieve` abstains, we return the canonical "I don't know" message
  without a synthesis call (cheaper + provably grounded).
- **Tools are deterministic and offline.** `check_order_status` / `check_account_tier` synthesize
  stable dummy data from a hash of the id (no network, same id → same result). `check_item_price`
  and `get_item_details_from_xlsx` read a **pre-loaded catalog dict** (built once from the xlsx),
  so the tools themselves need no file IO and tests pass a tiny inline catalog.
- **LLM-per-tier via an injected factory.** The orchestrator takes
  `llm_for: Callable[[int], LLMClient]` (production default `lambda t: build_llm(settings, t)`), so
  tests inject `FakeLLM`s per tier with zero network. Tier-1 synthesis uses the tier-1 LLM; Tier-2
  planning + synthesis use the tier-2 LLM.
- **Usage aggregates across the whole pipeline.** `ExecutionResult.usage` sums the token usage of
  every LLM call made for a request (router + planner + synthesis). `/chat` logs that single
  aggregated record under the chosen tier — so the Phase-7 cost-savings math sees the true per-tier
  cost, not just the routing call.
- **Execution boundary unchanged for Tier 3.** Tier 3 still returns the stub answer; only the
  message is reworded to "(Phase 6)". Wiring it is Phase 6.

**New/changed files at a glance:**

| File | Change |
|---|---|
| `scripts/build_item_details.py` | **new** — generates `xlsx/item_details.xlsx` (structured catalog) |
| `xlsx/item_details.xlsx` | **new** — committed artifact |
| `src/tiered_rag/knowledge_base.py` | + `load_item_details()` + `catalog_index()` |
| `src/tiered_rag/config.py` | + `item_details_path` |
| `src/tiered_rag/tools/__init__.py`, `.../tools/registry.py` | **new** — tools + `TOOLS` registry |
| `src/tiered_rag/router.py` | extend `ROUTER_SYSTEM` (tier-1 inline plan) |
| `src/tiered_rag/orchestrator.py` | **new** — `Tier1Executor`, `Tier2Executor`, `Orchestrator`, `ExecutionResult` |
| `src/tiered_rag/api.py` | `/chat` calls the orchestrator (real answer + aggregated usage) |

---

## Task 0: Structured item catalog — `xlsx/item_details.xlsx` + loader + config

**Files:**
- Create: `scripts/build_item_details.py`
- Modify: `src/tiered_rag/knowledge_base.py` (add `load_item_details` + `catalog_index`)
- Modify: `src/tiered_rag/config.py` (add `item_details_path`)
- Test: `tests/test_item_details.py`
- Test: `tests/test_config.py` (extend)

**Design:** mirror `build_knowledge_base.py`. The catalog is a small deterministic table the Tier-2
tools read: columns `item_id, sku, name, price_usd, rarity, stock, category`. `load_item_details`
reuses the same `pandas` read as the KB loader and returns a `list[dict]`. `catalog_index(rows)`
builds a lookup keyed by **both** `str(item_id)` and the upper-cased `sku` (so "7" and "SKU-07" both
resolve), which the tools use. The routing set references "Dragon Skin", "item id 7", and "SKU-42";
include a *Dragon Skin* row and ids 1–12 (so id 7 resolves) but deliberately **no SKU-42** (so the
"not found" path is exercised in Task 1).

**Step 1: Write the failing tests** (`tests/test_item_details.py`)
```python
from tiered_rag.knowledge_base import catalog_index, load_item_details


def test_loads_catalog_rows():
    rows = load_item_details("xlsx/item_details.xlsx")
    assert len(rows) >= 10
    first = rows[0]
    assert {"item_id", "sku", "name", "price_usd", "rarity", "stock", "category"} <= first.keys()
    assert len({r["item_id"] for r in rows}) == len(rows)  # unique ids


def test_catalog_index_keys_by_id_and_sku():
    rows = [{"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
             "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"}]
    idx = catalog_index(rows)
    assert idx["7"]["name"] == "Dragon Skin"
    assert idx["SKU-07"]["name"] == "Dragon Skin"   # sku lookup, case-normalized
    assert idx["sku-07"]["name"] == "Dragon Skin"
```

Append to `tests/test_config.py`:
```python
def test_phase4_item_details_path_default():
    assert Settings().item_details_path.endswith("item_details.xlsx")
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError`/`ImportError: catalog_index`; missing file;
`AttributeError: item_details_path`)
Run: `pytest tests/test_item_details.py tests/test_config.py -v`

**Step 3: Implement**

`scripts/build_item_details.py` (abbreviated — fill all 12 rows; keep prices/rarity/stock stable):
```python
"""Generate xlsx/item_details.xlsx — a small structured item catalog for Tier-2
structured extraction + price lookups. Run once to (re)produce the committed artifact:

    python scripts/build_item_details.py
"""
from pathlib import Path

import pandas as pd

ITEMS = [
    {"item_id": 1,  "sku": "SKU-01", "name": "Starter Sword",   "price_usd": 4.99,  "rarity": "Common",    "stock": 999, "category": "Weapon"},
    {"item_id": 2,  "sku": "SKU-02", "name": "Iron Shield",     "price_usd": 6.49,  "rarity": "Common",    "stock": 540, "category": "Armor"},
    {"item_id": 7,  "sku": "SKU-07", "name": "Dragon Skin",     "price_usd": 19.99, "rarity": "Legendary", "stock": 42,  "category": "Cosmetic"},
    # … fill ids 3–6 and 8–12 with stable, factual rows (12 total) …
]


def main() -> None:
    out = Path("xlsx/item_details.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["item_id", "sku", "name", "price_usd", "rarity", "stock", "category"]
    pd.DataFrame(ITEMS, columns=cols).to_excel(out, index=False)
    print(f"wrote {len(ITEMS)} items to {out}")


if __name__ == "__main__":
    main()
```

Add to `src/tiered_rag/knowledge_base.py`:
```python
def load_item_details(path: str) -> list[dict]:
    df = pd.read_excel(path)
    return df.to_dict(orient="records")


def catalog_index(rows: list[dict]) -> dict[str, dict]:
    """Lookup keyed by both str(item_id) and upper-cased sku."""
    idx: dict[str, dict] = {}
    for r in rows:
        idx[str(r["item_id"])] = r
        idx[str(r["sku"]).upper()] = r
    return idx
```
*(The tool normalizes its lookup key to `str(...).upper()` so `sku-07` resolves — see Task 1.)*

Add to `Settings` (after the Phase-3 cost knobs):
```python
    # --- Tier-2 structured extraction (Phase 4) ---
    item_details_path: str = "xlsx/item_details.xlsx"
```

Then generate the artifact: `python scripts/build_item_details.py`.

**Step 4: Run → expect PASS**
Run: `pytest tests/test_item_details.py tests/test_config.py -v`

**Step 5: Commit**
```bash
git add scripts/build_item_details.py xlsx/item_details.xlsx \
        src/tiered_rag/knowledge_base.py src/tiered_rag/config.py \
        tests/test_item_details.py tests/test_config.py
git commit -m "feat(p4): item_details.xlsx catalog + loader/index + config"
```

---

## Task 1: Tier-2 tools + registry (function calling + structured extraction)

**Files:**
- Create: `src/tiered_rag/tools/__init__.py`
- Create: `src/tiered_rag/tools/registry.py`
- Test: `tests/test_tools.py`

**Design:** four deterministic tools, all reachable through a single registry so the Tier-2 planner
can name them and the executor can dispatch uniformly. Each registry entry is a `Tool{name,
description, run}` where `run(args: dict, catalog: dict) -> dict` (tools that don't need the catalog
ignore it — uniform signature keeps the executor a one-liner).

- `check_order_status(order_id)` — deterministic status from a hash of `order_id`
  (`processing|shipped|delivered|cancelled`) + a tracking number. (function calling)
- `check_item_price(item_id, catalog)` — price from the catalog or `{"error": "unknown item"}`.
- `check_account_tier(account_id)` — deterministic tier (`Bronze|Silver|Gold|Platinum`). (function calling)
- `get_item_details_from_xlsx(item_id, catalog)` — the full catalog row, or
  `{"error": "item not found"}`. (structured extraction)

**Step 1: Write the failing tests** (`tests/test_tools.py`)
```python
from tiered_rag.tools.registry import TOOLS, run_tool

CATALOG = {
    "7": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
          "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"},
    "SKU-07": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
               "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"},
}


def test_registry_has_the_four_brief_tools():
    assert {"check_order_status", "check_item_price",
            "check_account_tier", "get_item_details_from_xlsx"} <= set(TOOLS)


def test_check_order_status_is_deterministic():
    a = run_tool("check_order_status", {"order_id": "12345"}, CATALOG)
    b = run_tool("check_order_status", {"order_id": "12345"}, CATALOG)
    assert a == b and a["status"] in {"processing", "shipped", "delivered", "cancelled"}


def test_get_item_details_hit_and_miss():
    hit = run_tool("get_item_details_from_xlsx", {"item_id": "SKU-07"}, CATALOG)
    assert hit["name"] == "Dragon Skin" and hit["rarity"] == "Legendary"
    miss = run_tool("get_item_details_from_xlsx", {"item_id": "SKU-42"}, CATALOG)
    assert "error" in miss


def test_check_item_price_reads_catalog():
    assert run_tool("check_item_price", {"item_id": "7"}, CATALOG)["price_usd"] == 19.99


def test_unknown_tool_raises_keyerror():
    import pytest
    with pytest.raises(KeyError):
        run_tool("no_such_tool", {}, CATALOG)
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.tools.registry`)
Run: `pytest tests/test_tools.py -v`

**Step 3: Implement**

`src/tiered_rag/tools/__init__.py`: *(empty — package marker)*

`src/tiered_rag/tools/registry.py`:
```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

_STATUSES = ["processing", "shipped", "delivered", "cancelled"]
_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]


def _bucket(key: str, n: int) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % n


def check_order_status(order_id: str) -> dict:
    return {
        "order_id": order_id,
        "status": _STATUSES[_bucket("order:" + order_id, len(_STATUSES))],
        "tracking_number": f"TRK-{_bucket(order_id, 1_000_000):06d}",
    }


def check_account_tier(account_id: str) -> dict:
    return {"account_id": account_id,
            "account_tier": _TIERS[_bucket("acct:" + account_id, len(_TIERS))]}


def _lookup(item_id: str, catalog: dict) -> dict | None:
    return catalog.get(str(item_id).upper()) or catalog.get(str(item_id))


def check_item_price(item_id: str, catalog: dict) -> dict:
    row = _lookup(item_id, catalog)
    if not row:
        return {"error": "unknown item", "item_id": item_id}
    return {"item_id": item_id, "name": row["name"], "price_usd": row["price_usd"]}


def get_item_details_from_xlsx(item_id: str, catalog: dict) -> dict:
    row = _lookup(item_id, catalog)
    return dict(row) if row else {"error": "item not found", "item_id": item_id}


@dataclass
class Tool:
    name: str
    description: str
    run: Callable[[dict, dict], dict]


TOOLS: dict[str, Tool] = {
    "check_order_status": Tool(
        "check_order_status", "Look up the live status of an order by order_id.",
        lambda args, catalog: check_order_status(str(args["order_id"]))),
    "check_item_price": Tool(
        "check_item_price", "Get the current price of an item by item_id or sku.",
        lambda args, catalog: check_item_price(str(args["item_id"]), catalog)),
    "check_account_tier": Tool(
        "check_account_tier", "Get the membership tier for an account_id.",
        lambda args, catalog: check_account_tier(str(args["account_id"]))),
    "get_item_details_from_xlsx": Tool(
        "get_item_details_from_xlsx", "Get the full catalog record for an item_id or sku.",
        lambda args, catalog: get_item_details_from_xlsx(str(args["item_id"]), catalog)),
}


def run_tool(name: str, args: dict, catalog: dict) -> dict:
    return TOOLS[name].run(args, catalog)
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_tools.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/tools/__init__.py src/tiered_rag/tools/registry.py tests/test_tools.py
git commit -m "feat(p4): Tier-2 tools (function calling + structured extraction) + registry"
```

---

## Task 2: Router carries the Tier-1 plan inline

**Files:**
- Modify: `src/tiered_rag/router.py` (`ROUTER_SYSTEM`)
- Test: `tests/test_router.py` (extend)

**Design:** per the locked architecture, a tier-1 decision must carry its plan inline so the
executor needs no second tier-1 call. Extend `ROUTER_SYSTEM` so the model sets `plan` to one of
`greeting` / `faq` / `classification` **for tier 1** (and leaves `plan: null` for tier 2/3). The
schema (`plan: str | None`) is unchanged; only the prompt and one new test change. The Phase-2
routing eval checks `tier` only, so it stays green.

**Step 1: Write the failing test** — append to `tests/test_router.py`:
```python
def test_tier1_plan_is_an_intent_label():
    canned = json.dumps({"tier": 1, "reason": "greeting", "plan": "greeting"})
    sel = Router(FakeLLM(canned)).route("hi there!")
    assert sel.tier == 1
    assert sel.plan in {"greeting", "faq", "classification"}
```
*(This passes against the schema already; its real purpose is to **pin the contract** that tier-1
`plan` is an intent label. Confirm it currently passes, then update `ROUTER_SYSTEM` so the live
model actually produces it — verified by the Task-6 integration run.)*

**Step 2: Run** — `pytest tests/test_router.py -v` (this test goes green immediately; the behavior
change is in the prompt, exercised end-to-end in Task 6).

**Step 3: Implement** — extend `ROUTER_SYSTEM` in `src/tiered_rag/router.py`:
```python
ROUTER_SYSTEM = """You are the Tier-1 router for a game-store support chatbot.
Classify the user's message into exactly ONE tier, then reply with ONLY a JSON object.

Tiers:
- 1 = a greeting, a simple FAQ answerable from a knowledge base, or a single
  classification/label request.
- 2 = needs a function call or structured data lookup: order status, item price or
  item details, or account tier.
- 3 = complex multi-step troubleshooting, or a sensitive/escalation complaint.

For tier 1, set "plan" to the intent: "greeting", "faq", or "classification".
For tier 2 and tier 3, set "plan" to null (the tier's own model builds the plan later).

Reply with JSON only (no prose, no markdown fence):
{"tier": <1|2|3>, "reason": "<short reason>", "plan": <"greeting"|"faq"|"classification"|null>}
"""
```

**Step 4: Run → expect PASS** (router + eval suites stay green)
Run: `pytest tests/test_router.py tests/test_eval_routing.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/router.py tests/test_router.py
git commit -m "feat(p4): router emits the Tier-1 intent as an inline plan"
```

---

## Task 3: Tier-1 execution (greeting / FAQ-with-RAG / classification)

**Files:**
- Create: `src/tiered_rag/orchestrator.py` (`ExecutionResult`, synthesis helper, `Tier1Executor`)
- Test: `tests/test_orchestrator_tier1.py`

**Design:** `Tier1Executor.execute(query, plan)` dispatches on the inline plan (defaulting unknown →
`faq`):
- **greeting** → one synthesis call with a greeting system prompt, **no context**.
- **faq** → `Retriever.retrieve(query)`; if `abstain` → return the canonical
  `I_DONT_KNOW` message with `final_input_context=""` and **no LLM call**; else build context from
  the retrieved answer/hits and synthesize a grounded reply.
- **classification** → one synthesis call with a labeling system prompt; the answer is the label.

`synthesize(llm, system, context, query) -> LLMResponse` sends `system` + a user message embedding
`context` and `query`; the **grounding instruction** lives in the system prompt. `ExecutionResult`
captures everything the gateway + Phase-5 verifier need.

**Step 1: Write the failing tests** (`tests/test_orchestrator_tier1.py`)
```python
from qdrant_client import QdrantClient

from tiered_rag.ingest import ingest
from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import I_DONT_KNOW, ExecutionResult, Tier1Executor
from tiered_rag.retrieval import Retriever
from tiered_rag.vector_store import QdrantStore


def _retriever(fake_embedder, threshold):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    ingest([{"id": 1, "question": "how do I reset my password",
             "answer": "Open Settings > Security > Reset.", "category": "Account"}],
           store, fake_embedder)
    return Retriever(store, fake_embedder, threshold=threshold)


def test_greeting_answers_without_rag(fake_embedder):
    ex = Tier1Executor(_retriever(fake_embedder, 0.6), FakeLLM("Hi! How can I help?"))
    res = ex.execute("hi there!", plan="greeting")
    assert isinstance(res, ExecutionResult)
    assert res.answer == "Hi! How can I help?"
    assert res.final_input_context == ""        # no RAG for greetings
    assert res.usage.total_tokens > 0


def test_faq_synthesizes_from_retrieved_context(fake_embedder):
    # FakeLLM echoes the user message so we can prove the context was passed in
    ex = Tier1Executor(_retriever(fake_embedder, 0.6), FakeLLM(lambda s, u: u))
    res = ex.execute("how do I reset my password", plan="faq")
    assert "Open Settings > Security > Reset." in res.final_input_context
    assert "Open Settings > Security > Reset." in res.answer   # grounded in context


def test_faq_abstains_below_threshold_without_calling_llm(fake_embedder):
    def _boom(s, u):
        raise AssertionError("LLM must not be called when retrieval abstains")
    ex = Tier1Executor(_retriever(fake_embedder, 0.999), FakeLLM(_boom))
    res = ex.execute("what is the capital of France", plan="faq")
    assert res.answer == I_DONT_KNOW
    assert res.final_input_context == ""


def test_unknown_plan_defaults_to_faq(fake_embedder):
    ex = Tier1Executor(_retriever(fake_embedder, 0.6), FakeLLM(lambda s, u: u))
    res = ex.execute("how do I reset my password", plan=None)
    assert "Open Settings > Security > Reset." in res.answer
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.orchestrator`)
Run: `pytest tests/test_orchestrator_tier1.py -v`

**Step 3: Implement** `src/tiered_rag/orchestrator.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field

from .llm.client import LLMClient
from .llm.usage import LLMResponse, TokenUsage
from .retrieval import Retriever

I_DONT_KNOW = ("I'm sorry, I don't have enough information to answer that. "
               "Let me know if there's something else I can help with.")

GREETING_SYSTEM = "You are a friendly game-store support agent. Greet the user warmly in one line."
FAQ_SYSTEM = ("You are a support agent. Answer the user's question using ONLY the CONTEXT below. "
              "If the context does not contain the answer, say you don't know. Do not invent facts.")
CLASSIFY_SYSTEM = ("Classify the user's message into a single category label and reply with the "
                   "label only (e.g. Billing, Technical, Account, Orders).")


@dataclass
class ExecutionResult:
    tier: int
    answer: str
    final_input_context: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    reason: str = ""
    plan: str | None = None


def _synth_user(context: str, query: str) -> str:
    if context:
        return f"CONTEXT:\n{context}\n\nQUESTION: {query}"
    return query


def synthesize(llm: LLMClient, system: str, context: str, query: str) -> LLMResponse:
    return llm.complete(system, _synth_user(context, query))


class Tier1Executor:
    def __init__(self, retriever: Retriever, llm: LLMClient):
        self.retriever, self.llm = retriever, llm

    def execute(self, query: str, plan: str | None) -> ExecutionResult:
        if plan == "greeting":
            r = synthesize(self.llm, GREETING_SYSTEM, "", query)
            return ExecutionResult(tier=1, answer=r.content, usage=r.usage, plan="greeting")
        if plan == "classification":
            r = synthesize(self.llm, CLASSIFY_SYSTEM, "", query)
            return ExecutionResult(tier=1, answer=r.content, usage=r.usage, plan="classification")
        # default + "faq": RAG-grounded, abstain-aware
        rr = self.retriever.retrieve(query)
        if rr.abstain:
            return ExecutionResult(tier=1, answer=I_DONT_KNOW, plan="faq")
        context = rr.answer or ""
        r = synthesize(self.llm, FAQ_SYSTEM, context, query)
        return ExecutionResult(tier=1, answer=r.content, final_input_context=context,
                               usage=r.usage, plan="faq")
```
*(`ExecutionResult.usage` defaults to an empty `TokenUsage()` so the abstain path — which makes no
LLM call — still returns a valid, zero-token usage.)*

**Step 4: Run → expect PASS**
Run: `pytest tests/test_orchestrator_tier1.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/orchestrator.py tests/test_orchestrator_tier1.py
git commit -m "feat(p4): Tier-1 execution (greeting / FAQ-with-RAG / classification + abstain)"
```

---

## Task 4: Tier-2 execution (LLM pipeline plan → tools → synthesis)

**Files:**
- Modify: `src/tiered_rag/orchestrator.py` (add `Tier2Plan`, `Tier2Executor`)
- Test: `tests/test_orchestrator_tier2.py`

**Design:** `Tier2Executor.execute(query)`:
1. **Plan** — call the Tier-2 LLM with `TIER2_PLAN_SYSTEM` (lists the `TOOLS` names+descriptions,
   asks for JSON `{"calls": [{"tool": "...", "args": {...}}, …]}`). Reuse the router's `_extract_json`
   to tolerate fences; validate into `Tier2Plan`. On parse failure → empty plan.
2. **Execute** — for each call, `run_tool(name, args, catalog)`; record `{tool, args, result}` in
   `tool_calls`. Unknown tool → record an `{"error": ...}` result and continue (never crash).
3. **Assemble** `final_input_context` — a readable block of the tool results.
4. **Synthesize** — one grounded synthesis call (`FAQ_SYSTEM`) over that context.
5. **Usage** — aggregate the plan call + synthesis call.

**Step 1: Write the failing tests** (`tests/test_orchestrator_tier2.py`)
```python
import json

from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import Tier2Executor

CATALOG = {"7": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
                 "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"},
           "SKU-07": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
                      "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"}}


def _planner(calls):
    """A FakeLLM that returns a plan for the plan-call and echoes context for synthesis."""
    plan_json = json.dumps({"calls": calls})

    def responder(system, user):
        return plan_json if "plan" in system.lower() else user  # synth echoes its user msg
    return FakeLLM(responder)


def test_tier2_runs_planned_tools_and_grounds_answer():
    calls = [{"tool": "get_item_details_from_xlsx", "args": {"item_id": "SKU-07"}}]
    ex = Tier2Executor(_planner(calls), CATALOG)
    res = ex.execute("give me the full details for item SKU-07")
    assert res.tier == 2
    assert res.tool_calls[0]["tool"] == "get_item_details_from_xlsx"
    assert res.tool_calls[0]["result"]["name"] == "Dragon Skin"
    assert "Dragon Skin" in res.final_input_context
    assert "Dragon Skin" in res.answer            # grounded
    assert res.usage.total_tokens > 0             # plan + synth aggregated


def test_tier2_unknown_tool_does_not_crash():
    ex = Tier2Executor(_planner([{"tool": "bogus", "args": {}}]), CATALOG)
    res = ex.execute("do something weird")
    assert "error" in res.tool_calls[0]["result"]


def test_tier2_unparseable_plan_yields_empty_plan():
    ex = Tier2Executor(FakeLLM("not json at all"), CATALOG)
    res = ex.execute("status of order #1?")
    assert res.tool_calls == []
```

**Step 2: Run → expect FAIL** (`ImportError: cannot import name 'Tier2Executor'`)
Run: `pytest tests/test_orchestrator_tier2.py -v`

**Step 3: Implement** — add to `src/tiered_rag/orchestrator.py`:
```python
import json

from pydantic import BaseModel

from .router import _extract_json
from .tools.registry import TOOLS, run_tool


def _tool_menu() -> str:
    return "\n".join(f"- {t.name}: {t.description}" for t in TOOLS.values())


TIER2_PLAN_SYSTEM = (
    "You are the Tier-2 planner. Build a pipeline plan of tool calls to answer the user.\n"
    "Available tools:\n" + _tool_menu() + "\n\n"
    'Reply with JSON only: {"calls": [{"tool": "<name>", "args": {<k>: <v>}}, ...]}. '
    "Use an empty list if no tool is needed."
)


class ToolCall(BaseModel):
    tool: str
    args: dict = {}


class Tier2Plan(BaseModel):
    calls: list[ToolCall] = []


def _format_context(tool_calls: list[dict]) -> str:
    return "\n".join(f"{c['tool']}({c['args']}) -> {json.dumps(c['result'])}" for c in tool_calls)


class Tier2Executor:
    def __init__(self, llm: LLMClient, catalog: dict):
        self.llm, self.catalog = llm, catalog

    def _plan(self, query: str) -> tuple[Tier2Plan, TokenUsage]:
        resp = self.llm.complete(TIER2_PLAN_SYSTEM, query)
        try:
            plan = Tier2Plan(**_extract_json(resp.content))
        except Exception:
            plan = Tier2Plan(calls=[])
        return plan, resp.usage

    def execute(self, query: str) -> ExecutionResult:
        plan, plan_usage = self._plan(query)
        tool_calls: list[dict] = []
        for call in plan.calls:
            try:
                result = run_tool(call.tool, call.args, self.catalog)
            except KeyError:
                result = {"error": f"unknown tool: {call.tool}"}
            except Exception as e:  # bad args, etc. — never crash the pipeline
                result = {"error": str(e)}
            tool_calls.append({"tool": call.tool, "args": call.args, "result": result})

        context = _format_context(tool_calls)
        synth = synthesize(self.llm, FAQ_SYSTEM, context, query)
        usage = TokenUsage(
            plan_usage.prompt_tokens + synth.usage.prompt_tokens,
            plan_usage.completion_tokens + synth.usage.completion_tokens,
        )
        return ExecutionResult(tier=2, answer=synth.content, final_input_context=context,
                               tool_calls=tool_calls, usage=usage)
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_orchestrator_tier2.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/orchestrator.py tests/test_orchestrator_tier2.py
git commit -m "feat(p4): Tier-2 execution (LLM pipeline plan -> tools -> grounded synthesis)"
```

---

## Task 5: Orchestrator — dispatch by tier + aggregate usage (Tier 3 stub)

**Files:**
- Modify: `src/tiered_rag/orchestrator.py` (add `Orchestrator`)
- Test: `tests/test_orchestrator.py`

**Design:** `Orchestrator` ties the router + tier executors together.
`Orchestrator(router, retriever, catalog, llm_for)` where `llm_for: Callable[[int], LLMClient]`.
`run(query)`:
1. `sel, route_usage = router.route_detailed(query)` (unpack `RouteResult`).
2. dispatch:
   - tier 1 → `Tier1Executor(retriever, llm_for(1)).execute(query, sel.plan)`
   - tier 2 → `Tier2Executor(llm_for(2), catalog).execute(query)`
   - tier 3 → stub `ExecutionResult(tier=3, answer="[stub] Tier-3 multi-step reasoning (Phase 6)")`
3. fold the **routing** usage into the result's usage (so `/chat` logs router + execution together)
   and stamp `tier/reason/plan` from the selection.

**Step 1: Write the failing tests** (`tests/test_orchestrator.py`)
```python
import json

from qdrant_client import QdrantClient

from tiered_rag.ingest import ingest
from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import Orchestrator
from tiered_rag.retrieval import Retriever
from tiered_rag.router import Router
from tiered_rag.vector_store import QdrantStore

CATALOG = {"SKU-07": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
                      "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"}}


def _retriever(fake_embedder):
    store = QdrantStore(QdrantClient(location=":memory:"), collection="kb")
    ingest([{"id": 1, "question": "how do I reset my password",
             "answer": "Open Settings > Security > Reset.", "category": "Account"}],
           store, fake_embedder)
    return Retriever(store, fake_embedder, threshold=0.6)


def _orchestrator(fake_embedder, route_tier, route_plan=None):
    router = Router(FakeLLM(json.dumps({"tier": route_tier, "reason": "x", "plan": route_plan})))
    # tier-1 LLM echoes context; tier-2 LLM returns a plan then echoes context
    def llm_for(tier):
        if tier == 2:
            def r(system, user):
                return (json.dumps({"calls": [{"tool": "get_item_details_from_xlsx",
                                               "args": {"item_id": "SKU-07"}}]})
                        if "plan" in system.lower() else user)
            return FakeLLM(r)
        return FakeLLM(lambda s, u: u)
    return Orchestrator(router, _retriever(fake_embedder), CATALOG, llm_for)


def test_orchestrator_tier1_faq(fake_embedder):
    res = _orchestrator(fake_embedder, 1, "faq").run("how do I reset my password")
    assert res.tier == 1
    assert "Open Settings > Security > Reset." in res.answer
    assert res.usage.total_tokens > 0   # routing + synthesis aggregated


def test_orchestrator_tier2(fake_embedder):
    res = _orchestrator(fake_embedder, 2).run("full details for SKU-07")
    assert res.tier == 2
    assert "Dragon Skin" in res.answer


def test_orchestrator_tier3_is_stub(fake_embedder):
    res = _orchestrator(fake_embedder, 3).run("everything is broken, escalate")
    assert res.tier == 3
    assert "stub" in res.answer.lower()
```

**Step 2: Run → expect FAIL** (`ImportError: cannot import name 'Orchestrator'`)
Run: `pytest tests/test_orchestrator.py -v`

**Step 3: Implement** — add to `src/tiered_rag/orchestrator.py`:
```python
from typing import Callable

from .router import Router


class Orchestrator:
    def __init__(self, router: Router, retriever: Retriever, catalog: dict,
                 llm_for: Callable[[int], LLMClient]):
        self.router, self.retriever, self.catalog, self.llm_for = router, retriever, catalog, llm_for

    def run(self, query: str) -> ExecutionResult:
        route = self.router.route_detailed(query)
        sel = route.selection
        if sel.tier == 2:
            res = Tier2Executor(self.llm_for(2), self.catalog).execute(query)
        elif sel.tier == 3:
            res = ExecutionResult(tier=3,
                                  answer="[stub] would run the Tier-3 multi-step chain (Phase 6)")
        else:
            res = Tier1Executor(self.retriever, self.llm_for(1)).execute(query, sel.plan)

        res.reason = sel.reason
        res.plan = res.plan if res.plan is not None else sel.plan
        res.usage = TokenUsage(
            res.usage.prompt_tokens + route.usage.prompt_tokens,
            res.usage.completion_tokens + route.usage.completion_tokens,
        )
        return res
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_orchestrator.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(p4): Orchestrator dispatch by tier + aggregate pipeline usage (T3 stub)"
```

---

## Task 6: Wire the orchestrator into `/chat` + integration test + README

**Files:**
- Modify: `src/tiered_rag/api.py` (`/chat` runs the orchestrator)
- Test: `tests/test_api.py` (extend: real answers; keep usage/`/usage` green)
- Create: `tests/test_integration_pipeline.py` (`@integration`, live mocks + real ollama/Qdrant)
- Modify: `README.md` (Phase-4 section)

**Design:** `/chat` builds an `Orchestrator` (via a `get_orchestrator` dependency that wires the
real `Router`, `Retriever`, loaded catalog, and `llm_for = lambda t: build_llm(s, t)`), calls
`run(query)`, logs the **aggregated** usage, and returns the real `answer` + `final_input_context`
length + tool-call count. Tests override `get_orchestrator` with a `FakeLLM`-backed one (mirroring
the Phase-2 `get_router` override). The Phase-3 `usage`/`/usage` contract is preserved.

**Step 1: Write the failing tests**

Rework the override helper + add cases in `tests/test_api.py`:
```python
def _client_with_orchestrator(orch):
    app = create_app()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    return TestClient(app)


def test_chat_returns_real_tier1_answer(fake_embedder):
    orch = _orchestrator(fake_embedder, 1, "faq")  # reuse the Task-5 builder (move to a shared helper)
    body = _client_with_orchestrator(orch).post(
        "/chat", json={"query": "how do I reset my password"}).json()
    assert body["tier"] == 1
    assert "Open Settings > Security > Reset." in body["answer"]
    assert body["usage"]["total_tokens"] > 0
```
*(Move the `_orchestrator`/`_retriever` builders from `test_orchestrator.py` into
`tests/data/` or a small `tests/_helpers.py` so both modules share them. Keep the existing
`/usage` test, repointing it at an orchestrator whose router returns tier 2.)*

**Step 2: Run → expect FAIL** (`ImportError: get_orchestrator`; `/chat` still returns the stub)
Run: `pytest tests/test_api.py -v`

**Step 3: Implement** — update `src/tiered_rag/api.py`:
```python
from .embeddings import OllamaEmbedder
from .knowledge_base import catalog_index, load_item_details
from .orchestrator import Orchestrator
from .retrieval import Retriever
from .vector_store import QdrantStore
# (keep: build_llm, Router, UsageLog, get_settings, time)


def get_orchestrator() -> Orchestrator:
    from qdrant_client import QdrantClient
    s = get_settings()
    router = Router(build_llm(s, 1), temperature=s.router_temperature)
    store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
    retriever = Retriever(store, OllamaEmbedder(s.ollama_host, s.embed_model), s.confidence_threshold)
    catalog = catalog_index(load_item_details(s.item_details_path))
    return Orchestrator(router, retriever, catalog, llm_for=lambda tier: build_llm(s, tier))
```
Replace the `/chat` body so it runs the orchestrator and logs aggregated usage:
```python
    @app.post("/chat", response_model=ChatResponse)
    def chat(req, orchestrator=Depends(get_orchestrator),
             usage_log=Depends(get_usage_log), settings=Depends(get_settings_dep)):
        t0 = time.perf_counter()
        res = orchestrator.run(req.query)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        rec = usage_log.record(tier=res.tier, model=settings.openai_model,
                               usage=res.usage, latency_ms=latency_ms, settings=settings)
        return ChatResponse(
            tier=res.tier, reason=res.reason, plan=res.plan, answer=res.answer,
            usage=Usage(prompt_tokens=rec.prompt_tokens, completion_tokens=rec.completion_tokens,
                        total_tokens=rec.total_tokens, cost_usd=rec.cost_usd),
        )
```
*(Drop the old `get_router` dependency from `/chat`. Keep `ChatResponse`/`Usage`/`/usage`/`/healthz`
unchanged. `get_router` may stay exported for back-compat or be removed — remove it and its import
if nothing else uses it.)*

**Step 4: Write the integration test** (`tests/test_integration_pipeline.py`, `@integration`)
```python
import httpx
import pytest
from fastapi.testclient import TestClient

from tiered_rag.api import create_app
from tiered_rag.config import get_settings

pytestmark = pytest.mark.integration


def _up(base_url):
    try:
        return httpx.get(base_url.replace("/v1", "") + "/healthz", timeout=2).status_code == 200
    except Exception:
        return False


def test_pipeline_end_to_end_via_mocks(monkeypatch):
    s = get_settings()
    if not _up(s.mock_llm_base_url):
        pytest.skip("mock tier servers not running")
    monkeypatch.setenv("LLM_TYPE", "mock")
    client = TestClient(create_app())
    body = client.post("/chat", json={"query": "give me the full details for item SKU-07"}).json()
    assert body["tier"] == 2
    assert body["usage"]["total_tokens"] > 0
```
*(This needs the mocks up + a Qdrant with the KB ingested for the FAQ path; the test above targets
the tier-2 path which only needs the mocks + the catalog xlsx. Add a tier-1 FAQ assertion guarded by
a Qdrant-up check if desired.)*

**Step 5: Run the full suite + write the README**
```bash
pytest -m "not integration" -v      # all offline (FakeLLM + in-memory Qdrant + TestClient)
# bring up mocks (Phase 3) then:
pytest -m integration -v            # pipeline + Phase-1/2/3; skips what's down
```
`README.md` — add a **Phase-4 "Tier 1 & Tier 2 Execution"** section: the tier-1 inline-plan dispatch
(greeting / FAQ-with-RAG / classification + abstain), the tier-2 LLM-planned tool pipeline (the four
tools + `item_details.xlsx`), `final_input_context` assembly + grounded synthesis, the aggregated
per-request usage, and an example `/chat` response now carrying a **real** answer.

**Step 6: Commit**
```bash
git add src/tiered_rag/api.py tests/test_api.py tests/test_integration_pipeline.py README.md \
        tests/_helpers.py  # if added
git commit -m "feat(p4): wire orchestrator into /chat (real T1/T2 answers) + integration + README"
```

---

## Phase 4 Definition of Done

- [ ] `pytest -m "not integration"` → all green, fully offline (FakeLLM + in-memory Qdrant + TestClient).
- [ ] `xlsx/item_details.xlsx` exists (committed) and `load_item_details`/`catalog_index` read it.
- [ ] Four Tier-2 tools work behind a `TOOLS` registry: `check_order_status`, `check_item_price`,
      `check_account_tier` (function calling) + `get_item_details_from_xlsx` (structured extraction).
- [ ] Router emits a tier-1 inline plan (`greeting`/`faq`/`classification`); Phase-2 routing eval stays green.
- [ ] Tier-1 execution: greeting (no RAG), FAQ (RAG-grounded synthesis, **abstains** below threshold
      with no LLM call), classification (label).
- [ ] Tier-2 execution: Tier-2 LLM builds a tool-call plan → tools run → `final_input_context` →
      grounded synthesis; unknown tool / unparseable plan never crash.
- [ ] `/chat` returns a **real** answer (Tier 1/2) with **aggregated** token usage; `/usage` still
      reports running totals; Tier 3 still stubbed.
- [ ] `pytest -m integration` → full pipeline runs against the live mock servers (skips if down).
- [ ] README Phase-4 section written. All work committed.

**Next:** write `FIFTH_PHASE_PLAN.md` (Zero-Hallucination Guardrails) — a **verifier agent** that
checks the synthesized answer against `final_input_context`/retrieved sources and rejects unsupported
claims, plus **knowledge-gap alerting** (async mock webhook/log + a "Pending Human Specialist Review"
reply) when the answer can't be grounded. Phase 4's `ExecutionResult.final_input_context` +
`tool_calls` are exactly the evidence the verifier consumes, and the abstain path is the first gap
signal to escalate.
