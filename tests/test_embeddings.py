from tiered_rag.embeddings import FakeEmbedder


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
