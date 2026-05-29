"""Phase-8 EVAL_REPORT data generator: run the Phase-1 abstention + Phase-2 routing
harnesses LIVE and print a markdown block to paste into EVAL_REPORT.md.

Discipline (mirrors the Phase-7 README): assemble numbers from REAL runs, never invent.
Each section is skipped gracefully (with a logged note) if its backing service is down,
so a missing ollama/Qdrant/LLM never crashes the whole report.

Usage:
    docker compose up -d qdrant && ollama serve &     # real RAG
    python -m tiered_rag.ingest                       # KB into Qdrant
    python scripts/eval_report.py                     # prints abstention + routing blocks
"""
import sys

from tiered_rag.config import get_settings
from tiered_rag.eval_abstention import evaluate as eval_abstention
from tiered_rag.eval_routing import evaluate as eval_routing
from tiered_rag.embeddings import OllamaEmbedder
from tiered_rag.llm.client import build_llm
from tiered_rag.retrieval import Retriever
from tiered_rag.router import Router
from tiered_rag.vector_store import QdrantStore

sys.path.insert(0, "tests")
from data.eval_questions import IN_SCOPE, OUT_OF_SCOPE  # noqa: E402
from data.routing_questions import ROUTING_QUESTIONS    # noqa: E402


def abstention_block(s) -> None:
    from qdrant_client import QdrantClient

    print("## Abstention (Phase 1 — real ollama + Qdrant)\n")
    try:
        store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
        retriever = Retriever(store, OllamaEmbedder(s.ollama_host, s.embed_model),
                              s.confidence_threshold)
        dataset = ([{"q": q, "should_answer": True} for q in IN_SCOPE]
                   + [{"q": q, "should_answer": False} for q in OUT_OF_SCOPE])
        m = eval_abstention(retriever, dataset)
    except Exception as e:
        print(f"_skipped: {type(e).__name__}: {e}_\n")
        return
    print(f"- threshold: `{s.confidence_threshold}`")
    print(f"- abstention rate (out-of-scope): **{m['abstention_rate']:.2%}** "
          f"({len(OUT_OF_SCOPE)} OOD questions)")
    print(f"- false-abstention rate (in-scope paraphrases): **{m['false_abstention_rate']:.2%}** "
          f"({len(IN_SCOPE)} in-scope questions)\n")


def routing_block(s) -> None:
    print(f"## Routing accuracy (LLM_TYPE={s.llm_type}, model={s.model_for_tier(1)})\n")
    try:
        router = Router(build_llm(s, 1), temperature=s.router_temperature)
        m = eval_routing(router, ROUTING_QUESTIONS)
    except Exception as e:
        print(f"_skipped: {type(e).__name__}: {e}_\n")
        return
    print(f"- overall accuracy: **{m['accuracy']:.2%}** over {len(ROUTING_QUESTIONS)} questions\n")
    print("| Category | Accuracy |")
    print("|---|---|")
    for cat, acc in sorted(m["per_category"].items()):
        print(f"| {cat} | {acc:.2%} |")
    print()


def main() -> None:
    s = get_settings()
    print(f"# EVAL_REPORT data (generated live) — backend `{s.llm_type}`\n")
    abstention_block(s)
    routing_block(s)
    print("> Token/cost-savings + cache hit-rate + load line come from Phase-7 "
          "`/stats` + `scripts/load_test.py` (see EVAL_REPORT.md).")


if __name__ == "__main__":
    main()
