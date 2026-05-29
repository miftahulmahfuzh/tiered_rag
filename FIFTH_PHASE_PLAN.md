# Phase 5 — Zero-Hallucination Guardrails — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:executing-plans to
> implement this plan task-by-task. Use superpowers-extended-cc:test-driven-development
> for every task (RED → GREEN → COMMIT).

**Goal:** Make the chatbot **provably refuse to hallucinate**. Phase 4 produces a real answer plus
the exact evidence it was built from (`ExecutionResult.final_input_context` + `tool_calls`). Phase 5
adds a **guardrail stage** between synthesis and the user:

- **Verifier agent** — a cheap LLM that checks the synthesized answer against its evidence and
  rejects it if any claim is **not supported** by the sources. Rejected answers never reach the user.
- **Knowledge-gap alerting** — when the system **cannot answer safely** (retrieval *abstained*, or the
  verifier *rejected* the answer), it fires an **async alert** (structured log line + optional
  best-effort webhook) so a human can close the gap, and — for the rejection case — replies with the
  canonical **"Pending Human Specialist Review"** message instead of an unverified answer.

RAG stays real; the LLM stays feature-flagged (`mock`/`openai`). Everything new is offline-testable
with `FakeLLM` + in-memory Qdrant + `FakeEmbedder`; the mock tier-1 server is taught to recognise the
verifier prompt (mirroring the Phase-3 `ROUTER_MARKER`) so the live-mock pipeline stays coherent.

**Architecture (what Phase 5 adds — the guardrail stage inside `Orchestrator.run`):**

```
Router.route_detailed(query) ── TierSelection + usage
   │
   ▼
<tier executor>  ──►  ExecutionResult{answer, final_input_context, tool_calls, usage, abstained}
   │
   ▼  ┌──────────────────────── GUARDRAIL (Phase 5) ────────────────────────┐
   │  │ 1. abstained?  (retrieval below threshold, no usable sources)        │
   │  │       → GapAlert(kind="abstain")  ; reply stays the honest "I don't  │
   │  │         know" (Phase-1 contract preserved) ; alert fires             │
   │  │ 2. has evidence (final_input_context non-empty) AND verifier present?│
   │  │       verdict = Verifier.verify(query, answer, evidence)  (+usage)   │
   │  │       supported   → keep answer ; verified=True                      │
   │  │       NOT supported → GapAlert(kind="unverified") ; verified=False ; │
   │  │                       answer := PENDING_REVIEW                       │
   │  │ 3. no evidence (greeting/classification/T3 stub) → skip (verified=None)│
   │  └──────────────────────────────────────────────────────────────────────┘
   │   usage := exec + verifier + router   (cost includes the guardrail call)
   ▼
ExecutionResult{…, verified: bool|None, gap: GapAlert|None}
   │
   ▼
/chat → log aggregated usage
      → if res.gap: BackgroundTasks.add_task(alerter.alert, res.gap)   # async, after response
      → ChatResponse{…, verified, pending_review}
```

**Tech Stack:** builds on Phase 1 (retrieval/abstain), Phase 3 (`LLMClient` + `TokenUsage` +
in-memory+log collector pattern), Phase 4 (`Orchestrator`, `ExecutionResult`, `_extract_json`,
`synthesize`). **No new runtime deps** — the verifier reuses `LLMClient`; the alerter logs via
`logging` and (optionally) POSTs via the already-present `httpx`; async dispatch uses FastAPI's
built-in `BackgroundTasks`. Offline tests use `FakeLLM` + in-memory Qdrant + `FakeEmbedder` +
`TestClient` (which runs background tasks synchronously, so alert side-effects are assertable); one
`@integration` test routes through the live mock servers (skips if down).

**Key design decisions (locked for this phase):**

- **The guardrail lives in the `Orchestrator`, alert *dispatch* lives in the API.** `Orchestrator.run`
  owns the *answer*, so it decides verified/rejected and swaps in the escalation message. It does **not**
  perform I/O for the alert — it only attaches a `GapAlert` payload to the result. `/chat` dispatches
  that payload via `BackgroundTasks` so alerting is genuinely **async** and never blocks the reply, and
  the app-scoped `Alerter` (on `app.state`, like `UsageLog`) is inspectable in tests.
- **Verifier is opt-in by construction.** `Orchestrator(..., verifier: Verifier | None = None)`. When
  `verifier is None`, verification is **skipped** (`verified` stays `None`). This keeps every Phase-4
  test green untouched (their orchestrators pass no verifier) and lets production wire a real verifier
  via `get_orchestrator` only when `settings.verify_answers` is true.
- **Fail-closed verifier.** The verifier asks for JSON `{"supported": bool, "reason": str}` and parses
  it with the Phase-2 `_extract_json`. On any parse/validation failure it returns
  `Verdict(supported=False, reason="verifier parse failure (fail-closed)")` — an unparseable verdict
  means "we cannot prove this is grounded", so we **do not** serve it. Safety over availability.
- **Verification only runs when there is evidence.** Greeting, classification, and the Tier-3 stub
  carry an empty `final_input_context`; there is nothing claimed-from-sources to check, so they bypass
  the verifier (`verified=None`). Verification applies to Tier-1 **FAQ** (grounded in the retrieved KB
  answer) and **Tier-2** (grounded in the formatted tool results).
- **Two gap kinds, one alert channel, two replies.** `kind="abstain"` (retrieval found nothing usable)
  keeps the honest **"I don't know"** reply — we don't promise human follow-up on genuinely
  out-of-scope questions, but we *do* log the gap so the KB can grow. `kind="unverified"` (we had
  sources but the answer drifted) returns **"Pending Human Specialist Review"** because the user asked
  something in-scope we must not answer wrongly. Both fire the same async alert; the `kind` field
  distinguishes them downstream.
- **Verifier uses the cheapest tier.** Grounding is a yes/no check, so the verifier is built from
  `llm_for(1)` (the Tier-1 model). Its usage folds into `ExecutionResult.usage`, so the Phase-7
  cost-savings math sees the true per-request cost including the guardrail.
- **Mock stays coherent.** The Tier-1 mock learns a `VERIFIER_MARKER` (a substring of `VERIFIER_SYSTEM`,
  pinned by a guard test exactly like `ROUTER_MARKER`) and returns a deterministic
  `{"supported": true, …}` so the live-mock pipeline doesn't spuriously escalate. The verifier is
  *meaningfully* exercised on the `LLM_TYPE=openai` path and in the offline tests (which inject
  approving/rejecting verifiers directly).

**New/changed files at a glance:**

| File | Change |
|---|---|
| `src/tiered_rag/config.py` | + `alert_webhook_url`, `verify_answers` |
| `src/tiered_rag/alerting.py` | **new** — `GapAlert` + `Alerter` (in-mem list + log line + optional webhook) |
| `src/tiered_rag/verifier.py` | **new** — `Verdict`, `VERIFIER_SYSTEM`, `VERIFIER_MARKER`, `Verifier.verify()` |
| `src/tiered_rag/orchestrator.py` | `ExecutionResult` += `verified`/`abstained`/`gap`; `Tier1Executor` sets `abstained`; `Orchestrator` runs the guardrail + folds verifier usage; `PENDING_REVIEW` |
| `src/tiered_rag/mock_llm.py` | Tier-1 mock recognises `VERIFIER_MARKER` → deterministic supported verdict |
| `src/tiered_rag/api.py` | app-scoped `Alerter`; `get_alerter`; `get_orchestrator` wires the verifier; `/chat` async-dispatches the alert; `ChatResponse` += `verified`/`pending_review` |
| `tests/_helpers.py` | `build_orchestrator(..., verifier=None)` passthrough (Phase-4 tests stay green) |

---

## Task 0: Knowledge-gap alerting primitive — `GapAlert` + `Alerter` + config

**Files:**
- Create: `src/tiered_rag/alerting.py`
- Modify: `src/tiered_rag/config.py` (add `alert_webhook_url`, `verify_answers`)
- Test: `tests/test_alerting.py`
- Test: `tests/test_config.py` (extend)

**Design:** mirror `observability.UsageLog` — an in-memory collector that *also* emits a structured
log line, with an optional side channel. `Alerter.alert(gap)` always (1) appends to an in-memory
`alerts` list (test hook + simple inspection) and (2) logs a JSON line on the `tiered_rag.alerts`
logger. If `webhook_url` is set it *additionally* POSTs the alert JSON best-effort (errors swallowed —
alerting must never break a request). `GapAlert` carries everything a human needs: `kind`, `query`,
`answer` (the abstained/rejected reply), `evidence`, `reason`.

**Step 1: Write the failing tests** (`tests/test_alerting.py`)
```python
import logging

from tiered_rag.alerting import Alerter, GapAlert


def test_alert_is_collected_in_memory():
    a = Alerter()
    a.alert(GapAlert(kind="abstain", query="capital of France?", answer="I don't know"))
    assert len(a.alerts) == 1
    assert a.alerts[0].kind == "abstain"


def test_alert_emits_a_structured_log_line(caplog):
    a = Alerter()
    with caplog.at_level(logging.WARNING, logger="tiered_rag.alerts"):
        a.alert(GapAlert(kind="unverified", query="q", answer="bad", reason="claim X unsupported"))
    assert any("unverified" in r.getMessage() for r in caplog.records)


def test_no_webhook_call_when_url_empty(monkeypatch):
    # If a webhook were attempted with an empty URL the test HTTP layer would be touched;
    # assert the alerter does not call out when no URL is configured.
    called = {"n": 0}
    import tiered_rag.alerting as al
    monkeypatch.setattr(al.httpx, "post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    Alerter(webhook_url="").alert(GapAlert(kind="abstain", query="q", answer="a"))
    assert called["n"] == 0


def test_webhook_failure_is_swallowed(monkeypatch):
    import tiered_rag.alerting as al

    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(al.httpx, "post", _boom)
    # must not raise
    Alerter(webhook_url="http://example.test/hook").alert(
        GapAlert(kind="unverified", query="q", answer="a"))
```

Append to `tests/test_config.py`:
```python
def test_phase5_guardrail_defaults():
    s = Settings()
    assert s.alert_webhook_url == ""        # log-only by default
    assert s.verify_answers is True         # guardrail on by default
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.alerting`; missing settings)
Run: `pytest tests/test_alerting.py tests/test_config.py -v`

**Step 3: Implement**

Add to `Settings` (after the Phase-4 `item_details_path`):
```python
    # --- Zero-hallucination guardrails (Phase 5) ---
    verify_answers: bool = True            # run the verifier on grounded answers
    alert_webhook_url: str = ""            # empty -> log-only knowledge-gap alerts
```

`src/tiered_rag/alerting.py`:
```python
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

import httpx

logger = logging.getLogger("tiered_rag.alerts")


@dataclass
class GapAlert:
    """A knowledge-gap signal worth a human's attention."""
    kind: str                       # "abstain" (no sources) | "unverified" (answer not supported)
    query: str
    answer: str                     # the abstained / rejected reply (for human context)
    evidence: str = ""              # final_input_context the answer was (not) grounded in
    reason: str = ""                # verifier reason, when kind == "unverified"


class Alerter:
    """In-memory collector + structured logger for knowledge-gap alerts.

    Optionally POSTs each alert to a webhook (best-effort; failures are swallowed so
    alerting can never break a user request). Designed to be dispatched asynchronously
    (FastAPI BackgroundTasks) from the gateway.
    """

    def __init__(self, webhook_url: str = "") -> None:
        self.webhook_url = webhook_url
        self.alerts: list[GapAlert] = []

    def alert(self, gap: GapAlert) -> None:
        self.alerts.append(gap)
        logger.warning("knowledge_gap %s", json.dumps(asdict(gap)))
        if self.webhook_url:
            try:
                httpx.post(self.webhook_url, json=asdict(gap), timeout=2.0)
            except Exception:  # best-effort; alerting must never raise into the request path
                logger.exception("knowledge-gap webhook failed")
```
*(`field` import is harmless if unused now; drop it if your linter objects — kept for parity with
`observability`'s dataclass style. Remove the import if you prefer a clean lint.)*

**Step 4: Run → expect PASS**
Run: `pytest tests/test_alerting.py tests/test_config.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/alerting.py src/tiered_rag/config.py \
        tests/test_alerting.py tests/test_config.py
git commit -m "feat(p5): knowledge-gap alerting primitive (GapAlert + Alerter) + config"
```

---

## Task 1: Verifier agent — grounded answer check (fail-closed)

**Files:**
- Create: `src/tiered_rag/verifier.py`
- Test: `tests/test_verifier.py`

**Design:** `Verifier.verify(query, answer, evidence) -> tuple[Verdict, TokenUsage]` makes one LLM
call with `VERIFIER_SYSTEM` (a strict grounding instruction) and a user message embedding the
sources + the proposed answer. Parse the reply with the Phase-2 `_extract_json`; validate into
`Verdict`. On any failure → **fail-closed** `Verdict(supported=False, …)`. `VERIFIER_MARKER` is a
stable substring of `VERIFIER_SYSTEM` that the Phase-3 mock will key on (guard test in Task 4).

**Step 1: Write the failing tests** (`tests/test_verifier.py`)
```python
import json

from tiered_rag.llm.client import FakeLLM
from tiered_rag.verifier import VERIFIER_MARKER, VERIFIER_SYSTEM, Verdict, Verifier


def test_marker_is_substring_of_system_prompt():
    assert VERIFIER_MARKER in VERIFIER_SYSTEM


def test_supported_answer_passes():
    llm = FakeLLM(json.dumps({"supported": True, "reason": "fully grounded"}))
    verdict, usage = Verifier(llm).verify("q", "Open Settings > Security.", "Open Settings > Security.")
    assert isinstance(verdict, Verdict)
    assert verdict.supported is True
    assert usage.total_tokens > 0


def test_unsupported_answer_is_rejected():
    llm = FakeLLM(json.dumps({"supported": False, "reason": "claim not in sources"}))
    verdict, _ = Verifier(llm).verify("q", "It costs $5.", "name: Dragon Skin")
    assert verdict.supported is False
    assert "not in sources" in verdict.reason


def test_unparseable_verdict_fails_closed():
    verdict, _ = Verifier(FakeLLM("totally not json")).verify("q", "ans", "evidence")
    assert verdict.supported is False          # fail-closed: cannot prove grounded -> reject
    assert "fail-closed" in verdict.reason.lower()


def test_verify_passes_evidence_and_answer_to_the_llm():
    seen = {}

    def responder(system, user):
        seen["system"], seen["user"] = system, user
        return json.dumps({"supported": True, "reason": "ok"})
    Verifier(FakeLLM(responder)).verify("the question", "the answer", "the sources")
    assert VERIFIER_MARKER in seen["system"]
    assert "the sources" in seen["user"] and "the answer" in seen["user"]
```

**Step 2: Run → expect FAIL** (`ModuleNotFoundError: tiered_rag.verifier`)
Run: `pytest tests/test_verifier.py -v`

**Step 3: Implement** `src/tiered_rag/verifier.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from .llm.client import LLMClient
from .llm.usage import TokenUsage
from .router import _extract_json

# Stable substring the Phase-3 mock keys on (guard test in test_mock_llm).
VERIFIER_MARKER = "grounding verifier"

VERIFIER_SYSTEM = (
    "You are a strict grounding verifier for a support chatbot.\n"
    "Given SOURCES and a proposed ANSWER, decide whether EVERY factual claim in the answer is "
    "directly supported by the sources. If the answer states any fact not present in the sources, "
    "or the sources are empty/insufficient, it is NOT supported.\n"
    'Reply with JSON only (no prose, no markdown fence): '
    '{"supported": <true|false>, "reason": "<short reason>"}'
)


@dataclass
class Verdict:
    supported: bool
    reason: str = ""


def _verify_user(query: str, answer: str, evidence: str) -> str:
    return f"SOURCES:\n{evidence}\n\nANSWER:\n{answer}\n\nQUESTION:\n{query}"


class Verifier:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def verify(self, query: str, answer: str, evidence: str) -> tuple[Verdict, TokenUsage]:
        resp = self.llm.complete(VERIFIER_SYSTEM, _verify_user(query, answer, evidence))
        try:
            data = _extract_json(resp.content)
            verdict = Verdict(supported=bool(data["supported"]),
                              reason=str(data.get("reason", "")))
        except Exception:
            verdict = Verdict(supported=False, reason="verifier parse failure (fail-closed)")
        return verdict, resp.usage
```

**Step 4: Run → expect PASS**
Run: `pytest tests/test_verifier.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/verifier.py tests/test_verifier.py
git commit -m "feat(p5): verifier agent (grounded answer check, fail-closed)"
```

---

## Task 2: Orchestrator guardrail — verify, escalate, attach gap, fold usage

**Files:**
- Modify: `src/tiered_rag/orchestrator.py` (`ExecutionResult` fields; `Tier1Executor` abstain flag;
  `PENDING_REVIEW`; `Orchestrator` guardrail + verifier usage)
- Modify: `tests/_helpers.py` (`build_orchestrator` gains an optional `verifier` passthrough)
- Test: `tests/test_orchestrator_guardrail.py`

**Design:**
1. `ExecutionResult` gains `verified: bool | None = None`, `abstained: bool = False`,
   `gap: GapAlert | None = None`.
2. `Tier1Executor` sets `abstained=True` on its abstain branch (answer stays `I_DONT_KNOW` — the
   existing Phase-4 executor test is untouched).
3. `Orchestrator(__init__)` accepts `verifier: Verifier | None = None`. New `_guardrail(query, res)`:
   - `res.abstained` → attach `GapAlert(kind="abstain", …)`; reply unchanged.
   - else if `res.final_input_context` and `self.verifier` → `verify`; fold verifier usage into
     `res.usage`; set `res.verified`; if not supported → attach `GapAlert(kind="unverified", …)` and
     set `res.answer = PENDING_REVIEW`.
   - else → leave as-is (`verified=None`).
   Call `_guardrail` in `run()` **before** folding the router usage.

**Step 1: Write the failing tests** (`tests/test_orchestrator_guardrail.py`)
```python
from tiered_rag.llm.client import FakeLLM
from tiered_rag.orchestrator import PENDING_REVIEW
from tiered_rag.verifier import Verifier

from tests._helpers import build_orchestrator

APPROVE = Verifier(FakeLLM('{"supported": true, "reason": "grounded"}'))
REJECT = Verifier(FakeLLM('{"supported": false, "reason": "claim not in sources"}'))


def test_supported_answer_passes_through(fake_embedder):
    orch = build_orchestrator(fake_embedder, 1, "faq", verifier=APPROVE)
    res = orch.run("how do I reset my password")
    assert "Open Settings > Security > Reset." in res.answer
    assert res.verified is True
    assert res.gap is None


def test_unverified_answer_is_escalated(fake_embedder):
    orch = build_orchestrator(fake_embedder, 1, "faq", verifier=REJECT)
    res = orch.run("how do I reset my password")
    assert res.answer == PENDING_REVIEW
    assert res.verified is False
    assert res.gap is not None and res.gap.kind == "unverified"
    assert res.usage.total_tokens > 0          # router + synth + verifier folded


def test_tier2_unverified_is_escalated(fake_embedder):
    res = build_orchestrator(fake_embedder, 2, verifier=REJECT).run("full details for SKU-07")
    assert res.answer == PENDING_REVIEW
    assert res.gap.kind == "unverified"


def test_abstain_attaches_gap_but_keeps_i_dont_know(fake_embedder):
    # threshold 0.6 default; an out-of-scope query abstains via the real retriever in _helpers?
    # _helpers uses threshold 0.6 with a single stored doc; use a query that won't match.
    orch = build_orchestrator(fake_embedder, 1, "faq", verifier=APPROVE)
    res = orch.run("what is the capital of France")
    from tiered_rag.orchestrator import I_DONT_KNOW
    assert res.answer == I_DONT_KNOW
    assert res.gap is not None and res.gap.kind == "abstain"
    assert res.verified is None                # never ran the verifier (no evidence)


def test_greeting_skips_verification(fake_embedder):
    orch = build_orchestrator(fake_embedder, 1, "greeting", verifier=REJECT)
    res = orch.run("hi there!")
    assert res.answer != PENDING_REVIEW        # no evidence -> verifier never runs
    assert res.verified is None
    assert res.gap is None
```
*(Note on the abstain test: `build_orchestrator` builds the retriever with `threshold=0.6` and a
single stored doc whose `FakeEmbedder` vector only matches its own exact text; "capital of France"
hashes to a different vector with cosine < 0.6, so retrieval abstains. If this proves flaky, add a
`threshold` knob to `build_retriever`/`build_orchestrator` and pass `0.999`.)*

**Step 2: Run → expect FAIL** (`ImportError: PENDING_REVIEW`; `build_orchestrator` has no `verifier`
kwarg; `ExecutionResult` has no `verified`/`gap`)
Run: `pytest tests/test_orchestrator_guardrail.py -v`

**Step 3: Implement**

In `src/tiered_rag/orchestrator.py`, add to the imports:
```python
from .alerting import GapAlert
from .verifier import Verifier
```
Add the escalation constant next to `I_DONT_KNOW`:
```python
PENDING_REVIEW = ("This needs a human specialist. I've flagged it for review "
                  "(Pending Human Specialist Review) and someone will follow up shortly.")
```
Extend `ExecutionResult` (add three fields; keep existing ones):
```python
@dataclass
class ExecutionResult:
    tier: int
    answer: str
    final_input_context: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    reason: str = ""
    plan: str | None = None
    verified: bool | None = None          # None = not applicable/not run
    abstained: bool = False
    gap: "GapAlert | None" = None
```
In `Tier1Executor.execute`, set the abstain flag (only the abstain return changes):
```python
        rr = self.retriever.retrieve(query)
        if rr.abstain:
            return ExecutionResult(tier=1, answer=I_DONT_KNOW, plan="faq", abstained=True)
```
Give `Orchestrator` a verifier and the guardrail stage:
```python
class Orchestrator:
    def __init__(self, router: Router, retriever: Retriever, catalog: dict,
                 llm_for: Callable[[int], LLMClient], verifier: Verifier | None = None):
        self.router, self.retriever, self.catalog = router, retriever, catalog
        self.llm_for, self.verifier = llm_for, verifier

    def _guardrail(self, query: str, res: ExecutionResult) -> ExecutionResult:
        if res.abstained:
            res.gap = GapAlert(kind="abstain", query=query, answer=res.answer)
            return res
        if res.final_input_context and self.verifier is not None:
            verdict, vusage = self.verifier.verify(query, res.answer, res.final_input_context)
            res.usage = TokenUsage(res.usage.prompt_tokens + vusage.prompt_tokens,
                                   res.usage.completion_tokens + vusage.completion_tokens)
            res.verified = verdict.supported
            if not verdict.supported:
                res.gap = GapAlert(kind="unverified", query=query, answer=res.answer,
                                   evidence=res.final_input_context, reason=verdict.reason)
                res.answer = PENDING_REVIEW
        return res

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

        res = self._guardrail(query, res)

        res.reason = sel.reason
        res.plan = res.plan if res.plan is not None else sel.plan
        res.usage = TokenUsage(
            res.usage.prompt_tokens + route.usage.prompt_tokens,
            res.usage.completion_tokens + route.usage.completion_tokens,
        )
        return res
```

Update `tests/_helpers.py` — add a `verifier` passthrough so Phase-4 callers stay green:
```python
def build_orchestrator(fake_embedder, route_tier, route_plan=None, verifier=None):
    router = Router(FakeLLM(json.dumps({"tier": route_tier, "reason": "x", "plan": route_plan})))

    def llm_for(tier):
        if tier == 2:
            def r(system, user):
                return (json.dumps({"calls": [{"tool": "get_item_details_from_xlsx",
                                               "args": {"item_id": "SKU-07"}}]})
                        if "plan" in system.lower() else user)
            return FakeLLM(r)
        return FakeLLM(lambda s, u: u)
    return Orchestrator(router, build_retriever(fake_embedder), CATALOG, llm_for, verifier=verifier)
```

**Step 4: Run → expect PASS** (new guardrail tests **and** the untouched Phase-4 suites)
```bash
pytest tests/test_orchestrator_guardrail.py tests/test_orchestrator.py \
       tests/test_orchestrator_tier1.py tests/test_orchestrator_tier2.py -v
```

**Step 5: Commit**
```bash
git add src/tiered_rag/orchestrator.py tests/_helpers.py tests/test_orchestrator_guardrail.py
git commit -m "feat(p5): orchestrator guardrail (verify -> escalate + attach knowledge-gap)"
```

---

## Task 3: Wire the guardrail into `/chat` — async alert + escalation fields

**Files:**
- Modify: `src/tiered_rag/api.py` (`Alerter` on `app.state`; `get_alerter`; `get_orchestrator` wires
  the verifier; `/chat` async-dispatches the alert; `ChatResponse` += `verified`/`pending_review`)
- Test: `tests/test_api.py` (extend)

**Design:** `create_app` builds an app-scoped `Alerter(settings.alert_webhook_url)` on `app.state`
(like `UsageLog`). `get_orchestrator` builds the verifier from `build_llm(s, 1)` when
`s.verify_answers`. `/chat` takes `BackgroundTasks`; after running the orchestrator it logs usage,
and **if `res.gap is not None`** it schedules `background_tasks.add_task(alerter.alert, res.gap)`
(async — fires after the response is sent). `ChatResponse` surfaces `verified` and a `pending_review`
boolean (`res.gap is not None and res.gap.kind == "unverified"`). `TestClient` runs background tasks
synchronously on response, so tests can assert `alerter.alerts` after the call.

**Step 1: Write the failing tests** — append to `tests/test_api.py`:
```python
from tiered_rag.api import get_alerter      # add to the existing imports
from tiered_rag.llm.client import FakeLLM
from tiered_rag.verifier import Verifier


def test_chat_escalates_and_alerts_on_unverified_answer(fake_embedder):
    reject = Verifier(FakeLLM('{"supported": false, "reason": "ungrounded"}'))
    orch = build_orchestrator(fake_embedder, 1, "faq", verifier=reject)
    app = create_app()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    client = TestClient(app)
    body = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert body["pending_review"] is True
    assert body["verified"] is False
    assert "Pending Human Specialist Review" in body["answer"]
    # async alert fired (TestClient runs BackgroundTasks on response)
    assert len(app.state.alerter.alerts) == 1
    assert app.state.alerter.alerts[0].kind == "unverified"


def test_chat_supported_answer_has_no_alert(fake_embedder):
    approve = Verifier(FakeLLM('{"supported": true, "reason": "ok"}'))
    orch = build_orchestrator(fake_embedder, 1, "faq", verifier=approve)
    app = create_app()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    client = TestClient(app)
    body = client.post("/chat", json={"query": "how do I reset my password"}).json()
    assert body["verified"] is True
    assert body["pending_review"] is False
    assert len(app.state.alerter.alerts) == 0
```

**Step 2: Run → expect FAIL** (`ImportError: get_alerter`; `ChatResponse` has no `pending_review`)
Run: `pytest tests/test_api.py -v`

**Step 3: Implement** — update `src/tiered_rag/api.py`:
- import `BackgroundTasks` from `fastapi`; import `Alerter` from `.alerting`; import `Verifier` from
  `.verifier`.
- `ChatResponse` gains `verified: bool | None = None` and `pending_review: bool = False`.
- in `create_app`: `app.state.alerter = Alerter(get_settings().alert_webhook_url)`.
- add the dependency + wire the verifier:
```python
def get_alerter(request: Request) -> Alerter:
    return request.app.state.alerter


def get_orchestrator() -> Orchestrator:
    s = get_settings()
    router = Router(build_llm(s, 1), temperature=s.router_temperature)
    store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
    retriever = Retriever(store, OllamaEmbedder(s.ollama_host, s.embed_model), s.confidence_threshold)
    catalog = catalog_index(load_item_details(s.item_details_path))
    verifier = Verifier(build_llm(s, 1)) if s.verify_answers else None
    return Orchestrator(router, retriever, catalog,
                        llm_for=lambda tier: build_llm(s, tier), verifier=verifier)
```
- replace the `/chat` body:
```python
    @app.post("/chat", response_model=ChatResponse)
    def chat(
        req: ChatRequest,
        background_tasks: BackgroundTasks,
        orchestrator: Orchestrator = Depends(get_orchestrator),
        usage_log: UsageLog = Depends(get_usage_log),
        alerter: Alerter = Depends(get_alerter),
        settings: Settings = Depends(get_settings_dep),
    ):
        t0 = time.perf_counter()
        res = orchestrator.run(req.query)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        rec = usage_log.record(tier=res.tier, model=settings.openai_model,
                               usage=res.usage, latency_ms=latency_ms, settings=settings)
        if res.gap is not None:
            background_tasks.add_task(alerter.alert, res.gap)   # async knowledge-gap alert
        return ChatResponse(
            tier=res.tier, reason=res.reason, plan=res.plan, answer=res.answer,
            verified=res.verified,
            pending_review=(res.gap is not None and res.gap.kind == "unverified"),
            usage=Usage(prompt_tokens=rec.prompt_tokens, completion_tokens=rec.completion_tokens,
                        total_tokens=rec.total_tokens, cost_usd=rec.cost_usd),
        )
```

**Step 4: Run → expect PASS** (new API tests + the existing `/chat`/`/usage` tests stay green)
Run: `pytest tests/test_api.py -v`

**Step 5: Commit**
```bash
git add src/tiered_rag/api.py tests/test_api.py
git commit -m "feat(p5): /chat runs the guardrail (async knowledge-gap alert + escalation fields)"
```

---

## Task 4: Verifier-aware mock + integration test + README

**Files:**
- Modify: `src/tiered_rag/mock_llm.py` (recognise `VERIFIER_MARKER`)
- Test: `tests/test_mock_llm.py` (extend: marker guard + verifier reply shape)
- Test: `tests/test_integration_pipeline.py` (extend: guardrail does not crash the live-mock pipeline)
- Modify: `README.md` (Phase-5 section)

**Design:** the Tier-1 mock already returns routing JSON when it sees `ROUTER_MARKER`. Teach it to
return a deterministic **supported** verdict when it sees `VERIFIER_MARKER`, so the live-mock pipeline
never spuriously escalates. A guard test pins `VERIFIER_MARKER` to the real `VERIFIER_SYSTEM`
(mirroring the existing `ROUTER_MARKER` guard).

**Step 1: Write the failing tests** — append to `tests/test_mock_llm.py`:
```python
def test_verifier_marker_present_in_real_prompt():
    from tiered_rag.verifier import VERIFIER_MARKER, VERIFIER_SYSTEM
    assert VERIFIER_MARKER in VERIFIER_SYSTEM


def test_tier1_mock_returns_supported_verdict_for_verifier_prompt():
    import json

    from fastapi.testclient import TestClient

    from tiered_rag.mock_llm import create_mock_app
    from tiered_rag.verifier import VERIFIER_SYSTEM
    client = TestClient(create_mock_app(1))
    body = {"model": "mock", "messages": [
        {"role": "system", "content": VERIFIER_SYSTEM},
        {"role": "user", "content": "SOURCES:\n...\n\nANSWER:\n...\n\nQUESTION:\n..."}]}
    content = client.post("/v1/chat/completions", json=body).json()["choices"][0]["message"]["content"]
    assert json.loads(content)["supported"] is True
```

**Step 2: Run → expect FAIL** (mock returns the canned tier-1 string, not a verdict)
Run: `pytest tests/test_mock_llm.py -v`

**Step 3: Implement** — in `src/tiered_rag/mock_llm.py`, extend `_reply` (import the marker lazily to
avoid a circular import with `verifier` → `router`; `mock_llm` has no other `verifier` dep):
```python
def _reply(tier: int, system: str, user: str) -> str:
    if ROUTER_MARKER in system:
        chosen = _classify(user)
        return json.dumps({"tier": chosen, "reason": f"mock tier-{chosen} (deterministic)", "plan": None})
    from .verifier import VERIFIER_MARKER
    if VERIFIER_MARKER in system:
        return json.dumps({"supported": True, "reason": "mock verifier (deterministic)"})
    return f"[mock tier-{tier}] deterministic answer for: {user[:80]}"
```
*(If you prefer no import in the hot path, hard-code `VERIFIER_MARKER = "grounding verifier"` as a
local constant in `mock_llm.py` and keep the guard test asserting it equals `verifier.VERIFIER_MARKER`.)*

**Step 4: Extend the integration test** (`tests/test_integration_pipeline.py`) — the guardrail must
not break the live-mock pipeline:
```python
def test_pipeline_guardrail_does_not_break_mocks(monkeypatch):
    s = get_settings()
    if not _up(s.mock_llm_base_url):
        pytest.skip("mock tier servers not running")
    monkeypatch.setenv("LLM_TYPE", "mock")
    client = TestClient(create_app())
    body = client.post("/chat", json={"query": "give me the full details for item SKU-07"}).json()
    assert body["tier"] == 2
    assert body["usage"]["total_tokens"] > 0
    # mock planner returns no usable tool plan -> empty context -> verification is skipped,
    # so the answer is served (not escalated). The verifier is meaningfully exercised on the
    # LLM_TYPE=openai path and in the offline guardrail tests.
    assert body["pending_review"] is False
```

**Step 5: Run the full suite + write the README**
```bash
pytest -m "not integration" -v      # all offline (FakeLLM + in-memory Qdrant + TestClient)
# bring up mocks (Phase 3) then:
pytest -m integration -v            # pipeline + Phase-1/2/3; skips what's down
```
`README.md` — add a **Phase-5 "Zero-Hallucination Guardrails"** section: the verifier agent
(grounded answer check, **fail-closed**), the two knowledge-gap kinds (`abstain` keeps "I don't
know"; `unverified` → "Pending Human Specialist Review"), the **async** alert via `BackgroundTasks`
(log line always + optional webhook), the new `verified`/`pending_review` response fields, the
verifier-aware mock, and an example `/chat` response showing an escalation.

**Step 6: Commit**
```bash
git add src/tiered_rag/mock_llm.py tests/test_mock_llm.py \
        tests/test_integration_pipeline.py README.md
git commit -m "feat(p5): verifier-aware mock + guardrail integration test + README"
```

---

## Phase 5 Definition of Done

- [ ] `pytest -m "not integration"` → all green, fully offline (FakeLLM + in-memory Qdrant + TestClient).
- [ ] **Verifier agent** checks the synthesized answer against its evidence and **fails closed** on an
      unparseable verdict (`tiered_rag.verifier.Verifier`).
- [ ] **Knowledge-gap alerting**: `Alerter` collects in-memory + logs a structured line + optionally
      POSTs a webhook (best-effort, never raises); dispatched **async** from `/chat` via `BackgroundTasks`.
- [ ] Guardrail wired into `Orchestrator.run`: abstain → `GapAlert(kind="abstain")` + honest "I don't
      know"; unsupported answer → `GapAlert(kind="unverified")` + "Pending Human Specialist Review";
      greeting/classification/T3 (no evidence) bypass verification.
- [ ] Verifier usage **folds into** `ExecutionResult.usage` (cost includes the guardrail call).
- [ ] `/chat` returns `verified` + `pending_review`; `/usage` totals + Phase-4 answers stay green; all
      Phase-1–4 tests untouched and passing.
- [ ] Mock Tier-1 server recognises `VERIFIER_MARKER` (guard test in sync with `VERIFIER_SYSTEM`);
      `pytest -m integration` runs the guarded pipeline against live mocks (skips if down).
- [ ] README Phase-5 section written. All work committed.

**Next:** write `SIXTH_PHASE_PLAN.md` (Tier-3 Multi-Step Reasoning) — the Tier-3 LLM generates a
**chained plan** (step1 → step2 → step3, each step consuming the prior step's output), executed
sequentially with context threading into `final_input_context`, then a final grounded synthesis that
**flows through the same Phase-5 guardrail** (verifier + knowledge-gap escalation). Phase 5's
`Verifier` + `Alerter` are exactly the safety net Tier-3's longer reasoning chains need, and the
`Tier2Executor` tool-dispatch loop is the pattern the Tier-3 chain executor will generalise.
