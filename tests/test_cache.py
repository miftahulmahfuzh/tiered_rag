from tiered_rag.cache import InMemoryCacheBackend, SemanticCache, cacheable
from tiered_rag.embeddings import FakeEmbedder
from tiered_rag.orchestrator import ExecutionResult
from tiered_rag.alerting import GapAlert


def _cache(threshold=0.95, max_entries=512):
    return SemanticCache(FakeEmbedder(dim=64), InMemoryCacheBackend(max_entries), threshold)


def test_exact_repeat_query_is_a_hit():
    c = _cache()
    assert c.get("how do I reset my password") is None          # cold miss
    c.put("how do I reset my password", {"answer": "Open Settings > Security > Reset.", "tier": 1})
    hit = c.get("how do I reset my password")
    assert hit is not None and hit["answer"].startswith("Open Settings")


def test_unrelated_query_is_a_miss():
    c = _cache()
    c.put("how do I reset my password", {"answer": "x", "tier": 1})
    # FakeEmbedder: different strings -> different vectors -> cosine below the 0.95 bar
    assert c.get("what is the capital of France") is None


def test_threshold_controls_hit_strictness():
    loose = _cache(threshold=-1.0)                              # everything clears the bar
    loose.put("anything", {"answer": "a", "tier": 1})
    assert loose.get("totally different") is not None


def test_cache_respects_max_entries_cap():
    c = _cache(max_entries=2)
    for i in range(5):
        c.put(f"q{i}", {"answer": f"a{i}", "tier": 1})
    assert len(c.backend.scan()) == 2                           # bounded ring buffer


def test_cacheable_excludes_abstain_and_escalation():
    assert cacheable(ExecutionResult(tier=1, answer="ok"))               # served answer
    assert not cacheable(ExecutionResult(tier=1, answer="idk", abstained=True))
    esc = ExecutionResult(tier=2, answer="pending", gap=GapAlert(kind="unverified", query="q", answer="a"))
    assert not cacheable(esc)


class FakeRedis:
    """Minimal in-process double of the redis ops RedisCacheBackend uses."""
    def __init__(self):
        self.store: dict[str, dict] = {}

    def hset(self, key, mapping):
        self.store.setdefault(key, {}).update(mapping)

    def expire(self, key, ttl):  # TTL behaviour is not asserted offline
        return True

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def hgetall(self, key):
        return self.store.get(key, {})


def test_redis_backend_round_trips_an_entry():
    from tiered_rag.cache import RedisCacheBackend, SemanticCache
    from tiered_rag.embeddings import FakeEmbedder
    backend = RedisCacheBackend(FakeRedis(), prefix="t:cache", ttl=60, max_entries=4)
    c = SemanticCache(FakeEmbedder(dim=64), backend, threshold=0.95)
    c.put("reset my password", {"answer": "Open Settings > Security > Reset.", "tier": 1})
    assert c.get("reset my password")["answer"].startswith("Open Settings")


def test_redis_backend_bounds_entries_by_modulo():
    from tiered_rag.cache import RedisCacheBackend
    backend = RedisCacheBackend(FakeRedis(), prefix="t:cache", ttl=60, max_entries=2)
    for i in range(5):
        backend.add([float(i)] * 4, {"answer": f"a{i}"})
    assert len(backend.scan()) == 2                            # rolls over mod max_entries
