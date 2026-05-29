"""Live Redis round-trip for the semantic cache (skips if Redis is down)."""
import pytest

from tiered_rag.config import get_settings

pytestmark = pytest.mark.integration


def _redis_up(url: str) -> bool:
    try:
        import redis
        redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2).ping()
        return True
    except Exception:
        return False


def test_redis_cache_round_trip_live():
    s = get_settings()
    if not _redis_up(s.redis_url):
        pytest.skip("redis not running")
    import redis

    from tiered_rag.cache import RedisCacheBackend, SemanticCache
    from tiered_rag.embeddings import FakeEmbedder

    client = redis.Redis.from_url(s.redis_url, decode_responses=True)
    backend = RedisCacheBackend(client, prefix="tiered_rag:itest", ttl=30, max_entries=8)
    cache = SemanticCache(FakeEmbedder(dim=64), backend, threshold=0.95)

    cache.put("how do I reset my password", {"answer": "Open Settings > Security > Reset.", "tier": 1})
    hit = cache.get("how do I reset my password")
    assert hit is not None and hit["answer"].startswith("Open Settings")

    # clean up the keys this test created
    for key in client.keys("tiered_rag:itest:*"):
        client.delete(key)
