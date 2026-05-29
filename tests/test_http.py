import httpx
import pytest

from tiered_rag.http import post_with_retry, shared_client


def test_shared_client_pools_by_key_and_timeout():
    a = shared_client("k", 5.0)
    b = shared_client("k", 5.0)
    c = shared_client("k", 9.0)
    assert isinstance(a, httpx.Client)
    assert a is b              # same (key, timeout) -> reused, so DNS/conns are pooled
    assert a is not c          # different timeout -> distinct client


def test_post_with_retry_retries_transient_then_succeeds(monkeypatch):
    calls = {"n": 0}
    cli = shared_client("retry-helper", 5.0)

    def fake_post(url, json=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return "OK"

    monkeypatch.setattr(cli, "post", fake_post)
    assert post_with_retry(cli, "http://x", max_retries=3, retry_backoff=0.0) == "OK"
    assert calls["n"] == 3


def test_post_with_retry_does_not_retry_status_errors(monkeypatch):
    calls = {"n": 0}
    cli = shared_client("retry-helper2", 5.0)

    def fake_post(url, json=None):
        calls["n"] += 1
        raise httpx.HTTPStatusError("500", request=None, response=None)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        post_with_retry(cli, "http://x", max_retries=3, retry_backoff=0.0)
    assert calls["n"] == 1
