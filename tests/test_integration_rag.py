import httpx
import pytest
from qdrant_client import QdrantClient

from tiered_rag.config import get_settings
from tiered_rag.embeddings import OllamaEmbedder
from tiered_rag.ingest import ingest
from tiered_rag.knowledge_base import load_knowledge_base
from tiered_rag.retrieval import Retriever
from tiered_rag.vector_store import QdrantStore

pytestmark = pytest.mark.integration


def _ollama_up(host):
    try:
        return httpx.get(f"{host}/api/tags", timeout=2).status_code == 200
    except Exception:
        return False


def _qdrant_up(url):
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except Exception:
        return False


def test_real_rag_end_to_end():
    s = get_settings()
    if not _ollama_up(s.ollama_host):
        pytest.skip("ollama not running")
    if not _qdrant_up(s.qdrant_url):
        pytest.skip("qdrant not running")
    emb = OllamaEmbedder(s.ollama_host, s.embed_model)
    store = QdrantStore(QdrantClient(url=s.qdrant_url), s.qdrant_collection)
    ingest(load_knowledge_base("xlsx/knowledge_base.xlsx"), store, emb)
    r = Retriever(store, emb, s.confidence_threshold)
    in_scope = r.retrieve("how can I change my password?")
    out_scope = r.retrieve("who won the 1998 world cup?")
    assert in_scope.abstain is False
    assert out_scope.abstain is True
