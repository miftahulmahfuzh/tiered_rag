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
from .tools.registry import TOOLS, run_tool
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
