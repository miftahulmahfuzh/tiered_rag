# Phase 6 — Tier-3 Multi-Step Reasoning — Implementation Plan

> **✅ STATUS: COMPLETE (2026-05-29).** All four tasks landed on `main` via TDD (RED → GREEN → commit):
> `db7a6f0` (schema + prompts + config) · `ce02baf` (`Tier3Executor`) · `3e8de77` (orchestrator wiring +
> guardrail) · `0527517` (Tier-3 mock + integration test + README). **96 passed, 1 skipped** with the
> mock servers up (the skip is the pre-existing Phase-1 RAG test needing a live Qdrant collection — not a
> Phase-6 regression); **91 passed** offline. Every DoD box below is checked. See the README "Phase 6 —
> Tier-3 Multi-Step Reasoning" section for the live `/chat` example. **Next plan: `SEVENTH_PHASE_PLAN.md`.**

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to
> implement this plan task-by-task. Use superpowers-extended-cc:test-driven-development
> for every task (RED → GREEN → COMMIT).

**Goal:** Replace the Tier-3 **stub** with a real **multi-step reasoning chain**. Per the locked
architecture (`MAJOR_PHASES.md` §2 + taxonomy #6), Tier 3 handles **super-complex, multi-step
troubleshooting and sensitive complaints**: the Tier-3 LLM generates a **chained plan**
(step1 → step2 → step3, each step consuming the prior step's output), the executor runs the chain
**sequentially with context threading**, assembles the accumulated trace into `final_input_context`,
and produces a final **grounded** synthesis. That synthesis **flows through the exact same Phase-5
guardrail** (verifier + knowledge-gap escalation) with **zero new orchestrator branching** — because
the guardrail already fires on any result whose `final_input_context` is non-empty.

- A chain **step** is one of: a **tool call** (reuses the Phase-4 `TOOLS` registry — `check_order_status`,
  `check_item_price`, `check_account_tier`, `get_item_details_from_xlsx`), a **`retrieve`** step
  (grounds the chain in the real KB via the Phase-1 `Retriever`), or a **reasoning** step (an LLM call
  that consumes the running transcript and threads its output forward).
- The chain is **bounded** (`tier3_max_steps`, default 5) so a runaway plan can't blow up cost/latency.
- The final answer is synthesized with the Phase-4 grounded prompt (`FAQ_SYSTEM`) over the **whole
  transcript**, so the verifier checks the answer against the chain's accumulated evidence.

RAG stays real; the LLM stays feature-flagged (`mock`/`openai`). Everything new is offline-testable with
`FakeLLM` + in-memory Qdrant + `FakeEmbedder`; the Tier-3 mock server learns a `TIER3_PLAN_MARKER`
(mirroring the Phase-3 `ROUTER_MARKER` and Phase-5 `VERIFIER_MARKER`) so the live-mock pipeline returns a
deterministic chain plan and stays coherent end-to-end.

**Architecture (what Phase 6 adds — `Tier3Executor`, slotting into the existing `Orchestrator.run`):**

```
Router.route_detailed(query) ── TierSelection{tier=3, reason, plan=null} + usage
   │   (router already routes multi-step / sensitive complaints to tier 3 — no router change)
   ▼
Tier3Executor.execute(query)
   │  1. PLAN     Tier-3 LLM → {"steps":[{instruction, tool?, args?}, …]}   (parsed, degrade-to-empty)
   │  2. EXECUTE  for each step (capped at tier3_max_steps), threading the running transcript:
   │                 tool != null & known → run_tool(tool, args, catalog)         → record + append
   │                 tool == "retrieve"   → Retriever.retrieve(args.query|query)  → record + append
   │                 tool == null         → LLM reasoning over PRIOR STEPS + instruction → append
   │                 (output of step N is in the transcript fed to step N+1)
   │  3. ASSEMBLE final_input_context = the full "[step k] … -> …" transcript
   │  4. SYNTH    synthesize(llm, FAQ_SYSTEM, context, query)   (grounded in the transcript)
   │  usage := plan + every reasoning step + synth   (tool/retrieve steps cost 0 LLM tokens)
   ▼
ExecutionResult{tier=3, answer, final_input_context, tool_calls, usage}
   │
   ▼  ┌──────── GUARDRAIL (Phase 5, UNCHANGED) ────────┐
   │  │ final_input_context non-empty + verifier set?  │
   │  │   verdict = Verifier.verify(query, answer, ctx) │
   │  │   supported → keep ; NOT supported → escalate   │
   │  └─────────────────────────────────────────────────┘
   ▼  /chat → aggregated usage + async knowledge-gap alert + verified/pending_review (all Phase-5)
```

**Tech Stack:** builds on Phase 1 (`Retriever` / `RetrievalResult`), Phase 3 (`LLMClient`,
`TokenUsage`, mock-marker pattern), Phase 4 (`ExecutionResult`, `synthesize`, `FAQ_SYSTEM`,
`_extract_json`, `TOOLS` / `run_tool`, the `Tier2Executor` plan→dispatch→synth loop the chain
generalises), Phase 5 (the guardrail the chain's output flows through). **No new runtime deps.**
Offline tests use `FakeLLM` + in-memory Qdrant + `FakeEmbedder` + `TestClient`; one `@integration` test
routes a multi-step query through the live mock tier servers (skips if down).

**Key design decisions (locked for this phase):**

- **The chain executor generalises `Tier2Executor`.** Same shape — LLM **plans**, executor **runs**
  steps via the shared `TOOLS` registry with the same never-crash error handling (`_extract_json`,
  degrade-to-empty plan, `{"error": …}` per failed call), then a single grounded **synthesis**.
  The only new ideas are **sequential context threading** (step N's output is in the prompt for step
  N+1) and **reasoning steps** (LLM steps with no tool).
- **Zero new guardrail wiring.** `Orchestrator._guardrail` already verifies any result with a non-empty
  `final_input_context`. Because `Tier3Executor` populates `final_input_context` with the transcript,
  the Phase-5 verifier + knowledge-gap escalation apply to Tier 3 **automatically**. The orchestrator
  change is a one-line swap of the stub for `Tier3Executor(...).execute(query)`.
- **Grounding the chain.** Reasoning steps are *not* source-grounded on their own; the **final synthesis
  is grounded in the transcript** (`FAQ_SYSTEM` answers only from CONTEXT), and the verifier bounds the
  answer to that transcript. For genuinely zero-hallucination Tier-3, plans should include at least one
  **`retrieve`** or **tool** step that injects real data — these are first-class step kinds precisely so
  the chain has real evidence to reason over, not just LLM free-text.
- **Bounded chains.** `tier3_max_steps` (default 5, from `Settings` — never hardcoded) caps execution.
  A plan longer than the cap is silently truncated to the first N steps; the executor `log`s nothing but
  the truncation is observable (fewer `[step k]` lines / `tool_calls` than the plan requested).
- **Deterministic tool/retrieve steps cost 0 LLM tokens.** Only reasoning steps and the final synthesis
  consume tokens, so the aggregated `ExecutionResult.usage` (which folds into the Phase-7 cost math via
  `/chat`) reflects the *true* per-request cost of the chain plus the guardrail call.
- **Mock stays coherent.** The Tier-3 mock learns a `TIER3_PLAN_MARKER` (a substring of
  `TIER3_PLAN_SYSTEM`, pinned by a guard test exactly like `ROUTER_MARKER` / `VERIFIER_MARKER`) and
  returns a deterministic **reasoning-only** 2-step chain plan; its step/synth calls return the canned
  `"[mock tier-3] …"` answer. Combined with the Phase-5 verifier-aware Tier-1 mock (returns *supported*),
  the live-mock pipeline runs a real Tier-3 chain end-to-end and never spuriously escalates. The chain is
  *meaningfully* exercised on the `LLM_TYPE=openai` path and in the offline tests (which inject crafted
  plans + step outputs directly).

**New/changed files at a glance:**

| File | Change |
|---|---|
| `src/tiered_rag/config.py` | + `tier3_max_steps` |
| `src/tiered_rag/orchestrator.py` | **new** `ChainStep`, `Tier3Plan`, `TIER3_PLAN_SYSTEM`, `TIER3_PLAN_MARKER`, `TIER3_STEP_SYSTEM`, `Tier3Executor`; `Orchestrator` gains `tier3_max_steps` + runs the chain (replaces the stub) |
| `src/tiered_rag/mock_llm.py` | Tier-3 mock recognises `TIER3_PLAN_MARKER` → deterministic chain plan |
| `src/tiered_rag/api.py` | `get_orchestrator` passes `s.tier3_max_steps` |
| `tests/_helpers.py` | `build_orchestrator`'s `llm_for(3)` returns a chain-plan `FakeLLM` |
| `tests/test_orchestrator_tier3.py` | **new** — chain threading / tool step / retrieve step / cap / degrade |
| `tests/test_orchestrator.py` | replace `test_orchestrator_tier3_is_stub` with a real Tier-3 assertion |
| `tests/test_orchestrator_guardrail.py` | + Tier-3-flows-through-the-guardrail test |
| `tests/test_mock_llm.py` | + `TIER3_PLAN_MARKER` guard + plan-shape test |
| `tests/test_integration_pipeline.py` | + live-mock Tier-3 chain test |
| `tests/test_config.py` | + `tier3_max_steps` default |
| `README.md` | Phase-6 section |

---

## Task 0: Chain plan schema + Tier-3 prompts + config

**Files:**
- Modify: `src/tiered_rag/orchestrator.py` (add `ChainStep`, `Tier3Plan`, the three Tier-3 prompt
  constants; **no executor yet**)
- Modify: `src/tiered_rag/config.py` (add `tier3_max_steps`)
- Test: `tests/test_orchestrator_tier3_plan.py` (**new** — schema + marker guard)
- Test: `tests/test_config.py` (extend)

**Design:** mirror the Phase-4 `ToolCall` / `Tier2Plan` pair. A `ChainStep` carries an `instruction`
(for reasoning steps), an optional `tool` name, and `args`. `Tier3Plan` is a list of steps. The
`TIER3_PLAN_SYSTEM` prompt instructs the model to decompose the query into ordered steps and reply with
JSON only; `TIER3_PLAN_MARKER` is a stable substring the Phase-3 mock keys on. `TIER3_STEP_SYSTEM` is the
per-step reasoning prompt. `tier3_max_steps` bounds the chain.

**Step 1: Write the failing tests** (`tests/test_orchestrator_tier3_plan.py`)
```python
from tiered_rag.orchestrator import (
    TIER3_PLAN_MARKER,
    TIER3_PLAN_SYSTEM,
    ChainStep,
    Tier3Plan,
)


def test_marker_is_substring_of_plan_prompt():
    assert TIER3_PLAN_MARKER in TIER3_PLAN_SYSTEM


def test_chain_step_defaults():
    s = ChainStep()
    assert s.instruction == "" and s.tool is None and s.args == {}


def test_tier3_plan_parses_mixed_steps():
    plan = Tier3Plan(**{"steps": [
        {"instruction": "look up the order", "tool": "check_order_status", "args": {"order_id": "1"}},
        {"instruction": "explain the status to the user"},
    ]})
    assert len(plan.steps) == 2
    assert plan.steps[0].tool == "check_order_status"
    assert plan.steps[1].tool is None
```

Append to `tests/test_config.py`:
```python
def test_phase6_tier3_max_steps_default():
    s = Settings()
    assert s.tier3_max_steps == 5
    assert s.tier3_max_steps > 0
```

**Step 2: Run → expect FAIL** (`ImportError`: no `ChainStep`/`Tier3Plan`/`TIER3_PLAN_*`; missing setting)
Run: `pytest tests/test_orchestrator_tier3_plan.py tests/test_config.py -v`

**Step 3: Implement**

Add to `Settings` (after the Phase-5 guardrail block):
```python
    # --- Tier-3 multi-step reasoning (Phase 6) ---
    tier3_max_steps: int = 5               # bound the chain length (cost/latency guard)
```

In `src/tiered_rag/orchestrator.py`, add the Tier-3 schema + prompts (next to the Tier-2 ones).
Reuse the existing `_tool_menu()` helper for the plan prompt:
```python
TIER3_PLAN_MARKER = "Tier-3 planner"

TIER3_PLAN_SYSTEM = (
    "You are the Tier-3 planner for complex, multi-step support cases.\n"
    "Decompose the user's request into an ORDERED chain of steps; each step may use a tool, "
    "retrieve from the knowledge base, or reason over the previous steps' output.\n"
    "Available tools:\n" + _tool_menu() + "\n"
    '- retrieve: search the knowledge base; args {"query": "<text>"}.\n\n'
    'Reply with JSON only (no prose, no markdown fence): '
    '{"steps": [{"instruction": "<what this step does>", '
    '"tool": <"<tool name>"|"retrieve"|null>, "args": {<k>: <v>}}, ...]}. '
    "Use tool=null for a pure reasoning step. Keep the chain short and ordered."
)

TIER3_STEP_SYSTEM = (
    "You are executing ONE step of a Tier-3 reasoning chain. Use the PRIOR STEPS as context and "
    "perform only the current step. Be concise and do not invent facts beyond the prior context."
)


class ChainStep(BaseModel):
    instruction: str = ""
    tool: str | None = None
    args: dict = {}


class Tier3Plan(BaseModel):
    steps: list[ChainStep] = []
```
*(These reference `_tool_menu`, `BaseModel` — both already imported/defined in `orchestrator.py`. Place
the constants after `TIER2_PLAN_SYSTEM` and the models after `Tier2Plan` so `_tool_menu` is defined.)*

**Step 4: Run → expect PASS**
Run: `pytest tests/test_orchestrator_tier3_plan.py tests/test_config.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/orchestrator.py src/tiered_rag/config.py \
        tests/test_orchestrator_tier3_plan.py tests/test_config.py
git commit -m "feat(p6): Tier-3 chain plan schema + planner prompts + tier3_max_steps config"
```

---

## Task 1: `Tier3Executor` — sequential chain with context threading

**Files:**
- Modify: `src/tiered_rag/orchestrator.py` (add `Tier3Executor`)
- Test: `tests/test_orchestrator_tier3.py` (**new**)

**Design:** `Tier3Executor(llm, retriever, catalog, max_steps=5)`.
1. `_plan(query)` → `(Tier3Plan, TokenUsage)` — one LLM call with `TIER3_PLAN_SYSTEM`, parsed with
   `_extract_json`, degrade-to-empty on failure (identical pattern to `Tier2Executor._plan`).
2. `execute(query)` runs `plan.steps[:max_steps]` **in order**, threading a `transcript` list:
   - **tool step** (`tool` set, not `retrieve`) → `run_tool` with the same try/except as Tier-2; append
     `"[step k] tool(args) -> result"`; record a `tool_calls` entry; **0 LLM tokens**.
   - **retrieve step** (`tool == "retrieve"`) → `Retriever.retrieve(args["query"] or query)`; append
     `"[step k] retrieve -> {answer, abstain, score}"`; record a `tool_calls` entry; **0 LLM tokens**.
   - **reasoning step** (`tool is None`) → LLM call with `TIER3_STEP_SYSTEM` over
     `"PRIOR STEPS:\n{transcript}\n\nNOW DO: {instruction}"`; append `"[step k] instruction -> output"`;
     fold its usage.
   - `final_input_context = "\n".join(transcript)`; final `synthesize(llm, FAQ_SYSTEM, context, query)`;
     fold synth usage. Return `ExecutionResult(tier=3, answer=synth.content,
     final_input_context=context, tool_calls=tool_calls, usage=usage)`.

**Step 1: Write the failing tests** (`tests/test_orchestrator_tier3.py`)
```python
import json

from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import (
    TIER3_PLAN_MARKER,
    Tier3Executor,
)

from tests._helpers import CATALOG, build_retriever


def _chain_llm(steps):
    """FakeLLM: returns the given chain plan on the plan call; echoes its user msg otherwise
    (so reasoning-step + synth outputs are inspectable and prove context threading)."""
    plan_json = json.dumps({"steps": steps})

    def responder(system, user):
        return plan_json if TIER3_PLAN_MARKER in system else user
    return FakeLLM(responder)


def test_tier3_threads_context_and_grounds_answer():
    steps = [{"instruction": "assess the issue", "tool": None, "args": {}},
             {"instruction": "recommend next steps", "tool": None, "args": {}}]
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG).execute("everything broke")
    assert res.tier == 3
    assert "[step 1]" in res.final_input_context and "[step 2]" in res.final_input_context
    # step 2's reasoning output (echoed) embeds step 1's transcript line -> context threaded forward
    assert res.final_input_context.count("[step 1] assess the issue") >= 2
    assert res.usage.total_tokens > 0                      # plan + 2 steps + synth aggregated


def test_tier3_tool_step_uses_registry():
    steps = [{"instruction": "look up the item", "tool": "get_item_details_from_xlsx",
              "args": {"item_id": "SKU-07"}}]
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG).execute("details for SKU-07")
    assert res.tool_calls[0]["tool"] == "get_item_details_from_xlsx"
    assert res.tool_calls[0]["result"]["name"] == "Dragon Skin"
    assert "Dragon Skin" in res.final_input_context        # tool result threaded into context


def test_tier3_retrieve_step_grounds_in_kb(fake_embedder):
    steps = [{"instruction": "find the policy", "tool": "retrieve",
              "args": {"query": "how do I reset my password"}}]
    res = Tier3Executor(_chain_llm(steps), build_retriever(fake_embedder), CATALOG).execute("reset help")
    assert res.tool_calls[0]["tool"] == "retrieve"
    assert "Open Settings > Security > Reset." in res.final_input_context


def test_tier3_unparseable_plan_degrades_without_crashing():
    res = Tier3Executor(FakeLLM("not json at all"), retriever=None, catalog=CATALOG).execute("huh")
    assert res.tier == 3
    assert res.tool_calls == []                            # empty plan -> no steps
    assert res.final_input_context == ""                   # nothing to ground on


def test_tier3_caps_chain_at_max_steps():
    steps = [{"tool": "get_item_details_from_xlsx", "args": {"item_id": "SKU-07"}}] * 4
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG, max_steps=2).execute("x")
    assert len(res.tool_calls) == 2                        # truncated to the first 2 steps


def test_tier3_unknown_tool_does_not_crash():
    steps = [{"instruction": "do a weird thing", "tool": "bogus", "args": {}}]
    res = Tier3Executor(_chain_llm(steps), retriever=None, catalog=CATALOG).execute("weird")
    assert "error" in res.tool_calls[0]["result"]
```

**Step 2: Run → expect FAIL** (`ImportError: Tier3Executor`)
Run: `pytest tests/test_orchestrator_tier3.py -v`

**Step 3: Implement** — add `Tier3Executor` to `src/tiered_rag/orchestrator.py` (after `Tier2Executor`):
```python
class Tier3Executor:
    """Sequential multi-step reasoning chain (each step's output threads into the next)."""

    def __init__(self, llm: LLMClient, retriever: Retriever | None, catalog: dict, max_steps: int = 5):
        self.llm, self.retriever, self.catalog, self.max_steps = llm, retriever, catalog, max_steps

    def _plan(self, query: str) -> tuple[Tier3Plan, TokenUsage]:
        resp = self.llm.complete(TIER3_PLAN_SYSTEM, query)
        try:
            plan = Tier3Plan(**_extract_json(resp.content))
        except Exception:
            plan = Tier3Plan(steps=[])
        return plan, resp.usage

    def _run_step(self, i: int, step: ChainStep, query: str,
                  transcript: list[str]) -> tuple[str, dict | None, TokenUsage]:
        if step.tool == "retrieve":
            if self.retriever is None:
                result = {"error": "no retriever available"}
            else:
                rr = self.retriever.retrieve(step.args.get("query") or query)
                result = {"answer": rr.answer, "abstain": rr.abstain, "score": round(rr.score, 3)}
            call = {"step": i, "tool": "retrieve", "args": step.args, "result": result}
            return f"[step {i}] retrieve -> {json.dumps(result)}", call, TokenUsage()
        if step.tool:
            try:
                result = run_tool(step.tool, step.args, self.catalog)
            except KeyError:
                result = {"error": f"unknown tool: {step.tool}"}
            except Exception as e:  # bad args, etc. — never crash the chain
                result = {"error": str(e)}
            call = {"step": i, "tool": step.tool, "args": step.args, "result": result}
            return f"[step {i}] {step.tool}({step.args}) -> {json.dumps(result)}", call, TokenUsage()
        # reasoning step: thread the running transcript forward
        prior = "\n".join(transcript)
        user = f"PRIOR STEPS:\n{prior}\n\nNOW DO: {step.instruction}" if prior else step.instruction
        r = self.llm.complete(TIER3_STEP_SYSTEM, user)
        return f"[step {i}] {step.instruction} -> {r.content}", None, r.usage

    def execute(self, query: str) -> ExecutionResult:
        plan, usage = self._plan(query)
        transcript: list[str] = []
        tool_calls: list[dict] = []
        for i, step in enumerate(plan.steps[: self.max_steps], start=1):
            line, call, step_usage = self._run_step(i, step, query, transcript)
            transcript.append(line)
            if call is not None:
                tool_calls.append(call)
            usage = TokenUsage(usage.prompt_tokens + step_usage.prompt_tokens,
                               usage.completion_tokens + step_usage.completion_tokens)
        context = "\n".join(transcript)
        synth = synthesize(self.llm, FAQ_SYSTEM, context, query)
        usage = TokenUsage(usage.prompt_tokens + synth.usage.prompt_tokens,
                           usage.completion_tokens + synth.usage.completion_tokens)
        return ExecutionResult(tier=3, answer=synth.content, final_input_context=context,
                               tool_calls=tool_calls, usage=usage)
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_orchestrator_tier3.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/orchestrator.py tests/test_orchestrator_tier3.py
git commit -m "feat(p6): Tier-3 chain executor (sequential steps + context threading + grounded synth)"
```

---

## Task 2: Wire `Tier3Executor` into `Orchestrator.run` (replaces the stub) + guardrail flow

**Files:**
- Modify: `src/tiered_rag/orchestrator.py` (`Orchestrator` gains `tier3_max_steps`; tier-3 branch runs
  the chain instead of the stub)
- Modify: `src/tiered_rag/api.py` (`get_orchestrator` passes `s.tier3_max_steps`)
- Modify: `tests/_helpers.py` (`build_orchestrator`'s `llm_for(3)` returns a chain-plan `FakeLLM`)
- Modify: `tests/test_orchestrator.py` (replace `test_orchestrator_tier3_is_stub`)
- Test: `tests/test_orchestrator_guardrail.py` (add: Tier-3 flows through the guardrail)

**Design:** swap the stub for `Tier3Executor(self.llm_for(3), self.retriever, self.catalog,
self.tier3_max_steps).execute(query)`. `Orchestrator.__init__` gains `tier3_max_steps: int = 5`
(additive default → Phase-4/5 orchestrators built without it stay green). The Phase-5 `_guardrail` is
**unchanged**: the chain's non-empty `final_input_context` makes the verifier run on Tier 3 for free.

**Step 1: Write / change the failing tests**

In `tests/_helpers.py`, extend `llm_for` inside `build_orchestrator` to drive a Tier-3 chain
(reasoning-only plan so no tool args are needed; step + synth echo their input):
```python
    def llm_for(tier):
        if tier == 2:
            def r(system, user):
                return (json.dumps({"calls": [{"tool": "get_item_details_from_xlsx",
                                               "args": {"item_id": "SKU-07"}}]})
                        if "plan" in system.lower() else user)
            return FakeLLM(r)
        if tier == 3:
            plan = json.dumps({"steps": [
                {"instruction": "assess the issue", "tool": None, "args": {}},
                {"instruction": "recommend next steps", "tool": None, "args": {}}]})

            def r3(system, user):
                return plan if "planner" in system.lower() else user
            return FakeLLM(r3)
        return FakeLLM(lambda s, u: u)
```

Replace `test_orchestrator_tier3_is_stub` in `tests/test_orchestrator.py` with:
```python
def test_orchestrator_tier3_runs_real_chain(fake_embedder):
    res = build_orchestrator(fake_embedder, 3).run("I was double-charged and got locked out")
    assert res.tier == 3
    assert "stub" not in res.answer.lower()
    assert "[step 1]" in res.final_input_context and "[step 2]" in res.final_input_context
    assert res.usage.total_tokens > 0          # routing + plan + steps + synth aggregated
```

Add to `tests/test_orchestrator_guardrail.py` (proves Tier 3 flows through the Phase-5 guardrail):
```python
def test_tier3_unverified_is_escalated(fake_embedder):
    res = build_orchestrator(fake_embedder, 3, verifier=REJECT).run("complex sensitive complaint")
    assert res.answer == PENDING_REVIEW
    assert res.verified is False
    assert res.gap is not None and res.gap.kind == "unverified"
```

**Step 2: Run → expect FAIL** (old stub test gone/failing; `Orchestrator` still returns the stub for
tier 3, so the new tests fail on `final_input_context` / escalation)
Run: `pytest tests/test_orchestrator.py tests/test_orchestrator_guardrail.py -v`

**Step 3: Implement**

In `src/tiered_rag/orchestrator.py`, update `Orchestrator`:
```python
class Orchestrator:
    def __init__(self, router: Router, retriever: Retriever, catalog: dict,
                 llm_for: Callable[[int], LLMClient], verifier: Verifier | None = None,
                 tier3_max_steps: int = 5):
        self.router, self.retriever, self.catalog = router, retriever, catalog
        self.llm_for, self.verifier, self.tier3_max_steps = llm_for, verifier, tier3_max_steps
```
and in `run()`, replace the tier-3 stub branch:
```python
        elif sel.tier == 3:
            res = Tier3Executor(self.llm_for(3), self.retriever, self.catalog,
                                self.tier3_max_steps).execute(query)
```

In `src/tiered_rag/api.py`, pass the setting through `get_orchestrator`:
```python
    return Orchestrator(router, retriever, catalog,
                        llm_for=lambda tier: build_llm(s, tier), verifier=verifier,
                        tier3_max_steps=s.tier3_max_steps)
```

**Step 4: Run → expect PASS** (new tier-3 + guardrail tests **and** every untouched Phase-4/5 suite)
```bash
pytest tests/test_orchestrator.py tests/test_orchestrator_tier3.py \
       tests/test_orchestrator_guardrail.py tests/test_api.py -v
```

**Step 5: Commit**
```bash
git add src/tiered_rag/orchestrator.py src/tiered_rag/api.py tests/_helpers.py \
        tests/test_orchestrator.py tests/test_orchestrator_guardrail.py
git commit -m "feat(p6): orchestrator runs the Tier-3 chain (replaces stub) + flows through guardrail"
```

---

## Task 3: Tier-3-aware mock + integration test + README

**Files:**
- Modify: `src/tiered_rag/mock_llm.py` (recognise `TIER3_PLAN_MARKER`)
- Test: `tests/test_mock_llm.py` (extend: marker guard + plan-shape)
- Test: `tests/test_integration_pipeline.py` (extend: live-mock Tier-3 chain)
- Modify: `README.md` (Phase-6 section)

**Design:** the Tier-1 mock already returns routing JSON (`ROUTER_MARKER`) and a supported verdict
(`VERIFIER_MARKER`). Teach the mock to return a deterministic **reasoning-only 2-step chain plan** when
it sees `TIER3_PLAN_MARKER`, so the live Tier-3 server drives a real chain whose step/synth calls fall
through to the canned `"[mock tier-3] …"` answer. A guard test pins `TIER3_PLAN_MARKER` to the real
`TIER3_PLAN_SYSTEM` (import lazily, mirroring the `VERIFIER_MARKER` handling, to keep the hot path clean).

**Step 1: Write the failing tests** — append to `tests/test_mock_llm.py`:
```python
def test_tier3_plan_marker_present_in_real_prompt():
    from tiered_rag.orchestrator import TIER3_PLAN_MARKER, TIER3_PLAN_SYSTEM
    assert TIER3_PLAN_MARKER in TIER3_PLAN_SYSTEM


def test_tier3_mock_returns_chain_plan_for_planner_prompt():
    import json

    from tiered_rag.orchestrator import TIER3_PLAN_SYSTEM
    resp = _post(TestClient(create_mock_app(3)), TIER3_PLAN_SYSTEM, "double charged + locked out")
    plan = json.loads(resp.json()["choices"][0]["message"]["content"])
    assert isinstance(plan["steps"], list) and len(plan["steps"]) >= 1
    assert "instruction" in plan["steps"][0]
```

**Step 2: Run → expect FAIL** (mock returns the canned tier-3 string, not a plan)
Run: `pytest tests/test_mock_llm.py -v`

**Step 3: Implement** — in `src/tiered_rag/mock_llm.py`, extend `_reply` (lazy import to avoid importing
the whole orchestrator at server start; placed after the verifier branch):
```python
def _reply(tier: int, system: str, user: str) -> str:
    if ROUTER_MARKER in system:
        chosen = _classify(user)
        return json.dumps({"tier": chosen, "reason": f"mock tier-{chosen} (deterministic)", "plan": None})
    from .verifier import VERIFIER_MARKER
    if VERIFIER_MARKER in system:
        return json.dumps({"supported": True, "reason": "mock verifier (deterministic)"})
    from .orchestrator import TIER3_PLAN_MARKER
    if TIER3_PLAN_MARKER in system:
        return json.dumps({"steps": [
            {"instruction": "assess the complaint and its sub-issues", "tool": None, "args": {}},
            {"instruction": "recommend concrete next steps", "tool": None, "args": {}}]})
    return f"[mock tier-{tier}] deterministic answer for: {user[:80]}"
```

**Step 4: Extend the integration test** (`tests/test_integration_pipeline.py`) — a multi-step query
routes to Tier 3 and runs the chain end-to-end through the live mocks:
```python
def test_pipeline_tier3_chain_via_mocks(monkeypatch):
    s = get_settings()
    if not _up(s.mock_llm_base_url):
        pytest.skip("mock tier servers not running")
    monkeypatch.setenv("LLM_TYPE", "mock")
    client = TestClient(create_app())
    body = client.post(
        "/chat",
        json={"query": "I was double-charged, the refund failed, and now I'm locked out"},
    ).json()
    assert body["tier"] == 3
    assert body["usage"]["total_tokens"] > 0
    # reasoning-only chain -> the Tier-1 mock verifier returns supported -> not escalated
    assert body["pending_review"] is False
    assert "[mock tier-3]" in body["answer"]
```

**Step 5: Run the full suite + write the README**
```bash
pytest -m "not integration" -v      # all offline (FakeLLM + in-memory Qdrant + TestClient)
# bring up the three mock tier servers (Phase 3), then:
pytest -m integration -v            # pipeline + Phase-1/2/3; skips what's down
```
`README.md` — add a **Phase-6 "Tier-3 Multi-Step Reasoning"** section: the chained plan
(plan → ordered steps → context threading → grounded synthesis), the three step kinds (tool / retrieve
/ reasoning), the `tier3_max_steps` bound, how the chain's `final_input_context` makes it **flow through
the Phase-5 guardrail automatically** (verifier + escalation), the deterministic Tier-3 mock plan, and an
example `/chat` Tier-3 response showing the multi-step answer + aggregated usage.

**Step 6: Commit**
```bash
git add src/tiered_rag/mock_llm.py tests/test_mock_llm.py \
        tests/test_integration_pipeline.py README.md
git commit -m "feat(p6): Tier-3-aware mock + chain integration test + README"
```

---

## Phase 6 Definition of Done

- [x] `pytest -m "not integration"` → all green, fully offline (FakeLLM + in-memory Qdrant + TestClient).
      **91 passed.**
- [x] **`Tier3Executor`** generates/consumes a chained plan: ordered steps with **context threading**
      (step N's output is in step N+1's prompt), supporting **tool**, **retrieve**, and **reasoning**
      steps; bounded by `tier3_max_steps`; degrade-to-empty + never-crash on bad plans/tools.
      **Covered by `tests/test_orchestrator_tier3.py` (6 tests).**
- [x] Final synthesis is **grounded in the transcript** (`FAQ_SYSTEM` over `final_input_context`); chain
      usage (plan + reasoning steps + synth) **folds into** `ExecutionResult.usage`.
- [x] `Orchestrator.run` runs the chain for tier 3 (**stub removed**); the **Phase-5 guardrail applies
      to Tier 3 unchanged** — an unsupported chain answer escalates to "Pending Human Specialist Review".
      **`test_tier3_unverified_is_escalated` proves the escalation path.**
- [x] `/chat` returns real Tier-3 answers with aggregated usage + `verified`/`pending_review`; all
      Phase-1–5 tests stay green (the old `test_orchestrator_tier3_is_stub` is replaced, not skipped —
      now `test_orchestrator_tier3_runs_real_chain`).
- [x] Tier-3 mock recognises `TIER3_PLAN_MARKER` (guard test in sync with `TIER3_PLAN_SYSTEM`);
      `pytest -m integration` runs the live-mock Tier-3 chain (`test_pipeline_tier3_chain_via_mocks`,
      passed with mocks up; skips if down).
- [x] README Phase-6 section written. All work committed.

**Next:** write `SEVENTH_PHASE_PLAN.md` (High-Scale Engineering) — **Redis semantic caching** (embed the
query, look up a near-duplicate in a Qdrant/Redis cache, serve the cached response on a hit), **health
checks + failover** across the mock tier workers (detect a down instance, fail over to a healthy one),
the full **token/cost/latency/staging-efficiency** observability rollup with the headline
**cost-savings calc** (Tier-1/2 routing vs all-Tier-3 — the per-request `ExecutionResult.usage` and the
`/usage` log built across Phases 3–6 are exactly the inputs), and a **load test** (100+ concurrent users)
against the deterministic mock backend. Phases 4–6 now produce *real* per-tier answers and costs, so the
caching hit-rate and the routing cost-savings are finally measurable on live traffic.
