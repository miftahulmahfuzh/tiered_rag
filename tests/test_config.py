from tiered_rag.config import Settings


def test_defaults():
    s = Settings()
    assert s.embed_model == "nomic-embed-text:v1.5"
    assert s.embed_dim == 768
    assert s.qdrant_collection == "knowledge_base"
    assert s.confidence_threshold == 0.6
    assert s.ollama_host.startswith("http")


def test_env_override(monkeypatch):
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.8")
    assert Settings().confidence_threshold == 0.8
