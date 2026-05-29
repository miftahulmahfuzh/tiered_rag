# Shared pytest fixtures (added per-task as the suite grows).
import pytest

from tiered_rag.embeddings import FakeEmbedder


@pytest.fixture
def fake_embedder():
    return FakeEmbedder(dim=64)  # small dim -> fast tests
