from .embeddings import Embedder
from .vector_store import QdrantStore


def ingest(rows: list[dict], store: QdrantStore, embedder: Embedder) -> int:
    """Load the KB into Qdrant. Idempotent: re-running never spawns duplicates.

    Two independent safeguards keep the collection at exactly len(rows) points
    no matter how many times this runs:
      1. ``store.recreate`` drops and recreates the collection first, so every
         run rebuilds from empty (a clean, known-good state).
      2. each point uses the row's stable ``id`` as its Qdrant point id, so even
         a bare upsert overwrites the same id instead of appending a new point.
    Note this is destructive by design: it wipes the collection before reload,
    so any documents ingested from another source would also be cleared.
    """
    vectors = embedder.embed_documents([r["question"] for r in rows])
    store.recreate(dim=len(vectors[0]))  # wipe + recreate -> no carryover from prior runs
    store.upsert([
        {
            "id": r["id"],  # stable point id -> re-upsert overwrites, never duplicates
            "vector": v,
            "payload": {
                "question": r["question"],
                "answer": r["answer"],
                "category": r["category"],
                "id": r["id"],
            },
        }
        for r, v in zip(rows, vectors)
    ])
    return len(rows)


def main():  # real deployment path
    from qdrant_client import QdrantClient

    from .config import get_settings
    from .embeddings import OllamaEmbedder
    from .knowledge_base import load_knowledge_base

    s = get_settings()
    store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
    emb = OllamaEmbedder(s.ollama_host, s.embed_model)
    n = ingest(load_knowledge_base("xlsx/knowledge_base.xlsx"), store, emb)
    print(f"ingested {n} rows into '{s.qdrant_collection}'")


if __name__ == "__main__":
    main()
