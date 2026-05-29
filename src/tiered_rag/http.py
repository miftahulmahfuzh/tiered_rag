from __future__ import annotations

import atexit
import time

import httpx

# One pooled keep-alive httpx.Client per (key, timeout), shared process-wide.
# Callers (build_llm, TelegramClient, OllamaEmbedder) are constructed often — per
# request in the gateway — so without pooling every outbound call would re-resolve
# DNS and reopen a connection, multiplying exposure to the intermittent WSL2 DNS
# outages. A shared client resolves once and pools connections; httpx.Client is
# thread-safe for the threadpool endpoints.
_CLIENT_POOL: dict[tuple[str, float], httpx.Client] = {}


def shared_client(key: str, timeout: float, headers: dict | None = None) -> httpx.Client:
    pkey = (key, timeout)
    cli = _CLIENT_POOL.get(pkey)
    if cli is None:
        cli = httpx.Client(timeout=timeout, headers=headers or {})
        _CLIENT_POOL[pkey] = cli
    return cli


def post_with_retry(client: httpx.Client, url: str, *, json: dict | None = None,
                    max_retries: int = 4, retry_backoff: float = 0.5) -> httpx.Response:
    """POST retrying transient transport errors (DNS blips, timeouts) with
    exponential backoff. The default window (4 retries ≈ 7.5s) rides out the
    multi-second WSL2 DNS outages. Only ``httpx.TransportError`` is retried — HTTP
    status errors are not transient and propagate immediately.
    """
    for attempt in range(max_retries + 1):
        try:
            return client.post(url, json=json)
        except httpx.TransportError:
            if attempt == max_retries:
                raise
            if retry_backoff:
                time.sleep(retry_backoff * (2 ** attempt))
    raise RuntimeError("unreachable")


@atexit.register
def _close_pool() -> None:
    for cli in _CLIENT_POOL.values():
        cli.close()
