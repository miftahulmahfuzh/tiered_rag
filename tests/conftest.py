# Shared pytest fixtures (added per-task as the suite grows).
import pytest

from tiered_rag.embeddings import FakeEmbedder


@pytest.fixture
def fake_embedder():
    return FakeEmbedder(dim=64)  # small dim -> fast tests


@pytest.fixture
def client_with_inmemory_cache(fake_embedder):
    """TestClient with an in-memory semantic cache + counting-spy orchestrator (served tier-1 faq)."""
    from tests._helpers import build_cached_client
    return build_cached_client(fake_embedder)
