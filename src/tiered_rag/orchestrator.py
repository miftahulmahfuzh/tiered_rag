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
