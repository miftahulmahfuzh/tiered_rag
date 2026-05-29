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

    def fake_post(url, json=None, timeout=None):
        calls["url"], calls["json"] = url, json
        return _Resp()

    monkeypatch.setattr("tiered_rag.telegram.httpx.post", fake_post)
    c = TelegramClient("123:ABC", "https://api.telegram.org", timeout=5.0)
    out = c.send_message(42, "hello")
    assert calls["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert calls["json"] == {"chat_id": 42, "text": "hello"}
    assert out["ok"] is True
