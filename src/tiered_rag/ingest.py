from .embeddings import Embedder
from .vector_store import QdrantStore


def ingest(rows: list[dict], store: QdrantStore, embedder: Embedder) -> int:
    vectors = embedder.embed_documents([r["question"] for r in rows])
    store.recreate(dim=len(vectors[0]))
    store.upsert([
        {
            "id": r["id"],
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
