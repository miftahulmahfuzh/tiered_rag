from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel

from .alerting import GapAlert
from .llm.client import LLMClient
from .llm.usage import LLMResponse, TokenUsage
from .retrieval import Retriever
from .router import Router, _extract_json
from .tools.registry import TOOLS
from .verifier import Verifier

I_DONT_KNOW = ("I'm sorry, I don't have enough information to answer that. "
               "Let me know if there's something else I can help with.")

PENDING_REVIEW = ("This needs a human specialist. I've flagged it for review "
                  "(Pending Human Specialist Review) and someone will follow up shortly.")

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
    steps: list[dict] = field(default_factory=list)   # full ordered plan surfaced to the output

    usage: TokenUsage = field(default_factory=TokenUsage)
    reason: str = ""
    plan: str | None = None
    verified: bool | None = None          # None = not applicable/not run
    abstained: bool = False
    gap: "GapAlert | None" = None


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
            return ExecutionResult(tier=1, answer=I_DONT_KNOW, plan="faq", abstained=True)
        context = rr.answer or ""
        r = synthesize(self.llm, FAQ_SYSTEM, context, query)
        return ExecutionResult(tier=1, answer=r.content, final_input_context=context,
                               usage=r.usage, plan="faq")


def _tool_menu() -> str:
    return "\n".join(f"- {t.name}: {t.description} args {t.args}" for t in TOOLS.values())


def _dispatch(name: str, args: dict, catalog: dict) -> dict:
    """Run a planned tool call, never crashing the pipeline. An unrecognised tool
    *name* and a recognised tool called with *bad arguments* are distinct failures
    — conflating them is what mislabeled a missing arg key as "unknown tool"."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return tool.run(args, catalog)
    except Exception as e:
        return {"error": f"bad arguments for {name}: {e}"}


TIER2_PLAN_SYSTEM = (
    "You are the Tier-2 planner. Build a pipeline plan of tool calls to answer the user.\n"
    "Available tools:\n" + _tool_menu() + "\n\n"
    'Reply with JSON only: {"calls": [{"tool": "<name>", "args": {<k>: <v>}}, ...]}. '
    "Use an empty list if no tool is needed."
)


# Stable plan labels carried to the output for tier 2/3 (tier-1 labels live in the router).
TIER2_PLAN_LABEL = "tool_pipeline"
TIER3_PLAN_LABEL = "multi_step_chain"


class ToolCall(BaseModel):
    tool: str
    args: dict = {}


class Tier2Plan(BaseModel):
    calls: list[ToolCall] = []


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
            result = _dispatch(call.tool, call.args, self.catalog)
            tool_calls.append({"tool": call.tool, "args": call.args, "result": result})

        context = _format_context(tool_calls)
        synth = synthesize(self.llm, FAQ_SYSTEM, context, query)
        usage = TokenUsage(
            plan_usage.prompt_tokens + synth.usage.prompt_tokens,
            plan_usage.completion_tokens + synth.usage.completion_tokens,
        )
        return ExecutionResult(tier=2, answer=synth.content, final_input_context=context,
                               tool_calls=tool_calls, steps=tool_calls, usage=usage,
                               plan=TIER2_PLAN_LABEL)


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
                  transcript: list[str]) -> tuple[str, dict, TokenUsage]:
        """Run one step and return (transcript line, structured step record, token usage).

        The record always carries ``step``/``instruction``/``tool``; tool & retrieve steps
        add ``args``/``result``, a reasoning step (tool=null) adds its ``output``."""
        base = {"step": i, "instruction": step.instruction, "tool": step.tool}
        if step.tool == "retrieve":
            if self.retriever is None:
                result = {"error": "no retriever available"}
            else:
                rr = self.retriever.retrieve(step.args.get("query") or query)
                result = {"answer": rr.answer, "abstain": rr.abstain, "score": round(rr.score, 3)}
            rec = {**base, "args": step.args, "result": result}
            return f"[step {i}] retrieve -> {json.dumps(result)}", rec, TokenUsage()
        if step.tool:
            result = _dispatch(step.tool, step.args, self.catalog)
            rec = {**base, "args": step.args, "result": result}
            return f"[step {i}] {step.tool}({step.args}) -> {json.dumps(result)}", rec, TokenUsage()
        # reasoning step: thread the running transcript forward
        prior = "\n".join(transcript)
        user = f"PRIOR STEPS:\n{prior}\n\nNOW DO: {step.instruction}" if prior else step.instruction
        r = self.llm.complete(TIER3_STEP_SYSTEM, user)
        rec = {**base, "output": r.content}
        return f"[step {i}] {step.instruction} -> {r.content}", rec, r.usage

    def execute(self, query: str) -> ExecutionResult:
        plan, usage = self._plan(query)
        transcript: list[str] = []
        steps: list[dict] = []
        for i, step in enumerate(plan.steps[: self.max_steps], start=1):
            line, rec, step_usage = self._run_step(i, step, query, transcript)
            transcript.append(line)
            steps.append(rec)
            usage = TokenUsage(usage.prompt_tokens + step_usage.prompt_tokens,
                               usage.completion_tokens + step_usage.completion_tokens)
        # tool_calls is the tool/retrieve subset (reasoning steps have no tool)
        tool_calls = [s for s in steps if s["tool"] is not None]
        context = "\n".join(transcript)
        synth = synthesize(self.llm, FAQ_SYSTEM, context, query)
        usage = TokenUsage(usage.prompt_tokens + synth.usage.prompt_tokens,
                           usage.completion_tokens + synth.usage.completion_tokens)
        return ExecutionResult(tier=3, answer=synth.content, final_input_context=context,
                               tool_calls=tool_calls, steps=steps, usage=usage,
                               plan=TIER3_PLAN_LABEL)


class Orchestrator:
    def __init__(self, router: Router, retriever: Retriever, catalog: dict,
                 llm_for: Callable[[int], LLMClient], verifier: Verifier | None = None,
                 tier3_max_steps: int = 5):
        self.router, self.retriever, self.catalog = router, retriever, catalog
        self.llm_for, self.verifier, self.tier3_max_steps = llm_for, verifier, tier3_max_steps

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
            res = Tier3Executor(self.llm_for(3), self.retriever, self.catalog,
                                self.tier3_max_steps).execute(query)
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
