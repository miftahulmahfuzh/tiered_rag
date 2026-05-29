from tiered_rag.embeddings import FakeEmbedder, OllamaEmbedder


def test_fake_embedder_is_deterministic_and_right_dim():
    e = FakeEmbedder(dim=768)
    v1 = e.embed_query("hello")
    v2 = e.embed_query("hello")
    assert len(v1) == 768 and v1 == v2
    assert e.embed_query("hello") != e.embed_query("world")


def test_fake_embed_documents_batches():
    e = FakeEmbedder(dim=8)
    vecs = e.embed_documents(["a", "b", "c"])
    assert len(vecs) == 3 and all(len(v) == 8 for v in vecs)


def test_ollama_embedder_retries_transient_dns_error(monkeypatch):
    """RAG retrieval must survive a transient DNS blip to host.docker.internal."""
    import httpx
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"embeddings": [[0.1, 0.2]]}

    def fake_post(url, json=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return _Resp()

    emb = OllamaEmbedder("http://host:11434", "nomic", timeout=5.0,
                         max_retries=3, retry_backoff=0.0)
    monkeypatch.setattr(emb._client, "post", fake_post)
    assert emb.embed_query("hello") == [0.1, 0.2]
    assert calls["n"] == 3
