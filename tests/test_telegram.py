from tiered_rag.telegram import TelegramClient, extract_message


def test_extract_message_pulls_chat_id_and_text():
    update = {"update_id": 1, "message": {"chat": {"id": 42}, "text": "hi there"}}
    assert extract_message(update) == (42, "hi there")


def test_extract_message_ignores_non_text_and_malformed():
    assert extract_message({"update_id": 2}) is None                       # no message
    assert extract_message({"message": {"chat": {"id": 1}}}) is None         # no text
    assert extract_message({"edited_message": {"chat": {"id": 1}, "text": "x"}}) is None
    assert extract_message({}) is None


def test_client_builds_method_urls_and_posts(monkeypatch):
    calls = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"ok": True, "result": {"message_id": 7}}

    def fake_post(url, json=None):
        calls["url"], calls["json"] = url, json
        return _Resp()

    c = TelegramClient("123:ABC", "https://api.telegram.org", timeout=5.0)
    monkeypatch.setattr(c._client, "post", fake_post)
    out = c.send_message(42, "hello")
    assert calls["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert calls["json"] == {"chat_id": 42, "text": "hello"}
    assert out["ok"] is True


def test_send_message_retries_transient_dns_error(monkeypatch):
    """Delivery to Telegram must survive a transient WSL2 DNS blip, not crash the
    background task (the SKU-06 failure: answer generated, delivery died)."""
    import httpx
    calls = {"n": 0}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}

    def fake_post(url, json=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return _Resp()

    c = TelegramClient("999:RETRY", timeout=5.0, max_retries=3, retry_backoff=0.0)
    monkeypatch.setattr(c._client, "post", fake_post)
    assert c.send_message(42, "hi")["ok"] is True
    assert calls["n"] == 3
